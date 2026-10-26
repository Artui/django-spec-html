"""``SpecFormView``: a spec validated by a form, served as the page that form is.

Assertions read ``response.context_data["form"].errors`` - the bound form the
page re-renders - rather than parsing HTML, and each refusal test says which
check answered: the Validator records every call, so a test naming the shape
check asserts the Validator never ran, and one naming the form asserts it did.
The module is its own URLconf, since ``resolve_url`` reads the project's.
"""

from __future__ import annotations

import dataclasses
import datetime
import re
import tempfile
from collections.abc import Callable, Iterator, Mapping
from decimal import Decimal
from typing import Annotated, Any

import django
import pytest
from django import forms
from django.contrib.auth import get_user_model, login
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import (
    BadRequest,
    DisallowedRedirect,
    ImproperlyConfigured,
    PermissionDenied,
)
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.validators import MaxValueValidator
from django.db import connection
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.template import TemplateDoesNotExist
from django.test.utils import CaptureQueriesContext
from django.urls import path, reverse_lazy
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    ActionUnavailable,
    AdditionalInputRequired,
    Affordance,
    DataclassPresenter,
    DispatchError,
    DispatchResult,
    FieldMarking,
    FormValidator,
    NotPermitted,
    Parameter,
    Parameters,
    PermissionCheck,
    PoolSeeds,
    PrincipalUnavailable,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceConflict,
    ServiceError,
    ServiceNotFound,
    ServiceSpec,
    ServiceValidationError,
    UnknownArguments,
    ValidationContext,
    update_from_input,
)

from django_spec_html.http.unsupported_media_type import UnsupportedMediaType
from django_spec_html.views import spec_form_view
from django_spec_html.views.spec_form_view import SpecFormView
from tests.adapter_app.models import Author as BookAuthor
from tests.adapter_app.models import Book, Document, Edition, Status
from tests.dispatch.utils import (
    OPEN,
    PK,
    TENANT_SEEDS,
    OwnerOnly,
    Record,
    Refuse,
    Titled,
    make_user,
    note_by_pk,
)
from tests.dispatch_app.models import Note
from tests.http.utils import FACTORY, SeesTenant, note_spec, signed_in

pytestmark = pytest.mark.django_db

urlpatterns = [path("done/", lambda request: HttpResponse(), name="done")]

# Where ``stored_document`` puts its optional file, under the field's ``upload_to``.
STORED = "documents/notes.txt"


def stored_document(*, attachment: bool = True) -> Document:
    """A saved row holding its required file and, unless told not to, its optional one."""
    row = Document(title="Notes")
    if attachment:
        row.attachment.save("notes.txt", ContentFile(b"notes"), save=False)
    row.scan.save("scan.txt", ContentFile(b"scan"), save=False)
    row.save()
    return row


TEMPLATE = "spec_form.html"
REQUIRED = "This field is required."
CLOSED = "Entries are closed."
WHOLE = "Enter a whole number."
UNDECLARED = (
    "The route captures {names}, which the spec does not declare. Every URL kwarg is "
    "an argument: declare each one as a parameter, or leave it out of url_kwargs."
)


class EntryForm(forms.Form):
    title = forms.CharField(max_length=5)
    urgent = forms.BooleanField(required=False)
    tags = forms.MultipleChoiceField(choices=[("home", "Home"), ("work", "Work")], required=False)
    count = forms.IntegerField(required=False)
    # No blank option among the choices: a form reads an empty value before
    # it looks at them, so an optional one left blank is valid.
    colour = forms.ChoiceField(choices=[("red", "Red"), ("blue", "Blue")], required=False)
    due = forms.DateField(required=False)
    at = forms.DateTimeField(required=False)
    price = forms.DecimalField(required=False, localize=True)
    ratio = forms.FloatField(required=False, localize=True)
    code = forms.CharField(disabled=True, required=False, initial="E-1")

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if cleaned.get("title") == "shut":
            raise forms.ValidationError(CLOSED)
        return cleaned


class WithAnExtra(EntryForm):
    """Adds a field in ``__init__``, which is not in ``base_fields`` and so is never declared."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["extra"] = forms.CharField(required=False)


class Held:
    """The file a page shows as one the row holds: a name and a url, as a ``FieldFile`` has."""

    name = "covers/old.txt"
    url = "/media/covers/old.txt"

    def __str__(self) -> str:
        return self.name


class CoverForm(forms.Form):
    """An optional file shown beside the one held, so its widget renders the clear checkbox."""

    title = forms.CharField()
    cover = forms.FileField(required=False, initial=Held())


class Watched(FormValidator):
    """A FormValidator that records each call, so a test can tell who answered."""

    def __init__(self, form_class: type[forms.BaseForm] = EntryForm) -> None:
        super().__init__(form_class)
        self.calls: list[dict[str, Any]] = []

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        self.calls.append(dict(arguments))
        return super().validate(arguments, context)


class DocumentForm(forms.ModelForm):
    """An update of a row holding an optional file, which a ``ClearableFileInput`` can clear."""

    class Meta:
        model = Document
        fields = ["title", "attachment"]


class StampingForm(forms.ModelForm):
    """Writes onto its instance as it is built, as a form filling in a default does."""

    class Meta:
        model = Document
        fields = ["title"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.instance.title = "Stamped"


class NoteTitleForm(forms.Form):
    """A plain form naming one of a note's fields, and one no note has."""

    title = forms.CharField()
    remark = forms.CharField(required=False, initial="none")


RED_CLOSED = "Red is closed."


class RedIsClosed(forms.Form):
    """A required title, and a ``clean()`` rule over an optional colour."""

    title = forms.CharField()
    colour = forms.ChoiceField(choices=[("red", "Red"), ("blue", "Blue")], required=False)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if cleaned.get("colour") == "red":
            raise forms.ValidationError(RED_CLOSED)
        return cleaned


class SignInForm(forms.ModelForm):
    """A user's unique username, beside a date-time the shape check reads."""

    class Meta:
        model = get_user_model()
        fields = ["username", "last_login"]


class Counting(forms.Form):
    """Counts each ``clean()``, on the class, across every instance a post builds."""

    cleans = 0
    title = forms.CharField()

    def clean(self) -> dict[str, Any]:
        type(self).cleans += 1
        return super().clean() or {}


class NoCreating(PermissionCheck):
    """Admits every principal at class level, then refuses a call acting on no row."""

    message = "Nothing may be created here."

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return True

    def has_scope_permission(self, principal: Any, spec: Any, scope: Any) -> bool:
        return scope is not None


class Deactivating(PermissionCheck):
    """Admits the principal at class level, then deactivates it, as a concurrent request might."""

    def has_permission(self, principal: Any, spec: Any) -> bool:
        principal.is_active = False
        return True


class DisabledTitleForm(forms.Form):
    """A plain form whose ``title`` is shown and never read."""

    title = forms.CharField(disabled=True, initial="Fixed")


class Throttled(DispatchError):
    """A dispatch refusal no row of the status map names, as a later release might add."""


class UserForm(forms.ModelForm):
    """An update of a row with a unique field, as a profile page is."""

    class Meta:
        model = get_user_model()
        fields = ["username", "first_name"]


class AssignForm(forms.Form):
    """A form whose own validation reads rows: a choice among the users."""

    title = forms.CharField()
    assignee = forms.ModelChoiceField(queryset=get_user_model().objects.all(), required=False)


class Listed(forms.Form):
    """Builds its choices in its constructor, as a form offering the rows of the moment does."""

    title = forms.CharField()
    owner = forms.ChoiceField(required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["owner"].choices = [
            (user.pk, user.username) for user in get_user_model().objects.all()
        ]


class EditionForm(forms.ModelForm):
    """A row whose integer column a constraint holds unique, so validating it queries."""

    class Meta:
        model = Edition
        fields = ["book", "number"]


class AssigneesForm(forms.Form):
    """A form choosing several rows, which Django matches with one ``in`` lookup."""

    title = forms.CharField()
    assignees = forms.ModelMultipleChoiceField(queryset=get_user_model().objects.all())


class Counted(PermissionCheck):
    """Admits every principal, counting each class-level check."""

    def __init__(self) -> None:
        self.calls = 0

    def has_permission(self, principal: Any, spec: Any) -> bool:
        self.calls += 1
        return True


def user_by_pk(pk: int, **pool: Any) -> Any:
    return get_user_model().objects.filter(pk=pk).first()


def entry_spec(returns: Any = None, **kwargs: Any) -> ServiceSpec:
    """A write validated by ``EntryForm``, whose service records what it received."""
    kwargs.setdefault("permissions", OPEN)
    kwargs.setdefault("validator", Watched())
    return ServiceSpec(service=Record(returns), **kwargs)


def row_spec(**kwargs: Any) -> ServiceSpec:
    """``entry_spec`` on one note, read from the route's ``pk``."""
    return entry_spec(
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=note_by_pk, reads=PK
        ),
        **kwargs,
    )


def user_spec(service: Any) -> ServiceSpec:
    """An update of the user the route names, validated by ``UserForm``."""
    return ServiceSpec(
        service=service,
        permissions=OPEN,
        validator=FormValidator(UserForm),
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=user_by_pk, reads=PK
        ),
    )


def document_by_pk(*, pk: int) -> Any:
    return Document.objects.filter(pk=pk)


def document_spec(**kwargs: Any) -> ServiceSpec:
    """An update of the document the route names, validated by ``DocumentForm``."""
    kwargs.setdefault("permissions", OPEN)
    return ServiceSpec(
        service=Record(),
        validator=FormValidator(DocumentForm),
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=document_by_pk, reads=PK
        ),
        **kwargs,
    )


def raising(exc: Exception) -> ServiceSpec:
    def service(**pool: Any) -> None:
        raise exc

    return ServiceSpec(service=service, permissions=OPEN, validator=Watched())


def form_view(spec: ServiceSpec, **initkwargs: Any) -> Any:
    initkwargs.setdefault("template_name", TEMPLATE)
    initkwargs.setdefault("success_url", "/done/")
    return SpecFormView.as_view(spec=spec, **initkwargs)


def get(view: Any, user: Any, **url_kwargs: Any) -> Any:
    return view(signed_in(FACTORY.get("/"), user), **url_kwargs)


def post(view: Any, user: Any, data: Mapping[str, Any], **url_kwargs: Any) -> Any:
    return view(signed_in(FACTORY.post("/", data=data), user), **url_kwargs)


def received(spec: ServiceSpec) -> dict[str, Any]:
    """The validated values the service was called with, once."""
    calls = spec.service.calls
    assert len(calls) == 1
    return calls[0]["data"]


def refused(response: Any) -> dict[str, list[str]]:
    """The re-rendered form's errors, as a person reads them."""
    form = response.context_data["form"]
    assert form.is_bound
    return {key: list(messages) for key, messages in form.errors.items()}


def validated(spec: ServiceSpec) -> int:
    """How many times the Validator ran: none when the shape check answered."""
    return len(spec.validator.calls)


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    # The suite's settings name no URLconf, which pytest-django's ``urls``
    # marker needs one of to restore; the ``settings`` fixture sets it and
    # removes it again.
    settings.ROOT_URLCONF = __name__


@pytest.fixture
def ada() -> Any:
    return make_user("ada")


@pytest.fixture
def media(settings: Any) -> Iterator[str]:
    """Default storage in a directory removed after the test, so no upload outlives it."""
    with tempfile.TemporaryDirectory() as root:
        settings.MEDIA_ROOT = root
        settings.MEDIA_URL = "/media/"
        yield root


class TestGet:
    def test_renders_an_unbound_form_with_the_spec_and_the_view(self, ada: Any) -> None:
        spec = entry_spec()
        response = get(form_view(spec), ada)
        assert response.status_code == 200
        assert response.template_name == [TEMPLATE]
        context = response.context_data
        assert isinstance(context["form"], EntryForm)
        assert not context["form"].is_bound
        assert context["spec"] is spec
        assert isinstance(context["view"], SpecFormView)
        assert spec.service.calls == []

    def test_the_page_renders_the_form(self, ada: Any) -> None:
        response = get(form_view(entry_spec()), ada).render()
        assert b'name="title"' in response.content
        assert b'name="csrfmiddlewaretoken"' in response.content

    def test_head_is_answered_as_get(self, ada: Any) -> None:
        response = form_view(entry_spec())(signed_in(FACTORY.head("/"), ada))
        assert response.status_code == 200

    def test_extra_context_reaches_the_template(self, ada: Any) -> None:
        response = get(form_view(entry_spec(), extra_context={"heading": "New entry"}), ada)
        assert response.context_data["heading"] == "New entry"

    def test_a_principal_the_class_check_refuses_is_never_offered_the_page(self, ada: Any) -> None:
        with pytest.raises(PermissionDenied, match=r"^Only editors may run this\.$"):
            get(form_view(entry_spec(permissions=[Refuse()])), ada)

    def test_a_deactivated_principal_is_never_offered_the_page(self, ada: Any) -> None:
        ada.is_active = False
        with pytest.raises(PermissionDenied, match=r"^The acting principal is unavailable\.$"):
            get(form_view(entry_spec()), ada)


class TestAnUpdatePage:
    """GET finds the row the post would act on, and the form shows it."""

    def test_shows_the_row_with_its_file_and_the_clear_checkbox(self, ada: Any, media: str) -> None:
        row = stored_document()
        # A string, as a route's ``<str:pk>`` captures it: coerced as a post's is.
        response = get(form_view(document_spec()), ada, pk=str(row.pk)).render()
        assert response.status_code == 200
        form = response.context_data["form"]
        assert not form.is_bound
        assert form["title"].value() == "Notes"
        assert form["attachment"].value() == STORED
        # Built on a copy of the row, as the Validator binds its ModelForm.
        assert form.instance.pk == row.pk
        assert form.instance is not row
        assert b'type="checkbox" name="attachment-clear"' in response.content
        assert f'href="/media/{STORED}"'.encode() in response.content

    def test_the_rendered_checkbox_ticked_clears_the_held_file(self, ada: Any, media: str) -> None:
        row = stored_document()
        spec = document_spec()
        page = get(form_view(spec), ada, pk=row.pk).render()
        assert b'name="attachment-clear"' in page.content
        response = post(
            form_view(spec), ada, {"title": "Notes", "attachment-clear": "on"}, pk=row.pk
        )
        assert response.status_code == 302
        assert received(spec)["attachment"] is False

    def test_a_plain_form_shows_the_row_s_values_for_its_own_fields(self, ada: Any) -> None:
        note = Note.objects.create(owner=ada, title="Ada's")
        spec = row_spec(validator=Watched(NoteTitleForm))
        form = get(form_view(spec), ada, pk=note.pk).context_data["form"]
        assert form.initial == {"title": "Ada's"}
        assert form["title"].value() == "Ada's"
        # A field no note has keeps its own initial.
        assert form["remark"].value() == "none"

    def test_a_disabled_field_shows_its_own_initial(self, ada: Any) -> None:
        # The post cleans it from that initial and never reads it, so the
        # row's value is not what the page offers.
        note = Note.objects.create(owner=ada, title="Ada's")
        spec = row_spec(validator=Watched(DisabledTitleForm))
        form = get(form_view(spec), ada, pk=note.pk).context_data["form"]
        assert form["title"].value() == "Fixed"

    def test_a_model_form_of_another_model_is_built_as_a_create(self, ada: Any) -> None:
        # The Validator binds a ModelForm to the target only where the target
        # is a row of its model, so the page does too.
        note = Note.objects.create(owner=ada, title="Ada's")
        spec = row_spec(validator=FormValidator(DocumentForm))
        form = get(form_view(spec), ada, pk=note.pk).context_data["form"]
        assert isinstance(form.instance, Document)
        assert form.instance.pk is None
        assert form["title"].value() == "Ada's"

    def test_a_collection_target_shows_an_empty_form(self, ada: Any) -> None:
        Note.objects.create(owner=ada, title="Ada's")
        spec = entry_spec(
            collection_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST, selector=lambda: Note.objects.all()
            )
        )
        form = get(form_view(spec), ada).context_data["form"]
        assert form.initial == {}
        assert form["title"].value() is None

    def test_a_missing_row_allow_none_lets_through_shows_an_empty_form(self, ada: Any) -> None:
        spec = entry_spec(
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=note_by_pk, reads=PK, allow_none=True
            )
        )
        response = get(form_view(spec), ada, pk=404)
        assert response.status_code == 200
        assert response.context_data["form"].initial == {}

    def test_a_row_the_object_check_refuses_is_never_offered(self, ada: Any) -> None:
        note = Note.objects.create(owner=make_user("bob"), title="Bob's")
        spec = row_spec(permissions=[OwnerOnly()])
        with pytest.raises(PermissionDenied, match=r"^Only the owner may touch this note\.$"):
            get(form_view(spec), ada, pk=note.pk)

    def test_a_create_the_scope_check_refuses_is_never_offered(self, ada: Any) -> None:
        # A create resolves ``None``, which dispatch's scope check is asked
        # about, so the page is refused where its post would be.
        spec = entry_spec(permissions=[NoCreating()])
        with pytest.raises(PermissionDenied, match=r"^Nothing may be created here\.$"):
            get(form_view(spec), ada)
        with pytest.raises(PermissionDenied, match=r"^Nothing may be created here\.$"):
            post(form_view(spec), ada, {"title": "Hi"})

    def test_a_principal_deactivated_once_admitted_is_refused_at_the_row(self, ada: Any) -> None:
        # The target's lookup refuses a deactivated principal on its own, so
        # one admitted at class level and deactivated before the row is found
        # is the host's 403, not an unhandled refusal.
        note = Note.objects.create(owner=ada, title="Ada's")
        with pytest.raises(PermissionDenied, match=r"^The acting principal is unavailable\.$"):
            get(form_view(row_spec(permissions=[Deactivating()])), ada, pk=note.pk)

    def test_a_missing_row_is_http404(self, ada: Any) -> None:
        with pytest.raises(Http404):
            get(form_view(row_spec()), ada, pk=404)

    def test_a_selector_s_service_not_found_is_http404(self, ada: Any) -> None:
        def missing(*, pk: int) -> Any:
            raise ServiceNotFound("No such note.")

        spec = entry_spec(
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=missing, reads=PK
            )
        )
        with pytest.raises(Http404, match=r"^No such note\.$"):
            get(form_view(spec), ada, pk=1)

    def test_a_route_value_the_target_refuses_is_a_bad_request(self, ada: Any) -> None:
        # Its text is the refusal's tree, printed as a tree of plain strings:
        # a message's code is for a program, never part of the words.
        with pytest.raises(BadRequest) as caught:
            get(form_view(row_spec()), ada, pk="first")
        assert str(caught.value) == "{'pk': ['Enter a whole number.']}"

    def test_the_class_level_check_runs_once(self, ada: Any) -> None:
        # The grant the page's own check returned goes to the lookup.
        note = Note.objects.create(owner=ada, title="Ada's")
        counted = Counted()
        get(form_view(row_spec(permissions=[counted])), ada, pk=note.pk)
        assert counted.calls == 1

    def test_a_page_found_by_its_route_runs_nothing(self, ada: Any) -> None:
        note = Note.objects.create(owner=ada, title="Ada's")
        spec = row_spec()
        assert get(form_view(spec), ada, pk=note.pk).status_code == 200
        assert spec.service.calls == []
        assert validated(spec) == 0


class TestAnUpdatePost:
    """A post finds its target as the page does, before the form is built or read."""

    def test_a_refused_post_shows_the_held_file_and_its_clear_checkbox(
        self, ada: Any, media: str
    ) -> None:
        row = stored_document()
        response = post(form_view(document_spec()), ada, {"title": ""}, pk=str(row.pk))
        assert response.status_code == 400
        page = response.render()
        form = page.context_data["form"]
        assert form.is_bound
        assert refused(page) == {"title": [REQUIRED]}
        # Built on a copy of the row, as the page's GET builds it.
        assert (form.instance.pk, form.instance is row) == (row.pk, False)
        assert b'type="checkbox" name="attachment-clear"' in page.content
        assert f'href="/media/{STORED}"'.encode() in page.content

    def test_a_refused_post_on_a_plain_form_keeps_the_row_s_values_as_its_initial(
        self, ada: Any
    ) -> None:
        note = Note.objects.create(owner=ada, title="Ada's")
        spec = row_spec(validator=Watched(NoteTitleForm))
        response = post(form_view(spec), ada, {"title": ""}, pk=note.pk)
        assert response.status_code == 400
        assert response.context_data["form"].initial == {"title": "Ada's"}

    def test_the_object_check_refuses_before_a_malformed_post_is_read(self, ada: Any) -> None:
        # The row is found and checked first, so a principal the row refuses
        # is answered 403 rather than shown the page with the post's errors.
        note = Note.objects.create(owner=make_user("bob"), title="Bob's")
        spec = row_spec(permissions=[OwnerOnly()])
        with pytest.raises(PermissionDenied, match=r"^Only the owner may touch this note\.$"):
            post(form_view(spec), ada, {"title": "Hi", "count": "x"}, pk=note.pk)
        assert validated(spec) == 0

    def test_a_missing_row_is_http404_before_a_malformed_post_is_read(self, ada: Any) -> None:
        spec = row_spec()
        with pytest.raises(Http404):
            post(form_view(spec), ada, {"count": "x"}, pk=404)
        assert validated(spec) == 0

    def test_the_row_found_is_the_row_the_service_receives_checked_once(self, ada: Any) -> None:
        note = Note.objects.create(owner=ada, title="Ada's")
        owner_only = OwnerOnly()
        spec = row_spec(permissions=[owner_only])
        assert post(form_view(spec), ada, {"title": "Hi"}, pk=note.pk).status_code == 302
        assert spec.service.calls[0]["instance"] == note
        # Asked once, by the lookup in front of dispatch, which the grant tells.
        assert owner_only.rows == [note]

    def test_the_page_form_s_writes_never_reach_the_row_dispatch_is_handed(
        self, ada: Any, media: str
    ) -> None:
        # The page's form is built before dispatch, and the row it shows is
        # the one dispatch is handed as its target: built on that row itself,
        # what the form's constructor writes onto its instance would reach the
        # service as the row's stored values.
        row = stored_document()
        spec = ServiceSpec(
            service=Record(),
            permissions=OPEN,
            validator=FormValidator(StampingForm),
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=document_by_pk, reads=PK
            ),
        )
        assert post(form_view(spec), ada, {"title": "Renamed"}, pk=str(row.pk)).status_code == 302
        call = spec.service.calls[0]
        assert (call["instance"].pk, call["instance"].title) == (row.pk, "Notes")
        assert call["data"] == {"title": "Renamed"}

    def test_a_create_and_a_collection_post_as_before(self, ada: Any) -> None:
        created = entry_spec()
        assert post(form_view(created), ada, {"title": "Hi"}).status_code == 302
        assert "instance" not in created.service.calls[0]
        note = Note.objects.create(owner=ada, title="Ada's")
        listed = entry_spec(
            collection_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST, selector=lambda: Note.objects.all()
            )
        )
        assert post(form_view(listed), ada, {"title": "Hi"}).status_code == 302
        assert list(listed.service.calls[0]["collection"]) == [note]

    def test_a_create_the_scope_check_refuses_is_refused_before_the_post_is_read(
        self, ada: Any
    ) -> None:
        spec = entry_spec(permissions=[NoCreating()])
        with pytest.raises(PermissionDenied, match=r"^Nothing may be created here\.$"):
            post(form_view(spec), ada, {"count": "x"})
        assert validated(spec) == 0


class TestSuccess:
    def test_a_valid_post_redirects_to_the_success_url(self, ada: Any) -> None:
        spec = entry_spec()
        response = post(form_view(spec), ada, {"title": "Hi"})
        assert (response.status_code, response.url) == (302, "/done/")
        assert received(spec)["title"] == "Hi"

    # ``resolve_url`` reads either as ``redirect()`` does.
    @pytest.mark.parametrize(
        "success_url", [reverse_lazy("done"), "done"], ids=["lazy", "url-name"]
    )
    def test_a_success_url_is_resolved(self, ada: Any, success_url: Any) -> None:
        view = form_view(entry_spec(), success_url=success_url)
        assert post(view, ada, {"title": "Hi"}).url == "/done/"

    def test_an_overridden_get_success_url_reads_the_result(self, ada: Any) -> None:
        class ToTheEntry(SpecFormView):
            template_name = TEMPLATE

            def get_success_url(self, result: DispatchResult) -> str:
                return f"/entries/{result.value}/"

        view = ToTheEntry.as_view(spec=entry_spec(returns=7))
        assert post(view, ada, {"title": "Hi"}).url == "/entries/7/"

    def test_get_success_url_may_be_passed_to_as_view(self, ada: Any) -> None:
        view = SpecFormView.as_view(
            spec=entry_spec(returns=7),
            template_name=TEMPLATE,
            get_success_url=lambda result: f"/entries/{result.value}/",
        )
        assert post(view, ada, {"title": "Hi"}).url == "/entries/7/"


class TestReadingThroughTheWidgets:
    def test_a_checked_box_is_true_and_an_unchecked_one_false(self, ada: Any) -> None:
        checked, unchecked = entry_spec(), entry_spec()
        post(form_view(checked), ada, {"title": "Hi", "urgent": "on"})
        post(form_view(unchecked), ada, {"title": "Hi"})
        assert received(checked)["urgent"] is True
        assert received(unchecked)["urgent"] is False

    def test_a_multi_select_is_a_list(self, ada: Any) -> None:
        spec = entry_spec()
        post(form_view(spec), ada, {"title": "Hi", "tags": ["home", "work"]})
        assert received(spec)["tags"] == ["home", "work"]

    @pytest.mark.parametrize(
        ("name", "cleaned"),
        # Each read as ``""`` would be refused by the shape check where the
        # form takes it: not a whole number, not a date, not one of the choices.
        [("count", None), ("due", None), ("colour", "")],
    )
    def test_a_blank_optional_field_is_absent(self, ada: Any, name: str, cleaned: Any) -> None:
        # Holds the blank half of the absence guard.
        spec = entry_spec()
        post(form_view(spec), ada, {"title": "Hi", name: ""})
        assert name not in spec.validator.calls[0]
        assert received(spec)[name] == cleaned

    def test_a_required_field_never_posted_is_refused_once(self, ada: Any) -> None:
        # Holds the ``None`` half of the absence guard: read as ``None``, the
        # shape check would say "cannot be null" beside the form's "required".
        spec = entry_spec()
        response = post(form_view(spec), ada, {})
        assert (response.status_code, refused(response)) == (400, {"title": [REQUIRED]})
        assert validated(spec) == 0

    def test_a_required_field_left_blank_is_refused_once(self, ada: Any) -> None:
        spec = entry_spec()
        response = post(form_view(spec), ada, {"title": ""})
        assert (response.status_code, refused(response)) == (400, {"title": [REQUIRED]})
        assert validated(spec) == 0

    def test_dates_in_the_forms_input_formats_are_read_as_the_form_reads_them(
        self, ada: Any
    ) -> None:
        # Neither is ISO, and the shape check parses ISO: each is sent on in
        # the wire form it reads, as the field's own ``to_python`` read it.
        spec = entry_spec()
        post(form_view(spec), ada, {"title": "Hi", "due": "10/25/2006", "at": "10/25/2006 14:30"})
        at = timezone.make_aware(datetime.datetime(2006, 10, 25, 14, 30))
        sent = spec.validator.calls[0]
        assert (sent["due"], sent["at"]) == ("2006-10-25", at.isoformat())
        data = received(spec)
        assert (data["due"], data["at"]) == (datetime.date(2006, 10, 25), at)

    def test_localized_numbers_are_read_as_the_form_reads_them(self, ada: Any) -> None:
        spec = entry_spec()
        with translation.override("de"):
            post(form_view(spec), ada, {"title": "Hi", "price": "12,50", "ratio": "2,5"})
        sent = spec.validator.calls[0]
        assert (sent["price"], sent["ratio"]) == ("12.50", 2.5)
        data = received(spec)
        assert (data["price"], data["ratio"]) == (Decimal("12.50"), 2.5)

    def test_a_decimal_with_three_places_is_not_read_as_thousands(
        self, ada: Any, settings: Any
    ) -> None:
        # The page reads 1,500 as one and a half and sends on "1.500". The
        # validator's own localized field then read that as fifteen hundred.
        settings.USE_THOUSAND_SEPARATOR = True
        spec = entry_spec()
        with translation.override("de"):
            post(form_view(spec), ada, {"title": "Hi", "price": "1,500"})
        assert spec.validator.calls[0]["price"] == "1.500"
        assert received(spec)["price"] == Decimal("1.500")

    def test_an_integer_the_form_reads_is_not_refused(self, ada: Any) -> None:
        # Django's IntegerField reads "5.0" as 5; a flat reading refuses it.
        spec = entry_spec()
        post(form_view(spec), ada, {"title": "Hi", "count": "5.0"})
        assert spec.validator.calls[0]["count"] == 5
        assert received(spec)["count"] == 5

    def test_an_integer_past_a_float_s_range_reaches_the_validator(self, ada: Any) -> None:
        # The field reads the digits as the int they spell, which no float
        # holds: asking whether it was finite raised OverflowError.
        spec = entry_spec()
        post(form_view(spec), ada, {"title": "Hi", "count": "1" + "0" * 400})
        assert spec.validator.calls[0]["count"] == 10**400

    def test_a_malformed_date_is_refused_once_in_the_kernels_words(self, ada: Any) -> None:
        spec = entry_spec()
        response = post(form_view(spec), ada, {"title": "Hi", "due": "someday"})
        assert (response.status_code, refused(response)) == (400, {"due": ["Enter a valid date."]})
        assert validated(spec) == 0

    def test_a_non_finite_number_never_reaches_the_validator(self, ada: Any) -> None:
        # The field reads "nan" as a float and only refuses it when it
        # validates; sent on as the string, the flat reading refuses it first.
        spec = entry_spec()
        response = post(form_view(spec), ada, {"title": "Hi", "ratio": "nan"})
        assert refused(response) == {"ratio": ["Enter a number."]}
        assert validated(spec) == 0

    def test_the_csrf_token_and_a_submit_button_are_not_arguments(self, ada: Any) -> None:
        # Only the form's fields are read, so neither reaches the closed
        # argument set, which would refuse both under REJECT.
        data = {"title": "Hi", "csrfmiddlewaretoken": "token", "save": "Save"}
        assert post(form_view(entry_spec()), ada, data).status_code == 302

    def test_a_disabled_field_is_not_read(self, ada: Any) -> None:
        # The form cleans it from its initial whatever is posted, and the
        # Validator declares no parameter for it, so a posted value is dropped
        # rather than refused as an unknown argument.
        spec = entry_spec()
        response = post(form_view(spec), ada, {"title": "Hi", "code": "E-9"})
        assert response.status_code == 302
        assert received(spec)["code"] == "E-1"


class TestClearingAFile:
    """The page's clear checkbox, which the widget reads as the field's value."""

    def test_the_rendered_checkbox_ticked_clears_the_file(self, ada: Any) -> None:
        spec = entry_spec(validator=Watched(CoverForm))
        page = get(form_view(spec), ada).render()
        # The checkbox the page offers, named as the Validator declares it.
        assert b'type="checkbox" name="cover-clear"' in page.content
        response = post(form_view(spec), ada, {"title": "Hi", "cover-clear": "on"})
        assert response.status_code == 302
        # The form's own value for a cleared file, as the Validator returns it.
        assert received(spec)["cover"] is False

    def test_left_unticked_the_held_file_is_kept(self, ada: Any) -> None:
        spec = entry_spec(validator=Watched(CoverForm))
        assert post(form_view(spec), ada, {"title": "Hi"}).status_code == 302
        assert isinstance(received(spec)["cover"], Held)
        assert spec.validator.calls == [{"title": "Hi"}]

    def test_an_upload_and_the_ticked_box_are_refused_in_the_form_s_words(self, ada: Any) -> None:
        spec = entry_spec(validator=Watched(CoverForm))
        upload = SimpleUploadedFile("new.txt", b"new")
        data = {"title": "Hi", "cover": upload, "cover-clear": "on"}
        response = post(form_view(spec), ada, data)
        assert response.status_code == 400
        # The Validator's form answered, not the shape check.
        assert validated(spec) == 1
        contradiction = forms.FileField.default_error_messages["contradiction"]
        assert refused(response) == {"cover": [contradiction]}
        assert spec.service.calls == []


class TestRefusals:
    def test_a_form_wide_clean_error_appears_once(self, ada: Any) -> None:
        spec = entry_spec()
        response = post(form_view(spec), ada, {"title": "shut"})
        assert (response.status_code, refused(response)) == (400, {"__all__": [CLOSED]})
        assert validated(spec) == 1

    def test_after_a_shape_refusal_the_form_s_own_checks_answer_too(self, ada: Any) -> None:
        # The shape check refuses the count before the Validator runs, so the
        # page's own form, built as the Validator's is, adds what its
        # ``clean()`` finds, and its own word for the count adds nothing.
        spec = entry_spec()
        response = post(form_view(spec), ada, {"title": "shut", "count": "x"})
        assert (response.status_code, refused(response)) == (
            400,
            {"count": [WHOLE], "__all__": [CLOSED]},
        )
        assert validated(spec) == 0

    def test_a_blank_required_field_and_a_clean_violation_show_in_one_post(self, ada: Any) -> None:
        # The shape check answers the blank title, so the
        # Validator never runs (none of its calls), and the clean() rule the
        # colour breaks is the page's own form's, in the same post.
        spec = entry_spec(validator=Watched(RedIsClosed))
        response = post(form_view(spec), ada, {"title": "", "colour": "red"}).render()
        assert (response.status_code, refused(response)) == (
            400,
            {"title": [REQUIRED], "__all__": [RED_CLOSED]},
        )
        assert validated(spec) == 0
        assert response.content.count(REQUIRED.encode()) == 1
        assert response.content.count(RED_CLOSED.encode()) == 1

    def test_a_uniqueness_rule_shows_beside_a_shape_refusal(self, ada: Any) -> None:
        # The date-time is the shape check's, so the Validator never runs, and
        # the username another user holds is the page's own ModelForm's.
        make_user("grace")
        spec = ServiceSpec(
            service=Record(),
            permissions=OPEN,
            validator=Watched(SignInForm),
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=user_by_pk, reads=PK
            ),
        )
        response = post(
            form_view(spec), ada, {"username": "grace", "last_login": "never"}, pk=ada.pk
        )
        assert (response.status_code, refused(response)) == (
            400,
            {
                "last_login": ["Enter a valid date/time."],
                "username": ["A user with that username already exists."],
            },
        )
        assert validated(spec) == 0

    @pytest.mark.parametrize("pk", ["own", "abc"], ids=["the-row", "a-refused-route"])
    def test_an_update_page_s_own_unchanged_value_is_never_taken(self, ada: Any, pk: str) -> None:
        # The page's ModelForm is validated on a copy of her row, so its
        # uniqueness check leaves her own username out, as the Validator's
        # does. A route the target's reads refuse finds no row, so the form is
        # not judged as a create either: the route's refusal, found while
        # the post is read and before dispatch's shape check, is all that
        # page shows.
        spec = ServiceSpec(
            service=Record(),
            permissions=OPEN,
            validator=Watched(SignInForm),
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=user_by_pk, reads=PK
            ),
        )
        route = ada.pk if pk == "own" else pk
        response = post(form_view(spec), ada, {"username": "ada", "last_login": "never"}, pk=route)
        expected = (
            {"last_login": ["Enter a valid date/time."]}
            if pk == "own"
            else {"__all__": [f"pk: {WHOLE}"]}
        )
        assert (response.status_code, refused(response)) == (400, expected)
        assert validated(spec) == 0

    @pytest.mark.parametrize(
        "exc",
        [ServiceConflict("Busy."), ServiceValidationError("Busy."), ServiceError("Busy.")],
        ids=["conflict", "service-validation", "service-error"],
    )
    def test_a_refusal_after_the_validator_took_the_form_validates_it_no_further(
        self, ada: Any, exc: Exception
    ) -> None:
        # The Validator's form, built as the page's is, took the post, so the
        # page's form would find nothing it did not: its clean() runs once,
        # the Validator's, and the refusal is all the page shows.
        Counting.cleans = 0

        def service(**pool: Any) -> None:
            raise exc

        spec = ServiceSpec(service=service, permissions=OPEN, validator=FormValidator(Counting))
        response = post(form_view(spec), ada, {"title": "Hi"})
        assert refused(response) == {"__all__": ["Busy."]}
        assert Counting.cleans == 1

    def test_a_validator_refusal_appears_once(self, ada: Any) -> None:
        spec = entry_spec()
        response = post(form_view(spec), ada, {"title": "Longer"})
        assert refused(response) == {
            "title": ["Ensure this value has at most 5 characters (it has 6)."]
        }
        assert validated(spec) == 1

    def test_a_url_kwarg_the_shape_check_refuses_is_a_prefixed_non_field_error(
        self, ada: Any
    ) -> None:
        # One tree carries every problem: the route's and the form's together.
        spec = row_spec(permissions=[OwnerOnly()])
        response = post(form_view(spec), ada, {"title": "Hi", "count": "x"}, pk="abc")
        assert (response.status_code, refused(response)) == (
            400,
            {"count": [WHOLE], "__all__": [f"pk: {WHOLE}"]},
        )

    def test_an_update_page_never_calls_its_own_unchanged_value_taken(self, ada: Any) -> None:
        # The Validator's form is bound to her row and passed her username;
        # whatever the page's own form found, the service's refusal is the
        # page's only error.
        def busy(**pool: Any) -> None:
            raise ServiceConflict("Busy.")

        response = post(
            form_view(user_spec(busy)), ada, {"username": "ada", "first_name": "Ada"}, pk=ada.pk
        )
        assert (response.status_code, refused(response)) == (409, {"__all__": ["Busy."]})

    def test_an_update_pages_validator_refusal_is_all_it_shows(self, ada: Any) -> None:
        view = form_view(user_spec(lambda **pool: None))
        response = post(view, ada, {"username": "ada", "first_name": "x" * 200}, pk=ada.pk)
        assert (response.status_code, refused(response)) == (
            400,
            {"first_name": ["Ensure this value has at most 150 characters (it has 200)."]},
        )

    def test_a_principal_the_spec_refuses_is_never_shown_the_page(
        self, ada: Any, django_assert_num_queries: Any
    ) -> None:
        # Dispatch's shape check runs before its permission check, so a
        # malformed post from a refused principal was answered with the page:
        # every user the choice field lists, and which pk was one of them.
        # Refused before the post is read, it queries nothing.
        spec = ServiceSpec(
            service=lambda **pool: None, permissions=[Refuse()], validator=FormValidator(AssignForm)
        )
        with django_assert_num_queries(0), pytest.raises(PermissionDenied):
            post(form_view(spec), ada, {"title": "", "assignee": "99999"})

    def test_a_refused_principals_unreadable_number_is_never_read(self, ada: Any) -> None:
        # ``coerce_flat`` refuses a number it cannot read, and it runs before
        # dispatch: sized so that it, not the shape check, would answer, a
        # refused principal read the post first was shown the page at 400.
        spec = entry_spec(permissions=[Refuse()])
        with pytest.raises(PermissionDenied):
            post(form_view(spec), ada, {"title": "Hi", "count": "abc"})

    def test_a_refused_principals_form_is_never_built(
        self, ada: Any, django_assert_num_queries: Any
    ) -> None:
        # A constructor that reads rows runs only for a principal the spec admits.
        spec = ServiceSpec(
            service=lambda **pool: None, permissions=[Refuse()], validator=FormValidator(Listed)
        )
        with django_assert_num_queries(0), pytest.raises(PermissionDenied):
            post(form_view(spec), ada, {"title": "Hi"})

    def test_the_class_level_check_runs_once(self, ada: Any) -> None:
        # The view runs it before reading the post and hands dispatch the
        # grant, rather than dispatch running it a second time.
        check = Counted()
        spec = entry_spec(permissions=[check])
        assert post(form_view(spec), ada, {"title": "Hi"}).status_code == 302
        assert check.calls == 1

    def test_the_route_wins_a_clash_with_the_form(self, ada: Any) -> None:
        spec = entry_spec()
        post(form_view(spec), ada, {"title": "Hi", "colour": "red"}, colour="blue")
        assert received(spec)["colour"] == "blue"

    @pytest.mark.parametrize(
        ("detail", "expected"),
        [
            ("Closed today.", {"__all__": ["Closed today."]}),
            (gettext_lazy("Closed today."), {"__all__": ["Closed today."]}),
            (["Closed today.", "Try Monday."], {"__all__": ["Closed today.", "Try Monday."]}),
            (
                {"title": ["Taken."], "slot": ["Full."]},
                {"title": ["Taken."], "__all__": ["slot: Full."]},
            ),
        ],
        ids=["str", "lazy", "list", "mapping"],
    )
    def test_a_service_validation_error_is_placed_on_the_form(
        self, ada: Any, detail: Any, expected: Any
    ) -> None:
        response = post(form_view(raising(ServiceValidationError(detail))), ada, {"title": "Hi"})
        assert (response.status_code, refused(response)) == (400, expected)

    @pytest.mark.parametrize(
        ("exc", "status"),
        [
            (ServiceConflict("Moved while you were editing."), 409),
            (ActionUnavailable("Moved while you were editing.", code="moved"), 409),
            (ServiceError("Moved while you were editing."), 422),
            (
                AdditionalInputRequired("Moved while you were editing.", schema={"type": "object"}),
                422,
            ),
            (Throttled("Moved while you were editing."), 400),
            # Raised by an operation, since the page reads no body itself:
            # answered as the JSON views answer it.
            (UnsupportedMediaType("Moved while you were editing."), 415),
        ],
        ids=[
            "conflict",
            "action-unavailable",
            "service-error",
            "additional-input-required",
            "dispatch-error",
            "unsupported-media-type",
        ],
    )
    def test_a_message_refusal_is_a_non_field_error_at_its_status(
        self, ada: Any, exc: Exception, status: int
    ) -> None:
        response = post(form_view(raising(exc)), ada, {"title": "Hi"})
        assert (response.status_code, refused(response)) == (
            status,
            {"__all__": ["Moved while you were editing."]},
        )

    def test_the_refused_page_shows_each_message_once(self, ada: Any) -> None:
        response = post(form_view(entry_spec()), ada, {"title": "shut"}).render()
        assert response.content.count(CLOSED.encode()) == 1


@pytest.fixture(params=["stated", "unstated"])
def ranges(request: pytest.FixtureRequest) -> None:
    """The range the backend states, and none, as Django 4.2's SQLite states."""
    if request.param == "unstated":
        request.getfixturevalue("unstated_ranges")


@pytest.mark.usefixtures("ranges")
class TestAValuePastItsColumn:
    """A post refused for a value no column holds re-renders the page.

    The page's own form is validated beside an argument refusal under the
    kernel's ``clean_held_to_their_columns``, the Validator's own guards:
    validated without them, it would hand the value to the database driver,
    which raises ``OverflowError``.
    """

    def test_a_model_form_s_integer_past_its_column_is_refused_on_the_page(self, ada: Any) -> None:
        book = Book.objects.create(
            author=BookAuthor.objects.create(name="Ada"), title="Notes", price=Decimal("1.00")
        )
        spec = ServiceSpec(service=Record(), permissions=OPEN, validator=FormValidator(EditionForm))
        response = post(form_view(spec), ada, {"book": book.pk, "number": 2**63})
        assert response.status_code == 400
        assert refused(response) == {
            "number": ["Ensure this value is less than or equal to %s." % (2**63 - 1)]
        }

    def test_a_choice_s_key_past_its_column_is_refused_on_the_page(self, ada: Any) -> None:
        spec = ServiceSpec(service=Record(), permissions=OPEN, validator=FormValidator(AssignForm))
        response = post(form_view(spec), ada, {"title": "Hi", "assignee": 2**63})
        assert response.status_code == 400
        assert refused(response) == {
            "assignee": ["Select a valid choice. That choice is not one of the available choices."]
        }

    def test_a_key_past_its_column_beside_a_shape_refusal_never_reaches_the_driver(
        self, ada: Any
    ) -> None:
        # The shape check refuses the missing title before the Validator runs,
        # so the page's own form judges the key, held to its column as the
        # Validator's is: refused in the field's words, never handed to the
        # driver, which raises ``OverflowError`` for it.
        spec = ServiceSpec(
            service=Record(), permissions=OPEN, validator=FormValidator(AssigneesForm)
        )
        response = post(form_view(spec), ada, {"assignees": [2**63]})
        assert (response.status_code, refused(response)) == (
            400,
            {
                "title": [REQUIRED],
                "assignees": [
                    f"Select a valid choice. {2**63} is not one of the available choices."
                ],
            },
        )

    def test_a_list_s_key_past_its_column_is_refused_on_the_page(self, ada: Any) -> None:
        spec = ServiceSpec(
            service=Record(), permissions=OPEN, validator=FormValidator(AssigneesForm)
        )
        response = post(form_view(spec), ada, {"title": "Hi", "assignees": [2**63]})
        assert response.status_code == 400
        assert refused(response) == {
            "assignees": [f"Select a valid choice. {2**63} is not one of the available choices."]
        }


class TestDeniedAndMissing:
    def test_a_principal_the_class_check_refuses_is_permission_denied(self, ada: Any) -> None:
        spec = entry_spec(permissions=[Refuse()])
        with pytest.raises(PermissionDenied, match=r"^Only editors may run this\.$"):
            post(form_view(spec), ada, {"title": "Hi"})
        assert spec.service.calls == []

    def test_a_row_the_object_check_refuses_is_permission_denied(self, ada: Any) -> None:
        note = Note.objects.create(owner=make_user("bob"), title="Bob's")
        spec = row_spec(permissions=[OwnerOnly()])
        with pytest.raises(PermissionDenied, match=r"^Only the owner may touch this note\.$"):
            post(form_view(spec), ada, {"title": "Hi"}, pk=note.pk)
        assert spec.service.calls == []

    def test_a_deactivated_principal_is_permission_denied(self, ada: Any) -> None:
        ada.is_active = False
        with pytest.raises(PermissionDenied, match=r"^The acting principal is unavailable\.$"):
            post(form_view(entry_spec()), ada, {"title": "Hi"})

    def test_a_not_found_result_is_http404(self, ada: Any) -> None:
        spec = row_spec()
        with pytest.raises(Http404):
            post(form_view(spec), ada, {"title": "Hi"}, pk=404)
        assert spec.service.calls == []

    def test_a_service_not_found_is_http404(self, ada: Any) -> None:
        with pytest.raises(Http404, match=r"^No such entry\.$"):
            post(form_view(raising(ServiceNotFound("No such entry."))), ada, {"title": "Hi"})


class TestServing:
    def test_the_views_settings_reach_dispatch(self, ada: Any) -> None:
        # A field the form adds in ``__init__`` is read, and FormValidator
        # declares no parameter for it, so the policy decides what becomes of it.
        spec = entry_spec(validator=Watched(WithAnExtra))
        view = form_view(spec, unknown_arguments=UnknownArguments.IGNORE, pool_seeds=TENANT_SEEDS)
        assert post(view, ada, {"title": "Hi", "extra": "x"}).status_code == 302
        assert spec.service.calls[0]["tenant"] == "tenant-of-ada"

    def test_under_reject_a_field_the_spec_does_not_declare_is_refused(self, ada: Any) -> None:
        spec = entry_spec(validator=Watched(WithAnExtra))
        response = post(form_view(spec), ada, {"title": "Hi", "extra": "x"})
        assert (response.status_code, refused(response)) == (
            400,
            {"extra": ["Unknown argument."]},
        )

    @pytest.mark.parametrize("policy", [UnknownArguments.REJECT, UnknownArguments.IGNORE])
    def test_a_route_capturing_an_undeclared_kwarg_is_improperly_configured(
        self, ada: Any, policy: UnknownArguments
    ) -> None:
        # The route is the host's, so a kwarg the spec does not declare is
        # wrong for every caller rather than a refusal of this one, under
        # either policy: the policy governs what a client sends.
        spec = entry_spec()
        with pytest.raises(ImproperlyConfigured) as raised:
            post(
                form_view(spec, unknown_arguments=policy), ada, {"title": "Hi"}, zone="eu", org="a"
            )
        assert str(raised.value) == UNDECLARED.format(names="org, zone")
        assert spec.service.calls == []

    def test_the_page_is_refused_for_a_route_capturing_an_undeclared_kwarg(self, ada: Any) -> None:
        # On GET too, as SpecView refuses it on any method: served anyway, the
        # page would fail only once someone had filled it in.
        with pytest.raises(ImproperlyConfigured) as raised:
            get(form_view(entry_spec()), ada, zone="eu", org="a")
        assert str(raised.value) == UNDECLARED.format(names="org, zone")

    def test_an_undeclared_kwarg_is_raised_whoever_posts(self, ada: Any) -> None:
        # The host's mistake, so a principal the spec refuses meets it too,
        # rather than a 403 that hides it until someone admitted posts.
        spec = entry_spec(permissions=[Refuse()])
        with pytest.raises(ImproperlyConfigured) as raised:
            post(form_view(spec), ada, {"title": "Hi"}, zone="eu")
        assert str(raised.value) == UNDECLARED.format(names="zone")

    def test_a_method_it_does_not_serve_is_djangos_405(self, ada: Any) -> None:
        request = signed_in(FACTORY.put("/", data=b""), ada)
        assert form_view(entry_spec())(request).status_code == 405

    def test_it_is_a_sync_view(self) -> None:
        assert SpecFormView.view_is_async is False


HTMX = {"HTTP_HX_REQUEST": "true"}
BOOSTED = {**HTMX, "HTTP_HX_BOOSTED": "true"}
RESTORING = {**HTMX, "HTTP_HX_HISTORY_RESTORE_REQUEST": "true"}
# The three headers the mode is read from, which every answer varies on.
MODE_HEADERS = {"HX-Request", "HX-Boosted", "HX-History-Restore-Request"}
PAGE = "spec_page.html"
FRAGMENT = "spec_fragment.html"


def fragmented(spec: ServiceSpec, **attributes: Any) -> Any:
    """A view whose page has a layout and whose fragment, in a file of its own, has none.

    Set as class attributes, as a host's subclass sets them, so the fragment
    renders on every Django this suite runs on.
    """
    attributes = {
        "template_name": PAGE,
        "fragment_template_name": FRAGMENT,
        "success_url": "/done/",
        **attributes,
    }
    return type("Fragmented", (SpecFormView,), attributes).as_view(spec=spec)


def hx_get(view: Any, user: Any, headers: Mapping[str, str] = HTMX, **url_kwargs: Any) -> Any:
    return view(signed_in(FACTORY.get("/", **headers), user), **url_kwargs)


def hx_post(
    view: Any,
    user: Any,
    data: Mapping[str, Any],
    headers: Mapping[str, str] = HTMX,
    **url_kwargs: Any,
) -> Any:
    return view(signed_in(FACTORY.post("/", data=data, **headers), user), **url_kwargs)


def varied_on(response: Any) -> set[str]:
    return {name.strip() for name in response.headers.get("Vary", "").split(",") if name.strip()}


def hx_headers(response: Any) -> dict[str, str]:
    return {
        name: value for name, value in response.headers.items() if name.lower().startswith("hx-")
    }


class HtmxDetails:
    """What django-htmx's middleware sets as ``request.htmx``, in the shape it has.

    django-htmx's ``HtmxDetails`` is truthy for a request whose ``HX-Request``
    is ``true``, and carries ``boosted`` and ``history_restore_request``. The
    tests that use this give it answers its headers disagree with, so they
    can tell which of the two the view read.
    """

    def __init__(
        self, *, htmx: bool = True, boosted: bool = False, history_restore_request: bool = False
    ) -> None:
        self._htmx = htmx
        self.boosted = boosted
        self.history_restore_request = history_restore_request

    def __bool__(self) -> bool:
        return self._htmx


class TestWantsFragment:
    # Each condition of the rule is held by its own row: a request that is
    # not htmx, a boosted one and a history restore each get the page.
    @pytest.mark.parametrize(
        ("headers", "expected"),
        [
            ({}, False),
            (HTMX, True),
            ({"HTTP_HX_REQUEST": "false"}, False),
            (BOOSTED, False),
            ({**HTMX, "HTTP_HX_BOOSTED": "false"}, True),
            (RESTORING, False),
            ({**HTMX, "HTTP_HX_HISTORY_RESTORE_REQUEST": "false"}, True),
        ],
        ids=[
            "plain",
            "htmx",
            "htmx-false",
            "boosted",
            "boosted-false",
            "history-restore",
            "history-restore-false",
        ],
    )
    def test_read_from_the_headers(self, headers: Mapping[str, str], expected: bool) -> None:
        assert SpecFormView().wants_fragment(FACTORY.get("/", **headers)) is expected

    # Each row's headers say the opposite of the details, so a view reading
    # the headers where django-htmx set ``request.htmx`` fails every row.
    @pytest.mark.parametrize(
        ("headers", "details", "expected"),
        [
            ({}, HtmxDetails(), True),
            (HTMX, HtmxDetails(htmx=False), False),
            (HTMX, HtmxDetails(boosted=True), False),
            (HTMX, HtmxDetails(history_restore_request=True), False),
        ],
        ids=["htmx", "not-htmx", "boosted", "history-restore"],
    )
    def test_read_from_django_htmx_where_its_middleware_ran(
        self, headers: Mapping[str, str], details: HtmxDetails, expected: bool
    ) -> None:
        request = FACTORY.get("/", **headers)
        request.htmx = details
        assert SpecFormView().wants_fragment(request) is expected


class TestTheFragmentOnGet:
    def test_an_htmx_get_is_the_form_fragment(self, ada: Any) -> None:
        # An ``hx-get`` that loads the form into a dialog, say.
        spec = entry_spec()
        response = hx_get(fragmented(spec), ada).render()
        assert response.status_code == 200
        assert response.template_name == [FRAGMENT]
        assert b"<html>" not in response.content
        assert b'name="title"' in response.content
        assert not response.context_data["form"].is_bound
        assert response.context_data["spec"] is spec

    def test_its_context_is_the_page_s(self, ada: Any) -> None:
        view = fragmented(entry_spec(), extra_context={"heading": "New entry"})
        page, fragment = get(view, ada), hx_get(view, ada)
        assert set(fragment.context_data) == set(page.context_data)
        assert fragment.context_data["heading"] == "New entry"

    def test_a_plain_get_is_the_page(self, ada: Any) -> None:
        response = get(fragmented(entry_spec()), ada).render()
        assert response.template_name == [PAGE]
        assert b"<html>" in response.content

    @pytest.mark.parametrize("headers", [BOOSTED, RESTORING], ids=["boosted", "history-restore"])
    def test_a_boosted_or_restoring_get_is_the_page(
        self, ada: Any, headers: Mapping[str, str]
    ) -> None:
        # A boosted link swaps the whole body, and a history restore after a
        # cache miss needs the whole page back.
        response = hx_get(fragmented(entry_spec()), ada, headers).render()
        assert response.template_name == [PAGE]
        assert b"<html>" in response.content

    def test_a_principal_the_class_check_refuses_is_permission_denied(self, ada: Any) -> None:
        with pytest.raises(PermissionDenied, match=r"^Only editors may run this\.$"):
            hx_get(fragmented(entry_spec(permissions=[Refuse()])), ada)


class TestTheFragmentOnASuccess:
    def test_an_htmx_success_is_hx_redirect_never_a_302(self, ada: Any) -> None:
        # An XHR follows a 302 itself and swaps the next page into the form.
        # The view needs nothing htmx-specific: the default fragment address
        # is never rendered on a success.
        spec = entry_spec()
        response = hx_post(form_view(spec), ada, {"title": "Hi"})
        assert response.status_code == 200
        assert hx_headers(response) == {"HX-Redirect": "/done/"}
        assert "Location" not in response
        assert response.content == b""
        assert received(spec)["title"] == "Hi"

    def test_hx_redirect_reads_get_success_url(self, ada: Any) -> None:
        class ToTheEntry(SpecFormView):
            template_name = TEMPLATE

            def get_success_url(self, result: DispatchResult) -> str:
                return f"/entries/{result.value}/"

        response = hx_post(ToTheEntry.as_view(spec=entry_spec(returns=7)), ada, {"title": "Hi"})
        assert response["HX-Redirect"] == "/entries/7/"

    @pytest.mark.parametrize("headers", [BOOSTED, RESTORING], ids=["boosted", "history-restore"])
    def test_a_boosted_success_is_the_page_s_redirect(
        self, ada: Any, headers: Mapping[str, str]
    ) -> None:
        response = hx_post(fragmented(entry_spec()), ada, {"title": "Hi"}, headers)
        assert (response.status_code, response.url) == (302, "/done/")
        assert hx_headers(response) == {}

    @staticmethod
    def to(url: str) -> Any:
        class ToTheURL(SpecFormView):
            template_name = PAGE

            def get_success_url(self, result: DispatchResult) -> str:
                return url

        return ToTheURL.as_view(spec=entry_spec())

    def test_hx_redirect_is_the_page_s_location(self, ada: Any) -> None:
        # Encoded as the page's redirect encodes it: set raw, a path outside
        # ASCII is a MIME-encoded header no browser reads as the path.
        page = hx_post(self.to("/books/日本/"), ada, {"title": "Hi"}, {})
        fragment = hx_post(self.to("/books/日本/"), ada, {"title": "Hi"})
        assert fragment["HX-Redirect"] == page["Location"] == "/books/%E6%97%A5%E6%9C%AC/"
        assert "Location" not in fragment
        # Read as a redirect's ``url`` is, so its repr and a test reading it work.
        assert fragment.url == page.url

    @pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "fragment"])
    def test_an_unsafe_scheme_is_refused_in_both_modes(
        self, ada: Any, headers: Mapping[str, str]
    ) -> None:
        with pytest.raises(DisallowedRedirect):
            hx_post(self.to("javascript:alert(1)"), ada, {"title": "Hi"}, headers)

    def test_an_error_of_get_success_url_s_own_is_not_chained_to_the_lookup(self, ada: Any) -> None:
        # Raised outside the lookup's ``except``, so its traceback is its own
        # rather than "during handling of TemplateDoesNotExist".
        class Misconfigured(SpecFormView):
            template_name = PAGE

            def get_success_url(self, result: DispatchResult) -> str:
                raise ImproperlyConfigured("Nowhere to go.")

        with pytest.raises(ImproperlyConfigured) as raised:
            hx_post(Misconfigured.as_view(spec=entry_spec()), ada, {"title": "Hi"})
        assert raised.value.__context__ is None

    def test_a_cookie_the_middleware_sets_reaches_hx_redirect(
        self, ada: Any, settings: Any
    ) -> None:
        """Holds the page's claim that a cookie set while the request is handled reaches it.

        What sets it is the host's middleware, on whatever answer the view
        returns, so this holds that ``HX-Redirect`` is an answer the
        middleware passes through like any other: signing in sets the
        session cookie, and an answer that lost it would sign the visitor
        straight back out.
        """
        settings.SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"

        class SignsIn(SpecFormView):
            template_name = TEMPLATE

            def get_success_url(self, result: DispatchResult) -> str:
                login(self.request, ada)
                return "/done/"

        view = SessionMiddleware(SignsIn.as_view(spec=entry_spec()))
        request = signed_in(FACTORY.post("/", data={"title": "Hi"}, **HTMX), ada)
        response = view(request)
        assert (response.status_code, response["HX-Redirect"]) == (200, "/done/")
        assert response.cookies[settings.SESSION_COOKIE_NAME].value


@pytest.fixture
def result_fragment(settings: Any) -> str:
    """A fragment template defining ``spec-result``, as each Django addresses one.

    From Django 6.0 the address ``fragment.html#spec-result`` names the
    ``partialdef`` inside ``fragment.html``. Below it - with no
    django-template-partials installed - the same address is a template name
    like any other, so the template is registered under it: the view asks for
    one address on every version, and what answers it is the version's.
    """
    form = '<form hx-post="">{{ form }}</form>'
    result = "<p>Saved {{ result.value }} for {{ heading }}.</p>"
    if django.VERSION >= (6, 0):
        templates = {
            "fragment.html": f"{form}{{% partialdef spec-result %}}{result}{{% endpartialdef %}}"
        }
    else:
        templates = {"fragment.html": form, "fragment.html#spec-result": result}
    settings.TEMPLATES = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", templates)]},
        }
    ]
    return "fragment.html"


class TestTheResultFragment:
    def test_a_spec_result_fragment_is_rendered_in_place(
        self, ada: Any, result_fragment: str
    ) -> None:
        view = fragmented(
            entry_spec(returns=7),
            template_name=None,
            fragment_template_name=result_fragment,
            extra_context={"heading": "the entries"},
        )
        response = hx_post(view, ada, {"title": "Hi"}).render()
        assert response.status_code == 200
        assert response.content == b"<p>Saved 7 for the entries.</p>"
        assert response.context_data["result"].value == 7
        # The view's context with the result in it, and no form: the post's is done with.
        assert set(response.context_data) == {"result", "view", "spec", "heading"}
        assert hx_headers(response) == {"HX-Trigger": "spec-succeeded"}
        assert varied_on(response) >= MODE_HEADERS

    def test_the_form_fragment_of_that_template_has_no_result(
        self, ada: Any, result_fragment: str
    ) -> None:
        view = fragmented(entry_spec(), template_name=None, fragment_template_name=result_fragment)
        content = hx_get(view, ada).render().content
        assert content.startswith(b'<form hx-post="">')
        assert b"Saved" not in content

    def test_a_fragment_template_without_one_redirects(self, ada: Any) -> None:
        response = hx_post(fragmented(entry_spec()), ada, {"title": "Hi"})
        assert hx_headers(response) == {"HX-Redirect": "/done/"}


class TestTheFragmentOnARefusal:
    def test_a_refused_htmx_post_is_the_form_fragment_at_200(self, ada: Any) -> None:
        # The whole page swapped into the form's target nests a second layout
        # inside it, and a 4xx is swapped nowhere by default.
        response = hx_post(fragmented(entry_spec()), ada, {"title": "shut"}).render()
        assert response.status_code == 200
        assert response.template_name == [FRAGMENT]
        assert b"<html>" not in response.content
        assert response.content.count(CLOSED.encode()) == 1
        assert hx_headers(response) == {}

    @pytest.mark.parametrize(
        ("data", "exc", "status"),
        [
            ({"title": "shut"}, None, 400),
            ({"title": "Hi", "count": "x"}, None, 400),
            ({"title": "Hi"}, ServiceValidationError({"title": ["Taken."]}), 400),
            ({"title": "Hi"}, ServiceConflict("Moved."), 409),
            ({"title": "Hi"}, ActionUnavailable("Moved.", code="moved"), 409),
            ({"title": "Hi"}, ServiceError("Moved."), 422),
            ({"title": "Hi"}, AdditionalInputRequired("Moved.", schema={"type": "object"}), 422),
            ({"title": "Hi"}, Throttled("Moved."), 400),
            ({"title": "Hi"}, UnsupportedMediaType("Moved."), 415),
        ],
        ids=[
            "form",
            "shape-check",
            "service-validation-error",
            "conflict",
            "action-unavailable",
            "service-error",
            "additional-input-required",
            "dispatch-error",
            "unsupported-media-type",
        ],
    )
    def test_every_refusal_the_page_re_renders_is_a_fragment_at_200(
        self, ada: Any, data: Mapping[str, Any], exc: Exception | None, status: int
    ) -> None:
        def spec() -> ServiceSpec:
            return entry_spec() if exc is None else raising(exc)

        page = post(fragmented(spec()), ada, data)
        fragment = hx_post(fragmented(spec()), ada, data)
        assert (page.status_code, page.template_name) == (status, [PAGE])
        assert (fragment.status_code, fragment.template_name) == (200, [FRAGMENT])
        assert refused(fragment) == refused(page)
        assert set(fragment.context_data) == set(page.context_data)
        assert varied_on(page) >= MODE_HEADERS
        assert varied_on(fragment) >= MODE_HEADERS
        # No header on any of them, a confirmation included: it is re-rendered
        # in the target the form already swaps into.
        assert hx_headers(fragment) == {}

    @pytest.mark.parametrize("headers", [BOOSTED, RESTORING], ids=["boosted", "history-restore"])
    def test_a_boosted_refusal_is_the_page(self, ada: Any, headers: Mapping[str, str]) -> None:
        response = hx_post(fragmented(entry_spec()), ada, {"title": "shut"}, headers)
        assert (response.status_code, response.template_name) == (400, [PAGE])

    @pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "fragment"])
    @pytest.mark.parametrize(
        ("exc", "raised"),
        [
            (NotPermitted("Not yours."), PermissionDenied),
            (PrincipalUnavailable("Gone."), PermissionDenied),
            (ServiceNotFound("No such entry."), Http404),
        ],
        ids=["not-permitted", "principal-unavailable", "service-not-found"],
    )
    def test_a_denial_is_the_host_s_in_both_modes(
        self, ada: Any, headers: Mapping[str, str], exc: Exception, raised: type[Exception]
    ) -> None:
        with pytest.raises(raised, match=rf"^{re.escape(str(exc))}$"):
            hx_post(fragmented(raising(exc)), ada, {"title": "Hi"}, headers)

    @pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "fragment"])
    def test_a_missing_row_is_http404_in_both_modes(
        self, ada: Any, headers: Mapping[str, str]
    ) -> None:
        with pytest.raises(Http404):
            hx_post(fragmented(row_spec()), ada, {"title": "Hi"}, headers, pk=404)


class TestNothingHtmxReachesTheSpec:
    def test_dispatch_is_called_the_same_way_in_both_modes(
        self, ada: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, Any]] = []
        real = spec_form_view.dispatch

        def recording(spec: Any, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return real(spec, **kwargs)

        monkeypatch.setattr(spec_form_view, "dispatch", recording)
        spec = entry_spec()
        data = {"title": "Hi", "csrfmiddlewaretoken": "token", "save": "Save"}
        post(fragmented(spec), ada, data)
        every_header = {
            **HTMX,
            "HTTP_HX_TARGET": "form",
            "HTTP_HX_TRIGGER": "save",
            "HTTP_HX_TRIGGER_NAME": "save",
            "HTTP_HX_CURRENT_URL": "http://testserver/",
        }
        hx_post(fragmented(spec), ada, data, every_header)
        plain, htmx = calls
        assert htmx.keys() == plain.keys()
        assert htmx["arguments"] == plain["arguments"]
        assert plain["arguments"]["title"] == "Hi"
        assert not [name for name in htmx["arguments"] if name.lower().startswith("hx")]
        assert htmx["pool_seeds"] is plain["pool_seeds"]
        assert spec.service.calls[0]["data"] == spec.service.calls[1]["data"]


class TestVary:
    # One URL answers two bodies, so a cache keyed on fewer headers would
    # hand a fragment to a page load or a history restore.
    @pytest.mark.parametrize("headers", [{}, HTMX, BOOSTED, RESTORING])
    @pytest.mark.parametrize(
        ("method", "data"),
        [("get", None), ("post", {"title": "Hi"}), ("post", {"title": "shut"})],
        ids=["get", "success", "refused"],
    )
    def test_every_answer_varies_on_the_mode_headers(
        self, ada: Any, headers: Mapping[str, str], method: str, data: Any
    ) -> None:
        request = getattr(FACTORY, method)("/", data=data, **headers)
        response = fragmented(entry_spec())(signed_in(request, ada))
        assert varied_on(response) >= MODE_HEADERS

    def test_a_method_it_does_not_serve_varies_too(self, ada: Any) -> None:
        response = fragmented(entry_spec())(signed_in(FACTORY.put("/", data=b"", **HTMX), ada))
        assert response.status_code == 405
        assert varied_on(response) >= MODE_HEADERS


class TestTheFragmentAddress:
    def test_it_defaults_to_the_page_s_spec_form_fragment(self) -> None:
        view = SpecFormView(template_name="books/book_form.html")
        assert view.get_fragment_template_names() == ["books/book_form.html#spec-form"]

    def test_it_is_fragment_template_name_where_set(self) -> None:
        view = SpecFormView(template_name=PAGE, fragment_template_name=FRAGMENT)
        assert view.get_fragment_template_names() == [FRAGMENT]

    def test_a_page_with_no_spec_form_fragment_raises_naming_it(self, ada: Any) -> None:
        # Never the page instead: the error names the one block the template lacks.
        response = hx_get(form_view(entry_spec()), ada)
        with pytest.raises(TemplateDoesNotExist) as raised:
            response.render()
        assert str(raised.value) == f"{TEMPLATE}#spec-form"

    def test_a_fragment_template_that_does_not_exist_raises_naming_it(self, ada: Any) -> None:
        response = hx_post(
            fragmented(entry_spec(), fragment_template_name="entries/missing.html"),
            ada,
            {"title": "shut"},
        )
        with pytest.raises(TemplateDoesNotExist) as raised:
            response.render()
        assert str(raised.value) == "entries/missing.html"


class TestConfiguration:
    @pytest.mark.parametrize(
        ("given", "named"),
        [({}, "NoneType"), ({"spec": note_spec()}, "SelectorSpec")],
        ids=["no-spec", "selector-spec"],
    )
    def test_a_view_with_no_service_spec_is_refused(self, given: Any, named: str) -> None:
        with pytest.raises(ImproperlyConfigured) as refused_:
            SpecFormView.as_view(**given, success_url="/done/")
        assert str(refused_.value) == (
            f"SpecFormView.as_view() needs a ServiceSpec, the write a form posts; got {named}."
        )

    @pytest.mark.parametrize(
        ("validator", "named"), [(Titled(), "Titled"), (None, "NoneType")], ids=["other", "none"]
    )
    def test_a_spec_not_validated_by_a_form_is_refused(self, validator: Any, named: str) -> None:
        spec = ServiceSpec(service=Record(), permissions=OPEN, validator=validator)
        with pytest.raises(ImproperlyConfigured) as refused_:
            SpecFormView.as_view(spec=spec, success_url="/done/")
        assert str(refused_.value) == (
            "SpecFormView renders the form its spec validates with, so the spec's validator "
            f"must be a FormValidator; got {named}."
        )

    def test_a_view_with_nowhere_to_redirect_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused_:
            SpecFormView.as_view(spec=entry_spec())
        assert str(refused_.value) == (
            "SpecFormView.as_view() needs a success_url, where a successful post "
            "redirects, or a get_success_url that returns one."
        )

    def test_a_subclass_overriding_get_success_url_needs_no_success_url(self) -> None:
        # Holds the second condition of the redirect check.
        class ToTheEntry(SpecFormView):
            spec = entry_spec()

            def get_success_url(self, result: DispatchResult) -> str:
                return "/entries/"

        assert callable(ToTheEntry.as_view())

    def test_a_many_spec_is_refused(self) -> None:
        # A form posts one row, and a many spec takes a list of them.
        spec = ServiceSpec(service=Record(), permissions=OPEN, many=True, validator=Watched())
        with pytest.raises(ImproperlyConfigured, match="many spec"):
            form_view(spec)

    def test_a_misspelt_setting_is_djangos_own_refusal(self) -> None:
        with pytest.raises(TypeError, match="invalid keyword 'sepc'"):
            SpecFormView.as_view(sepc=entry_spec())


def paged(spec: ServiceSpec, name: str = "Paged", **attributes: Any) -> Any:
    """A subclass setting ``attributes``, as a host's does, so an attribute no view reads is a red."""
    attributes = {"spec": spec, "template_name": TEMPLATE, "success_url": "/done/", **attributes}
    return type(name, (SpecFormView,), attributes)


def headed(view: Any, user: Any, method: str = "GET", data: Any = None, **url_kwargs: Any) -> Any:
    """A request carrying ``X-Tenant: acme``, for a hook reading what the request carries."""
    headers = {"HTTP_X_TENANT": "acme"}
    if method == "GET":
        request = FACTORY.get("/", **headers)
    else:
        request = FACTORY.post("/", data=data or {}, **headers)
    return view(signed_in(request, user), **url_kwargs)


def tenant_from_the_header(self: Any, request: Any) -> PoolSeeds:
    return self.pool_seeds.extend(tenant=lambda: request.headers["X-Tenant"])


class SeesTenantOnTheRow(PermissionCheck):
    """Admits every row, recording the ``tenant`` seed its object-level check was asked with."""

    def __init__(self) -> None:
        self.tenants: list[Any] = []

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return True

    def has_object_permission(self, principal: Any, spec: Any, obj: Any, *, tenant: Any) -> bool:
        self.tenants.append(tenant)
        return True


@pytest.fixture
def note(ada: Any) -> Note:
    return Note.objects.create(owner=ada, title="One")


def field_tag(response: Any, name: str) -> bytes:
    """The rendered tag of the form's field ``name``, an input or a select."""
    found = re.search(
        rb'<(?:input|select)[^>]*name="' + name.encode() + rb'"[^>]*>', response.content
    )
    assert found is not None
    return found.group(0)


class TestThePagesRouteKwargs:
    def test_a_kwarg_mapped_to_none_is_dropped(self, ada: Any) -> None:
        view = paged(entry_spec(), route_kwargs={"org": None}).as_view()
        assert get(view, ada, org="acme").status_code == 200
        assert post(view, ada, {"title": "Hi"}, org="acme").status_code == 302

    def test_a_kwarg_is_renamed_to_the_parameter_it_fills(self, ada: Any, note: Note) -> None:
        spec = row_spec()
        view = paged(spec, route_kwargs={"note": "pk"}).as_view()
        assert get(view, ada, note=str(note.pk)).context_data["form"].initial["title"] == "One"
        assert post(view, ada, {"title": "Hi"}, note=str(note.pk)).status_code == 302
        assert spec.service.calls[0]["instance"] == note

    def test_a_renamed_kwarg_that_does_not_parse_is_the_clients_400(self, ada: Any) -> None:
        view = paged(row_spec(), route_kwargs={"note": "pk"}).as_view()
        with pytest.raises(BadRequest):
            get(view, ada, note="four")

    def test_a_tuple_allows_its_names_and_drops_the_rest(self, ada: Any, note: Note) -> None:
        view = paged(row_spec(), route_kwargs=("pk",)).as_view()
        response = get(view, ada, org="acme", pk=str(note.pk))
        assert response.context_data["form"].initial["title"] == "One"

    def test_a_bare_string_is_refused_at_as_view(self) -> None:
        with pytest.raises(TypeError, match="route_kwargs is a tuple of names"):
            paged(entry_spec(), route_kwargs="org").as_view()

    def test_a_target_the_spec_does_not_declare_is_refused_at_as_view(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="maps a kwarg to team, which the spec"):
            paged(entry_spec(), route_kwargs={"org": "team"}).as_view()

    def test_an_allowed_name_the_spec_does_not_declare_is_refused_at_as_view(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="allows team, which the spec"):
            paged(row_spec(), route_kwargs=("pk", "team")).as_view()

    def test_route_kwargs_passed_to_as_view_are_checked_there_too(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="maps a kwarg to team, which the spec"):
            form_view(entry_spec(), route_kwargs={"org": "team"})

    def test_two_kwargs_onto_one_parameter_are_refused_per_request(
        self, ada: Any, note: Note
    ) -> None:
        view = paged(row_spec(), route_kwargs={"note": "pk"}).as_view()
        assert get(view, ada, pk=str(note.pk)).status_code == 200
        with pytest.raises(ImproperlyConfigured, match="fills pk from more than one kwarg"):
            get(view, ada, pk=str(note.pk), note=str(note.pk))
        with pytest.raises(ImproperlyConfigured, match="fills pk from more than one kwarg"):
            post(view, ada, {"title": "Hi"}, pk=str(note.pk), note=str(note.pk))

    def test_a_kwarg_the_mapping_does_not_name_is_still_refused(self, ada: Any) -> None:
        view = paged(entry_spec(), route_kwargs={"org": None}).as_view()
        with pytest.raises(ImproperlyConfigured, match="The route captures team,"):
            get(view, ada, org="acme", team="a")


class TestThePagesPoolSeeds:
    @pytest.mark.parametrize(
        ("binding", "name"),
        [
            (ServerArguments(values={"title": "S"}), "title"),
            (ServerArguments(withheld=frozenset({"count"})), "count"),
        ],
        ids=["bound", "withheld"],
    )
    def test_a_per_call_seed_occupying_a_name_the_binding_answers_for_is_refused(
        self, ada: Any, binding: ServerArguments, name: str
    ) -> None:
        def seeded(self: Any, request: Any) -> PoolSeeds:
            return self.pool_seeds.extend(**{name: lambda: "seeded"})

        view = paged(
            entry_spec(), "Seeded", server_arguments=binding, get_pool_seeds=seeded
        ).as_view()
        for answer in (lambda: get(view, ada), lambda: post(view, ada, {"title": "Hi"})):
            with pytest.raises(ImproperlyConfigured) as refused_:
                answer()
            assert str(refused_.value).startswith(
                f"Seeded.get_pool_seeds() adds {name}, which server_arguments binds or withholds"
            )

    def test_a_seed_from_the_request_reaches_the_row_s_check_and_the_service(
        self, ada: Any, note: Note
    ) -> None:
        check = SeesTenantOnTheRow()
        spec = row_spec(permissions=[check])
        view = paged(spec, get_pool_seeds=tenant_from_the_header).as_view()
        assert headed(view, ada, pk=str(note.pk)).status_code == 200
        assert headed(view, ada, "POST", {"title": "Hi"}, pk=str(note.pk)).status_code == 302
        # The view's own seeds name no tenant at all.
        assert check.tenants == ["acme", "acme"]
        assert spec.service.calls[0]["tenant"] == "acme"

    def test_a_seed_from_the_request_reaches_the_scope_check(self, ada: Any) -> None:
        class SeesTenantInScope(PermissionCheck):
            def __init__(self) -> None:
                self.tenants: list[Any] = []

            def has_permission(self, principal: Any, spec: Any) -> bool:
                return True

            def has_scope_permission(
                self, principal: Any, spec: Any, scope: Any, *, tenant: Any
            ) -> bool:
                self.tenants.append(tenant)
                return True

        check = SeesTenantInScope()
        view = paged(entry_spec(permissions=[check]), get_pool_seeds=tenant_from_the_header)
        assert headed(view.as_view(), ada).status_code == 200
        assert headed(view.as_view(), ada, "POST", {"title": "Hi"}).status_code == 302
        assert check.tenants == ["acme", "acme"]

    @pytest.mark.parametrize(
        ("attributes", "tenant"),
        [
            ({"pool_seeds": TENANT_SEEDS}, "tenant-of-ada"),
            ({"get_pool_seeds": tenant_from_the_header}, "acme"),
        ],
        ids=["pool_seeds", "get_pool_seeds"],
    )
    def test_the_class_level_check_is_asked_with_the_seed_it_names(
        self, ada: Any, attributes: dict[str, Any], tenant: str
    ) -> None:
        # Asked before anything else, with the seeds the request's call is
        # resolved with, on GET and on POST; the post's grant hands dispatch
        # the check already made, so it is asked once per request.
        check = SeesTenant()
        view = paged(entry_spec(permissions=[check]), **attributes).as_view()
        assert headed(view, ada).status_code == 200
        assert headed(view, ada, "POST", {"title": "Hi"}).status_code == 302
        assert check.tenants == [tenant, tenant]

    def test_the_hook_is_asked_once_per_request(self, ada: Any, note: Note) -> None:
        asked: list[Any] = []

        def counted(self: Any, request: Any) -> PoolSeeds:
            asked.append(request)
            return self.pool_seeds

        view = paged(row_spec(), get_pool_seeds=counted).as_view()
        get(view, ada, pk=str(note.pk))
        post(view, ada, {"title": "Hi"}, pk=str(note.pk))
        assert len(asked) == 2

    @pytest.mark.parametrize("hook", ["get_pool_seeds", "get_server_arguments"])
    def test_an_async_hook_is_refused_at_as_view(self, hook: str) -> None:
        async def asynchronous(self: Any, request: Any) -> Any: ...

        with pytest.raises(ImproperlyConfigured) as refused_:
            paged(entry_spec(), "Hooked", **{hook: asynchronous}).as_view()
        # The page is sync, so the JSON view's note on AsyncSpecView is not its.
        assert str(refused_.value) == (
            f"Hooked.{hook} is async, and the view calls it without awaiting it, so it "
            "would answer a coroutine: define it with def."
        )
        with pytest.raises(ImproperlyConfigured, match=f"SpecFormView.{hook} is async"):
            form_view(entry_spec(), **{hook: asynchronous})


class TestThePagesServerArguments:
    def test_a_bound_value_outranks_a_malformed_posted_one(self, ada: Any) -> None:
        # Malformed, so a value coerced before the binding drops it is refused
        # as the client's 400 for a name it was never offered.
        spec = entry_spec()
        view = paged(spec, server_arguments=ServerArguments(values={"count": 3})).as_view()
        assert post(view, ada, {"title": "Hi", "count": "abc"}).status_code == 302
        assert received(spec)["count"] == 3

    def test_a_bound_value_outranks_a_posted_one(self, ada: Any) -> None:
        spec = entry_spec()
        view = paged(spec, server_arguments=ServerArguments(values={"title": "S"})).as_view()
        assert post(view, ada, {"title": "client"}).status_code == 302
        assert received(spec)["title"] == "S"

    def test_a_withheld_name_is_dropped_however_malformed(self, ada: Any) -> None:
        spec = entry_spec()
        withheld = ServerArguments(withheld=frozenset({"count"}))
        view = paged(spec, server_arguments=withheld).as_view()
        assert post(view, ada, {"title": "Hi", "count": "abc"}).status_code == 302
        assert received(spec)["count"] is None

    def test_a_route_capturing_a_withheld_name_is_refused(self, ada: Any) -> None:
        withheld = ServerArguments(withheld=frozenset({"count"}))
        view = paged(entry_spec(), server_arguments=withheld).as_view()
        for answer in (
            lambda: get(view, ada, count="abc"),
            lambda: post(view, ada, {"title": "Hi"}, count="abc"),
        ):
            with pytest.raises(ImproperlyConfigured) as refused_:
                answer()
            assert str(refused_.value).startswith(
                "The route captures count, which server_arguments answers for. "
                "Drop each with route_kwargs={'count': None}"
            )

    def test_a_route_kwarg_mapped_onto_a_bound_name_is_refused_at_as_view(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused_:
            paged(
                row_spec(),
                route_kwargs={"note": "pk", "org": None},
                server_arguments=ServerArguments(values={"pk": 1}),
            ).as_view()
        assert str(refused_.value).startswith(
            "Paged.route_kwargs fills pk, which server_arguments answers for."
        )

    def test_an_allowed_name_the_server_withholds_is_refused_at_as_view(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="route_kwargs fills count, which"):
            paged(
                entry_spec(),
                route_kwargs=("count",),
                server_arguments=ServerArguments(withheld=frozenset({"count"})),
            ).as_view()

    def test_the_target_is_found_by_the_server_s_value(self, ada: Any, note: Note) -> None:
        # The route's pk is dropped and the server's finds the row, so the
        # page and dispatch see one value.
        spec = row_spec()
        view = paged(
            spec,
            route_kwargs={"pk": None},
            server_arguments=ServerArguments(values={"pk": note.pk}),
        ).as_view()
        assert get(view, ada, pk="999").context_data["form"].initial["title"] == "One"
        assert post(view, ada, {"title": "Hi", "pk": "999"}, pk="999").status_code == 302
        assert spec.service.calls[0]["instance"] == note

    def test_a_binding_that_cannot_mean_what_it_says_is_refused_at_as_view(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="ServerArguments names nope, which the"):
            paged(entry_spec(), server_arguments=ServerArguments(values={"nope": 1})).as_view()
        with pytest.raises(ImproperlyConfigured, match="ServerArguments names nope, which the"):
            form_view(entry_spec(), server_arguments=ServerArguments(values={"nope": 1}))

    def test_the_check_reads_the_view_s_declared_seeds(self) -> None:
        # ``tenant`` is free under the default seeds and a seed's under these.
        spec = entry_spec(
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=note_in_tenant,
                reads=Parameters.of(Parameter("pk", "integer"), Parameter("tenant", "string")),
            )
        )
        bound = ServerArguments(values={"tenant": "acme"})
        paged(spec, server_arguments=bound).as_view()
        with pytest.raises(ImproperlyConfigured, match="which a registered pool seed occupies"):
            paged(spec, server_arguments=bound, pool_seeds=TENANT_SEEDS).as_view()

    def test_a_view_with_no_spec_is_refused_as_one_whatever_it_binds(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused_:
            SpecFormView.as_view(
                server_arguments=ServerArguments(values={"pk": 1}), success_url="/done/"
            )
        assert str(refused_.value) == (
            "SpecFormView.as_view() needs a ServiceSpec, the write a form posts; got NoneType."
        )

    def test_a_per_call_binding_of_the_same_names_is_served(self, ada: Any, note: Note) -> None:
        def from_the_header(self: Any, request: Any) -> ServerArguments:
            return ServerArguments(values={"pk": int(request.headers["X-Note"])})

        spec = row_spec()
        view = paged(
            spec,
            server_arguments=ServerArguments(values={"pk": 0}),
            get_server_arguments=from_the_header,
        ).as_view()
        headers = {"HTTP_X_NOTE": str(note.pk)}
        page = view(signed_in(FACTORY.get("/?pk=abc", **headers), ada))
        assert page.context_data["form"].initial["title"] == "One"
        posted = view(signed_in(FACTORY.post("/", data={"title": "Hi"}, **headers), ada))
        assert posted.status_code == 302
        assert spec.service.calls[0]["instance"] == note

    @pytest.mark.parametrize(
        ("declared", "served", "described"),
        [
            (
                ServerArguments(values={"title": "S"}),
                None,
                "binds nothing and withholds nothing, where server_arguments binds title "
                "and withholds nothing.",
            ),
            (
                ServerArguments(values={"title": "S"}),
                ServerArguments(withheld=frozenset({"title"})),
                "binds nothing and withholds title, where server_arguments binds title "
                "and withholds nothing.",
            ),
            (
                ServerArguments(withheld=frozenset({"count"})),
                None,
                "binds nothing and withholds nothing, where server_arguments binds nothing "
                "and withholds count.",
            ),
            (
                None,
                ServerArguments(values={"title": "S"}, withheld=frozenset({"nope"})),
                "binds title and withholds nope, where server_arguments binds nothing "
                "and withholds nothing.",
            ),
        ],
        ids=["dropping", "withholding-a-bound-name", "releasing", "undeclared"],
    )
    def test_a_per_call_binding_of_other_names_is_refused(
        self,
        ada: Any,
        declared: ServerArguments | None,
        served: ServerArguments | None,
        described: str,
    ) -> None:
        view = paged(
            entry_spec(),
            "PerCall",
            server_arguments=declared,
            get_server_arguments=lambda self, request: served,
        ).as_view()
        for answer in (lambda: get(view, ada), lambda: post(view, ada, {"title": "Hi"})):
            with pytest.raises(ImproperlyConfigured) as refused_:
                answer()
            assert str(refused_.value).startswith(f"PerCall.get_server_arguments() {described}")

    @pytest.mark.parametrize(("ask", "mode"), [(get, "page"), (hx_get, "fragment")])
    def test_a_bound_field_renders_disabled_with_the_server_s_value(
        self, ada: Any, ask: Any, mode: str
    ) -> None:
        view = fragmented(entry_spec(), server_arguments=ServerArguments(values={"title": "S"}))
        response = ask(view, ada).render()
        assert response.context_data["form"].fields["title"].disabled
        tag = field_tag(response, "title")
        assert (b'value="S"' in tag, b"disabled" in tag) == (True, True)

    @pytest.mark.parametrize(("ask", "mode"), [(post, "page"), (hx_post, "fragment")])
    def test_a_refused_post_shows_the_server_s_value_never_the_client_s(
        self, ada: Any, ask: Any, mode: str
    ) -> None:
        # Refused on ``count``, so the form is re-rendered from the post.
        spec = entry_spec()
        view = fragmented(spec, server_arguments=ServerArguments(values={"title": "S"}))
        response = ask(view, ada, {"title": "client", "count": "x"}).render()
        assert refused(response) == {"count": [WHOLE]}
        tag = field_tag(response, "title")
        assert (b'value="S"' in tag, b"disabled" in tag) == (True, True)
        # As a value, not a substring: the CSRF token is random text.
        assert b'value="client"' not in response.content
        assert spec.service.calls == []

    @pytest.mark.parametrize(
        ("ask", "data"),
        [
            (get, None),
            (hx_get, None),
            (post, {"title": "shut", "count": "77"}),
            (hx_post, {"title": "shut", "count": "77"}),
        ],
        ids=["page-get", "fragment-get", "page-post", "fragment-post"],
    )
    def test_a_withheld_field_renders_disabled_with_no_value(
        self, ada: Any, ask: Any, data: Any
    ) -> None:
        view = fragmented(
            entry_spec(), server_arguments=ServerArguments(withheld=frozenset({"count"}))
        )
        response = (ask(view, ada) if data is None else ask(view, ada, data)).render()
        tag = field_tag(response, "count")
        assert (b"value=" in tag, b"disabled" in tag) == (False, True)
        assert b'value="77"' not in response.content

    def test_the_field_shows_the_per_call_value_not_the_declared_one(self, ada: Any) -> None:
        view = fragmented(
            entry_spec(),
            server_arguments=ServerArguments(values={"title": "S"}),
            get_server_arguments=lambda self, request: ServerArguments(values={"title": "T"}),
        )
        for response in (get(view, ada), post(view, ada, {"title": "client", "count": "x"})):
            assert b'value="T"' in field_tag(response.render(), "title")

    def test_the_form_class_is_left_as_it_was(self, ada: Any) -> None:
        view = paged(entry_spec(), server_arguments=ServerArguments(values={"title": "S"}))
        get(view.as_view(), ada)
        assert not EntryForm.base_fields["title"].disabled
        assert not get(form_view(entry_spec()), ada).context_data["form"].fields["title"].disabled

    def test_an_empty_binding_and_none_stand_for_each_other(self, ada: Any) -> None:
        view = paged(
            entry_spec(),
            server_arguments=ServerArguments(),
            get_server_arguments=lambda self, request: None,
        ).as_view()
        assert get(view, ada).status_code == 200
        assert post(view, ada, {"title": "Hi"}).status_code == 302


CLOSED_TODAY = Affordance(code="closed", reason="Entries are closed today.", when=lambda: False)
"""A callable condition, unmet whatever the call: the operation is unavailable now."""

UNTITLED = Affordance(
    code="titled", reason="Only an untitled note is edited here.", when=Q(title="")
)
"""A condition on the row, unmet for any note with a title."""

OPEN_TODAY = Affordance(code="open", reason="Entries are open.", when=lambda: True)


def note_in_tenant(*, pk: int, tenant: str = "") -> Any:
    return Note.objects.filter(pk=pk)


def open_for(*, tenant: str) -> bool:
    return tenant != "acme"


def unavailable_of(response: Any) -> Any:
    return response.context_data.get("unavailable", "absent")


class TestUnavailableOnGet:
    """The page asks the kernel, before anyone posts, whether the operation is available."""

    def test_an_unmet_condition_is_in_the_page_s_context(self, ada: Any) -> None:
        view = form_view(entry_spec(affordances=[CLOSED_TODAY]))
        response = get(view, ada)
        assert response.status_code == 200
        unavailable = response.context_data["unavailable"]
        assert (unavailable.code, unavailable.reason) == ("closed", "Entries are closed today.")

    def test_an_unmet_condition_is_in_the_fragment_s_context(self, ada: Any) -> None:
        response = hx_get(fragmented(entry_spec(affordances=[CLOSED_TODAY])), ada)
        assert response.template_name == [FRAGMENT]
        assert response.context_data["unavailable"] is CLOSED_TODAY

    @pytest.mark.parametrize("ask", [get, hx_get], ids=["page", "fragment"])
    def test_a_condition_on_the_row_is_asked_of_the_row(
        self, ada: Any, note: Note, ask: Any
    ) -> None:
        view = fragmented(row_spec(affordances=[OPEN_TODAY, UNTITLED]))
        assert ask(view, ada, pk=str(note.pk)).context_data["unavailable"] is UNTITLED

    @pytest.mark.parametrize("ask", [get, hx_get], ids=["page", "fragment"])
    def test_every_condition_met_leaves_it_out(self, ada: Any, note: Note, ask: Any) -> None:
        note.title = ""
        note.save()
        view = fragmented(row_spec(affordances=[OPEN_TODAY, UNTITLED]))
        response = ask(view, ada, pk=str(note.pk))
        assert unavailable_of(response) == "absent"

    def test_a_callable_condition_is_named_before_a_row_condition_declared_first(
        self, ada: Any, note: Note
    ) -> None:
        # The callable conditions are asked first, with no row, so the page
        # names one where the post, asking in declaration order, names the
        # row's: both are unmet, and the post's 409 stays the authority.
        view = form_view(row_spec(affordances=[UNTITLED, CLOSED_TODAY]))
        assert get(view, ada, pk=str(note.pk)).context_data["unavailable"] is CLOSED_TODAY
        posted = post(view, ada, {"title": "Hi"}, pk=str(note.pk))
        assert (posted.status_code, posted.context_data["code"]) == (409, "titled")

    def test_no_row_asks_no_row_condition(self, ada: Any) -> None:
        # A missing row ``allow_none`` lets through is ``None``, which the
        # kernel refuses to ask a row condition about.
        spec = entry_spec(
            affordances=[UNTITLED],
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=note_by_pk, reads=PK, allow_none=True
            ),
        )
        assert unavailable_of(get(form_view(spec), ada, pk=404)) == "absent"

    def test_a_create_asks_no_row_condition(self, ada: Any) -> None:
        # A create resolves ``None`` too: no row to ask about.
        response = get(form_view(entry_spec(affordances=[UNTITLED])), ada)
        assert unavailable_of(response) == "absent"

    def test_a_row_gone_before_its_condition_is_asked_is_404(self, ada: Any, note: Note) -> None:
        class Deletes(PermissionCheck):
            def has_permission(self, principal: Any, spec: Any) -> bool:
                return True

            def has_object_permission(self, principal: Any, spec: Any, obj: Any) -> bool:
                Note.objects.filter(pk=obj.pk).delete()
                return True

        view = form_view(row_spec(permissions=[Deletes()], affordances=[UNTITLED]))
        with pytest.raises(Http404):
            get(view, ada, pk=str(note.pk))

    def test_a_condition_reading_a_per_call_seed_is_asked_with_it(self, ada: Any) -> None:
        when = Affordance(code="tenant", reason="Not for this tenant.", when=open_for)
        view = paged(entry_spec(affordances=[when]), get_pool_seeds=tenant_from_the_header)
        assert headed(view.as_view(), ada).context_data["unavailable"] is when

    def test_the_row_s_conditions_are_asked_with_the_call_s_seeds(self, ada: Any) -> None:
        # Both met, so the walk reaches ``enforce_affordances``, which asks
        # the callable again: without the call's reserved seeds it is never
        # handed ``tenant``.
        note = Note.objects.create(owner=ada, title="")
        when = Affordance(code="tenant", reason="Not for this tenant.", when=open_for)

        def globex(self: Any, request: Any) -> PoolSeeds:
            return self.pool_seeds.extend(tenant=lambda: "globex")

        view = paged(row_spec(affordances=[when, UNTITLED]), get_pool_seeds=globex).as_view()
        assert unavailable_of(get(view, ada, pk=str(note.pk))) == "absent"

    def test_on_a_row_each_condition_and_each_seed_is_asked_twice(self, ada: Any) -> None:
        # Once alone, then with the row's conditions; each seed once for the
        # lookup's pool and once for the affordances'.
        note = Note.objects.create(owner=ada, title="")
        asked: list[str] = []

        def counted(*, tenant: str) -> bool:
            asked.append("condition")
            return True

        def seeded(self: Any, request: Any) -> PoolSeeds:
            return self.pool_seeds.extend(tenant=lambda: asked.append("seed") or "globex")

        when = Affordance(code="counted", reason="Counted.", when=counted)
        view = paged(row_spec(affordances=[when, UNTITLED]), get_pool_seeds=seeded).as_view()
        assert unavailable_of(get(view, ada, pk=str(note.pk))) == "absent"
        assert (asked.count("condition"), asked.count("seed")) == (2, 2)

    def test_the_page_no_longer_offers_what_the_post_refuses(self, ada: Any) -> None:
        # Served before, the page offered the form with no reason, and the
        # post then answered 409.
        view = form_view(entry_spec(affordances=[CLOSED_TODAY]), template_name=AVAILABLE)
        page = get(view, ada).render()
        assert page.status_code == 200
        assert b'<p class="unavailable" data-code="closed">Entries are closed today.</p>' in (
            page.content
        )
        assert b"<fieldset disabled>" in page.content
        refused_ = post(view, ada, {"title": "Hi"})
        assert (refused_.status_code, refused(refused_)) == (
            409,
            {"__all__": ["Entries are closed today."]},
        )


AVAILABLE = "spec_form_available.html"


class TestTheRefusalsCode:
    @pytest.mark.parametrize("ask", [post, hx_post], ids=["page", "fragment"])
    def test_an_unmet_condition_still_refuses_the_post_with_its_code(
        self, ada: Any, ask: Any
    ) -> None:
        spec = entry_spec(affordances=[CLOSED_TODAY])
        response = ask(fragmented(spec), ada, {"title": "Hi"})
        assert response.status_code == (409 if ask is post else 200)
        assert response.context_data["code"] == "closed"
        assert spec.service.calls == []

    @pytest.mark.parametrize("ask", [post, hx_post], ids=["page", "fragment"])
    def test_a_service_s_action_unavailable_carries_its_code(self, ada: Any, ask: Any) -> None:
        view = fragmented(raising(ActionUnavailable("Moved.", code="moved")))
        assert ask(view, ada, {"title": "Hi"}).context_data["code"] == "moved"

    @pytest.mark.parametrize(
        "exc",
        [ServiceConflict("Moved."), ServiceError("Moved."), None],
        ids=["conflict", "service-error", "invalid"],
    )
    def test_a_refusal_with_no_code_leaves_it_out(self, ada: Any, exc: Any) -> None:
        spec = entry_spec() if exc is None else raising(exc)
        response = post(form_view(spec), ada, {"title": "shut" if exc is None else "Hi"})
        assert response.status_code in (400, 409, 422)
        assert "code" not in response.context_data


class OwnersNoteForm(forms.ModelForm):
    """A note's title, and an assignee among the users its constructor is told to offer."""

    assignee = forms.ModelChoiceField(queryset=get_user_model().objects.none(), required=False)

    class Meta:
        model = Note
        fields = ["title"]

    def __init__(self, *args: Any, owner: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["assignee"].queryset = get_user_model().objects.filter(pk=owner.pk)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if cleaned.get("title") == "shut":
            raise forms.ValidationError(CLOSED)
        return cleaned


class OwnersEntryForm(EntryForm):
    """``EntryForm``, its ``count`` capped by a keyword only its constructor is handed."""

    def __init__(self, *args: Any, cap: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["count"].max_value = cap
        self.fields["count"].validators.append(MaxValueValidator(cap))


class Keywords:
    """A ``form_kwargs`` recording what each call is handed, the call's context by identity.

    It names a seed, so the view must resolve it, and ``instance`` with a
    default, so a create, whose pool has none, is asked too.
    """

    def __init__(self, returns: Callable[[Any], Mapping[str, Any]]) -> None:
        self.returns = returns
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, *, user: Any, tenant: Any, call_context: Any, instance: Any = None
    ) -> Mapping[str, Any]:
        self.calls.append(
            {"user": user, "tenant": tenant, "instance": instance, "context": call_context}
        )
        return self.returns(user)


def capped(user: Any) -> Mapping[str, Any]:
    return {"cap": 3}


def owned(user: Any) -> Mapping[str, Any]:
    return {"owner": user}


class ContextService:
    """A service declaring the call's context by name, recording the very dict it is handed."""

    def __init__(self) -> None:
        self.contexts: list[Any] = []

    def __call__(self, *, call_context: Any) -> None:
        self.contexts.append(call_context)


def keyed_entry_spec(keywords: Keywords, *, row: bool) -> ServiceSpec:
    """``entry_spec``, or ``row_spec``, validated by ``OwnersEntryForm`` through ``keywords``."""
    validator = FormValidator(OwnersEntryForm, form_kwargs=keywords)
    return row_spec(validator=validator) if row else entry_spec(validator=validator)


def keyed_note_spec(keywords: Keywords, service: Any = None) -> ServiceSpec:
    """An update of the note the route names, validated by ``OwnersNoteForm`` through ``keywords``."""
    return ServiceSpec(
        service=service or Record(),
        permissions=OPEN,
        validator=FormValidator(OwnersNoteForm, form_kwargs=keywords),
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=note_by_pk, reads=PK
        ),
    )


class TestTheFormsKeywords:
    """The page's form is built with what the Validator's ``form_kwargs`` answers, as dispatch's is."""

    @pytest.mark.parametrize("row", [False, True], ids=["a-create", "a-plain-form-on-a-row"])
    def test_a_form_whose_init_needs_a_keyword_is_served(
        self, ada: Any, note: Note, row: bool
    ) -> None:
        view = form_view(keyed_entry_spec(Keywords(capped), row=row), pool_seeds=TENANT_SEEDS)
        route = {"pk": note.pk} if row else {}
        page = get(view, ada, **route)
        assert page.status_code == 200
        assert page.context_data["form"].fields["count"].max_value == 3
        # The cap only the keyword sets refuses the post, once, in its words.
        refusal = post(view, ada, {"title": "Hi", "count": "4"}, **route)
        assert (refusal.status_code, refused(refusal)) == (
            400,
            {"count": ["Ensure this value is less than or equal to 3."]},
        )
        assert post(view, ada, {"title": "Hi", "count": "2"}, **route).status_code == 302

    def test_a_model_form_whose_init_needs_a_keyword_is_served(self, ada: Any, note: Note) -> None:
        other = make_user("grace")
        view = form_view(keyed_note_spec(Keywords(owned)), pool_seeds=TENANT_SEEDS)
        page = get(view, ada, pk=note.pk)
        assert page.status_code == 200
        assert list(page.context_data["form"].fields["assignee"].queryset) == [ada]
        refusal = post(view, ada, {"title": "Hi", "assignee": other.pk}, pk=note.pk)
        assert refusal.status_code == 400
        assert list(refused(refusal)) == ["assignee"]
        ok = post(view, ada, {"title": "Hi", "assignee": ada.pk}, pk=note.pk)
        assert ok.status_code == 302

    def test_the_page_and_the_validator_are_handed_the_same_keywords(
        self, ada: Any, note: Note
    ) -> None:
        keywords = Keywords(owned)
        service = ContextService()
        view = form_view(keyed_note_spec(keywords, service), pool_seeds=TENANT_SEEDS)
        page = get(view, ada, pk=note.pk)
        # A post the Validator's clean() refuses, then one it takes: each asks
        # ``form_kwargs`` twice, the page's form first and the Validator's after.
        assert post(view, ada, {"title": "shut"}, pk=note.pk).status_code == 400
        assert post(view, ada, {"title": "Hi"}, pk=note.pk).status_code == 302
        calls = keywords.calls
        assert len(calls) == 5
        assert {(each["user"], each["tenant"], each["instance"]) for each in calls} == {
            (ada, "tenant-of-ada", note)
        }
        # The row itself on both sides, never the copy the page's ModelForm edits.
        assert calls[1]["instance"] is calls[2]["instance"]
        assert calls[3]["instance"] is calls[4]["instance"]
        assert page.context_data["form"].instance is not calls[0]["instance"]
        # One call context per request, the one dispatch is handed too.
        assert calls[1]["context"] is calls[2]["context"]
        assert calls[3]["context"] is calls[4]["context"] is service.contexts[0]
        assert calls[1]["context"] is not calls[3]["context"]

    def test_a_post_whose_route_is_refused_is_built_as_no_row(self, ada: Any, note: Note) -> None:
        # The target is never found, so the page's form is asked about none,
        # rather than the marker of a target not yet looked for.
        keywords = Keywords(owned)
        view = form_view(keyed_note_spec(keywords), pool_seeds=TENANT_SEEDS)
        response = post(view, ada, {"title": "Hi"}, pk="abc")
        assert (response.status_code, refused(response)) == (400, {"__all__": [f"pk: {WHOLE}"]})
        assert [each["instance"] for each in keywords.calls] == [None]

    def test_an_initial_form_kwargs_answers_sits_under_the_row_s_values(
        self, ada: Any, note: Note
    ) -> None:
        # A plain form on a row takes the row's values as its ``initial``, so
        # one ``form_kwargs`` answers too is merged under them rather than
        # passed twice: the disabled field the row never fills keeps it.
        def initial(user: Any) -> Mapping[str, Any]:
            return {"cap": 3, "initial": {"code": "K-9", "title": "Not the row's"}}

        view = form_view(keyed_entry_spec(Keywords(initial), row=True), pool_seeds=TENANT_SEEDS)
        page = get(view, ada, pk=note.pk)
        assert page.context_data["form"].initial == {"code": "K-9", "title": "One"}


def saved(*, instance: Any, data: dict[str, Any]) -> Any:
    """An update writing what the Validator returned, and nothing it did not, onto the row."""
    return update_from_input(instance, data)


def partial_user_spec() -> ServiceSpec:
    """``user_spec`` as a partial update, saving what was sent onto the user."""
    return ServiceSpec(
        service=saved,
        permissions=OPEN,
        validator=FormValidator(UserForm),
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=user_by_pk, reads=PK
        ),
        partial=True,
    )


def partial_spec() -> ServiceSpec:
    """A partial write validated by ``NoteTitleForm``, its title required on a whole call.

    No checkbox or multi-select: the browser posts those whatever is ticked,
    so neither is ever a field the post leaves out.
    """
    return entry_spec(validator=Watched(NoteTitleForm), partial=True)


class TestAPartialPage:
    """A ``partial`` spec's page: every field optional to the browser, and a blank one left alone."""

    def test_a_partial_page_renders_no_required_attribute(self, ada: Any) -> None:
        response = get(form_view(partial_spec()), ada).render()
        assert b"required" not in field_tag(response, "title")
        # The class is left as it was: only this form's own attribute changes.
        assert NoteTitleForm.use_required_attribute is True

    def test_a_whole_page_still_renders_it(self, ada: Any) -> None:
        response = get(form_view(entry_spec(validator=Watched(NoteTitleForm))), ada).render()
        assert b"required" in field_tag(response, "title")

    def test_a_refused_partial_post_renders_no_required_attribute_either(self, ada: Any) -> None:
        def busy(**pool: Any) -> None:
            raise ServiceConflict("Busy.")

        spec = ServiceSpec(
            service=busy, permissions=OPEN, validator=Watched(NoteTitleForm), partial=True
        )
        response = post(form_view(spec), ada, {"remark": "Later"}).render()
        assert response.status_code == 409
        assert b"required" not in field_tag(response, "title")

    def test_a_subset_is_posted_and_only_it_is_validated(self, ada: Any) -> None:
        spec = partial_spec()
        assert post(form_view(spec), ada, {"remark": "Later"}).status_code == 302
        assert received(spec) == {"remark": "Later"}

    def test_a_blank_field_leaves_the_row_s_value_as_it_was(self, ada: Any) -> None:
        ada.first_name = "Ada"
        ada.save()
        response = post(
            form_view(partial_user_spec()),
            ada,
            {"username": "countess", "first_name": ""},
            pk=ada.pk,
        )
        assert response.status_code == 302
        ada.refresh_from_db()
        assert (ada.username, ada.first_name) == ("countess", "Ada")

    def test_a_post_with_every_field_blank_dispatches(self, ada: Any) -> None:
        spec = partial_spec()
        response = post(form_view(spec), ada, {"title": "", "remark": "", "save": "Save"})
        assert response.status_code == 302
        assert received(spec) == {}

    def test_a_shape_refusal_shows_no_word_about_a_field_left_blank(self, ada: Any) -> None:
        # A blank field on a partial page is one the post leaves alone, so
        # nothing beside the refusal says it is required.
        spec = entry_spec(partial=True)
        response = post(form_view(spec), ada, {"title": "", "count": "x"})
        assert (response.status_code, refused(response)) == (400, {"count": [WHOLE]})
        assert validated(spec) == 0

    def test_a_checkbox_and_a_multi_select_are_always_sent(self, ada: Any) -> None:
        # The browser posts neither when unticked or empty, and the form reads
        # each as a value all the same, so neither is a field left out.
        spec = entry_spec(partial=True)
        assert post(form_view(spec), ada, {}).status_code == 302
        assert received(spec) == {"urgent": False, "tags": []}

    def test_a_ticked_clear_checkbox_clears_the_file(self, ada: Any) -> None:
        spec = entry_spec(validator=Watched(CoverForm), partial=True)
        response = post(form_view(spec), ada, {"cover-clear": "on"})
        assert response.status_code == 302
        assert received(spec) == {"cover": False}


class TestASeedResolvedAtEachStep:
    """A ``Grant`` carries no values, so each step naming a seed resolves it again."""

    @pytest.mark.parametrize(
        ("form_kwargs", "on_get", "on_post"),
        [
            (None, 3, 3),
            (lambda *, tenant: {}, 4, 4),
        ],
        ids=["the-check-names-it", "the-check-and-the-form-name-it"],
    )
    def test_a_named_seed_is_resolved_at_each_step_that_names_it(
        self, ada: Any, note: Note, form_kwargs: Any, on_get: int, on_post: int
    ) -> None:
        # GET: the class-level check, the target's lookup, the affordances'
        # pool and the form's keywords; POST: the check, the lookup, the
        # form's keywords and dispatch. A resolver answering a fresh object
        # answers each step a different one.
        resolved: list[object] = []

        def fresh() -> object:
            resolved.append(object())
            return resolved[-1]

        spec = row_spec(
            permissions=[SeesTenant()],
            validator=FormValidator(NoteTitleForm, form_kwargs=form_kwargs),
        )
        view = form_view(spec, pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=fresh))
        assert get(view, ada, pk=note.pk).status_code == 200
        assert len(resolved) == on_get
        resolved.clear()
        assert post(view, ada, {"title": "Hi"}, pk=note.pk).status_code == 302
        assert len(resolved) == on_post
        assert len({id(each) for each in resolved}) == on_post


class SeesTheContext(PermissionCheck):
    """Admits every principal and row, recording the call context each question is handed."""

    def __init__(self) -> None:
        self.contexts: list[tuple[str, Any]] = []

    def has_permission(self, principal: Any, spec: Any, *, call_context: Any) -> bool:
        self.contexts.append(("class", call_context))
        return True

    def has_object_permission(
        self, principal: Any, spec: Any, obj: Any, *, call_context: Any
    ) -> bool:
        self.contexts.append(("object", call_context))
        return True


class TestOneCallContextPerRequest:
    """Every kernel call the page makes for a request is handed the same dict."""

    def test_the_checks_the_affordances_the_form_and_the_service_share_one_dict(
        self, ada: Any, note: Note
    ) -> None:
        check = SeesTheContext()
        asked: list[tuple[str, Any]] = []

        def open_now(*, call_context: Any) -> bool:
            asked.append(("affordance", call_context))
            return True

        def keywords(*, call_context: Any) -> Mapping[str, Any]:
            asked.append(("form", call_context))
            return {}

        service = ContextService()
        spec = row_spec(
            permissions=[check],
            validator=FormValidator(NoteTitleForm, form_kwargs=keywords),
            affordances=[Affordance(code="open", reason="Open.", when=open_now)],
        )
        spec = dataclasses.replace(spec, service=service)
        view = form_view(spec)
        assert get(view, ada, pk=note.pk).status_code == 200
        on_get = check.contexts + asked
        # The class-level check, the row's check, both affordance questions
        # and the form's keywords.
        assert [name for name, _ in on_get] == [
            "class",
            "object",
            "affordance",
            "affordance",
            "form",
        ]
        assert len({id(context) for _, context in on_get}) == 1
        check.contexts.clear()
        asked.clear()
        assert post(view, ada, {"title": "Hi"}, pk=note.pk).status_code == 302
        on_post = [*check.contexts, *asked, ("service", service.contexts[0])]
        assert {name for name, _ in on_post} == {"class", "object", "form", "affordance", "service"}
        assert len({id(context) for _, context in on_post}) == 1
        assert on_get[0][1] is not on_post[0][1]


@dataclasses.dataclass
class NoteOut:
    """A note as the page names it: its title the label, its key a handle, its owner plumbing."""

    pk: Annotated[int, FieldMarking.handle()]
    title: Annotated[str, FieldMarking.label()]
    archived: bool
    owner_id: Annotated[int, FieldMarking.hidden()]


@dataclasses.dataclass
class AuthorOut:
    pk: Annotated[int, FieldMarking.handle()]
    name: str


@dataclasses.dataclass
class BookOut:
    """A book whose author is a nested object, with a handle of its own."""

    title: Annotated[str, FieldMarking.label()]
    author: AuthorOut


@dataclasses.dataclass
class Ref:
    pk: Annotated[int, FieldMarking.handle()]
    name: str


@dataclasses.dataclass
class Shelf:
    """A row that is no model's, holding an array of objects and an object that may be null."""

    name: Annotated[str, FieldMarking.label()]
    books: list[Ref]
    lead: Ref | None


@dataclasses.dataclass
class UnmarkedBookOut:
    """An Output with choices whose displays differ from their values, and no marking."""

    title: str
    status: Status


class Shelved(PermissionCheck):
    """Admits every principal and every set, recording each set the scope check is asked about."""

    def __init__(self, *, admits: bool = True) -> None:
        self.sets: list[Any] = []
        self.admits = admits

    message = "No set may be shelved here."

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return True

    def has_scope_permission(self, principal: Any, spec: Any, target: Any) -> bool:
        self.sets.append(target)
        return self.admits


def notes_of(principal: Any) -> Any:
    return Note.objects.filter(owner=principal).order_by("pk")


def set_spec(selector: Any = None, **kwargs: Any) -> ServiceSpec:
    """``entry_spec`` on the set of notes ``selector`` resolves: the signed-in principal's by default."""
    return entry_spec(
        collection_selector_spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=selector or (lambda *, user: notes_of(user)),
        ),
        **kwargs,
    )


def notes(owner: Any, count: int) -> list[Note]:
    return [Note.objects.create(owner=owner, title=f"Note {each}") for each in range(count)]


class TestTheTargetInTheContext:
    """The row or set the operation acts on is in the context, and nothing for a create."""

    def test_an_update_page_carries_its_row(self, ada: Any) -> None:
        note = Note.objects.create(owner=ada, title="Ada's")
        context = get(form_view(row_spec()), ada, pk=note.pk).context_data
        assert isinstance(context["target"], Note)
        assert context["target"].pk == note.pk

    def test_a_create_carries_no_target(self, ada: Any) -> None:
        context = get(form_view(entry_spec()), ada).context_data
        assert "target" not in context
        assert "target_count" not in context
        assert "target_counted_at" not in context

    def test_a_missing_row_allow_none_lets_through_carries_no_target(self, ada: Any) -> None:
        spec = entry_spec(
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=note_by_pk, reads=PK, allow_none=True
            )
        )
        assert "target" not in get(form_view(spec), ada, pk=404).context_data

    def test_a_refused_post_carries_its_row(self, ada: Any) -> None:
        note = Note.objects.create(owner=ada, title="Ada's")
        response = post(form_view(row_spec()), ada, {"title": "far too long"}, pk=note.pk)
        assert response.status_code == 400
        assert response.context_data["target"].pk == note.pk

    def test_a_post_whose_route_is_refused_carries_no_target(self, ada: Any) -> None:
        # The route never found a row, so there is none to show beside the
        # refusal, and the marker of one not looked for is never handed on.
        response = post(form_view(row_spec()), ada, {"title": "Hi"}, pk="first")
        assert response.status_code == 400
        assert "target" not in response.context_data

    def test_the_fragment_carries_the_target_too(self, ada: Any) -> None:
        note = Note.objects.create(owner=ada, title="Ada's")
        view = form_view(row_spec(), fragment_template_name=TEMPLATE)
        request = signed_in(FACTORY.get("/", headers={"HX-Request": "true"}), ada)
        assert view(request, pk=note.pk).context_data["target"].pk == note.pk


class TestABulkPage:
    """A ``collection_selector_spec``'s page shows how many rows its set holds, and when it counted."""

    def test_the_set_and_its_count_and_when_it_was_counted(self, ada: Any) -> None:
        notes(ada, 3)
        notes(make_user("bob"), 2)
        before = timezone.now()
        context = get(form_view(set_spec()), ada).context_data
        after = timezone.now()
        assert [note.title for note in context["target"]] == ["Note 0", "Note 1", "Note 2"]
        assert context["target_count"] == 3
        assert before <= context["target_counted_at"] <= after

    def test_an_empty_set_is_no_refusal(self, ada: Any) -> None:
        check = Shelved()
        response = get(form_view(set_spec(permissions=[check])), ada)
        assert response.status_code == 200
        assert response.context_data["target_count"] == 0
        assert [list(each) for each in check.sets] == [[]]

    def test_an_empty_set_is_still_asked_the_scope_check(self, ada: Any) -> None:
        spec = set_spec(permissions=[Shelved(admits=False)])
        with pytest.raises(PermissionDenied, match=r"^No set may be shelved here\.$"):
            get(form_view(spec), ada)

    def test_it_counts_without_loading_a_row(self, ada: Any) -> None:
        notes(ada, 12)
        view = form_view(set_spec())
        with CaptureQueriesContext(connection) as queries:
            context = get(view, ada).context_data
        assert context["target_count"] == 12
        # One query, a COUNT, and the set handed to the template still unread.
        assert len(queries) == 1
        assert "COUNT(" in queries[0]["sql"].upper()
        assert context["target"]._result_cache is None

    def test_a_set_answered_as_a_list_is_counted_as_one(self, ada: Any) -> None:
        notes(ada, 2)
        spec = set_spec(selector=lambda *, user: list(notes_of(user)))
        context = get(form_view(spec), ada).context_data
        assert context["target_count"] == 2

    def test_a_refused_post_counts_again(self, ada: Any) -> None:
        notes(ada, 2)
        view = form_view(set_spec())
        assert get(view, ada).context_data["target_count"] == 2
        notes(ada, 1)
        before = timezone.now()
        response = post(view, ada, {"title": "far too long"})
        assert response.status_code == 400
        assert response.context_data["target_count"] == 3
        assert response.context_data["target_counted_at"] >= before

    def test_a_refused_post_counts_the_set_as_it_is_now_not_as_the_service_read_it(
        self, ada: Any
    ) -> None:
        # The service reads the set it was handed, filling its result cache,
        # then changes what the set holds before it refuses. Not atomic, so
        # the change stays, and the page counts the set as it is now rather
        # than reading the cache the service filled.
        notes(ada, 2)

        def service(*, collection: Any, **pool: Any) -> None:
            assert len(collection) == 2
            Note.objects.create(owner=collection[0].owner, title="Added")
            raise ServiceError("Not now.")

        spec = dataclasses.replace(set_spec(atomic=False), service=service)
        response = post(form_view(spec), ada, {"title": "Hi"})
        assert response.status_code == 422
        assert response.context_data["target_count"] == 3

    def test_a_successful_post_counts_nothing(self, ada: Any) -> None:
        notes(ada, 2)
        view = form_view(set_spec())
        with CaptureQueriesContext(connection) as queries:
            assert post(view, ada, {"title": "Hi"}).status_code == 302
        assert not [query for query in queries if "COUNT(" in query["sql"].upper()]

    def test_an_instance_page_carries_no_count(self, ada: Any) -> None:
        note = Note.objects.create(owner=ada, title="Ada's")
        context = get(form_view(row_spec()), ada, pk=note.pk).context_data
        assert "target_count" not in context
        assert "target_counted_at" not in context


class TestTheTargetDisplay:
    """The target projected for a person through the Output's ``FieldMarking``s."""

    def test_a_row_is_named_by_its_label_with_no_handle_and_nothing_hidden(self, ada: Any) -> None:
        note = Note.objects.create(owner=ada, title="Ada's")
        spec = row_spec(presenter=DataclassPresenter(NoteOut))
        context = get(form_view(spec), ada, pk=note.pk).context_data
        assert context["target_display"] == {
            "label": "Ada's",
            "fields": {"title": "Ada's", "archived": False},
        }
        # The raw row stays beside it.
        assert context["target"].pk == note.pk

    def test_a_nested_handle_is_never_shown(self, ada: Any) -> None:
        author = BookAuthor.objects.create(name="Le Guin")
        book = Book.objects.create(author=author, title="Lathe", price=Decimal("9.50"))
        spec = entry_spec(
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda *, pk: Book.objects.filter(pk=pk),
                reads=PK,
            ),
            presenter=DataclassPresenter(BookOut),
        )
        display = get(form_view(spec), ada, pk=book.pk).context_data["target_display"]
        assert display == {
            "label": "Lathe",
            "fields": {"title": "Lathe", "author": {"name": "Le Guin"}},
        }

    def test_a_row_with_no_label_has_none_in_its_display(self, ada: Any) -> None:
        @dataclasses.dataclass
        class Untitled:
            pk: Annotated[int, FieldMarking.handle()]
            title: str

        note = Note.objects.create(owner=ada, title="Ada's")
        spec = row_spec(presenter=DataclassPresenter(Untitled))
        display = get(form_view(spec), ada, pk=note.pk).context_data["target_display"]
        assert display == {"fields": {"title": "Ada's"}}

    def test_each_row_of_a_set_is_projected_up_to_the_bound(self, ada: Any) -> None:
        notes(ada, 5)
        view = form_view(set_spec(presenter=DataclassPresenter(NoteOut)), target_display_limit=2)
        with CaptureQueriesContext(connection) as queries:
            context = get(view, ada).context_data
        assert context["target_count"] == 5
        assert [row["label"] for row in context["target_display"]] == ["Note 0", "Note 1"]
        assert context["target_display"][0] == {
            "label": "Note 0",
            "fields": {"title": "Note 0", "archived": False},
        }
        # The count, then the bounded rows in one query: never the whole set.
        assert len(queries) == 2
        assert "COUNT(" in queries[0]["sql"].upper()
        assert "LIMIT 2" in queries[1]["sql"].upper()

    def test_the_default_bound(self, ada: Any) -> None:
        assert SpecFormView.target_display_limit == 10
        notes(ada, SpecFormView.target_display_limit + 1)
        view = form_view(set_spec(presenter=DataclassPresenter(NoteOut)))
        context = get(view, ada).context_data
        assert len(context["target_display"]) == SpecFormView.target_display_limit
        assert context["target_count"] == SpecFormView.target_display_limit + 1

    def test_a_set_answered_as_a_list_is_projected_up_to_the_bound(self, ada: Any) -> None:
        notes(ada, 3)
        spec = set_spec(
            selector=lambda *, user: list(notes_of(user)), presenter=DataclassPresenter(NoteOut)
        )
        context = get(form_view(spec, target_display_limit=2), ada).context_data
        assert [row["label"] for row in context["target_display"]] == ["Note 0", "Note 1"]

    def test_an_empty_set_displays_no_row(self, ada: Any) -> None:
        context = get(form_view(set_spec(presenter=DataclassPresenter(NoteOut))), ada).context_data
        assert context["target_display"] == []
        assert context["target_count"] == 0

    def test_the_rows_are_presented_in_one_call(self, ada: Any) -> None:
        notes(ada, 3)
        presenter = DataclassPresenter(NoteOut)
        calls: list[int] = []
        presented_rows = presenter.presented_rows

        def counted(rows: Any, context: Any) -> Any:
            calls.append(len(rows))
            return presented_rows(rows, context)

        presenter.presented_rows = counted
        get(form_view(set_spec(presenter=presenter)), ada)
        assert calls == [3]

    def test_a_spec_with_no_output_has_no_display(self, ada: Any) -> None:
        note = Note.objects.create(owner=ada, title="Ada's")
        assert "target_display" not in get(form_view(row_spec()), ada, pk=note.pk).context_data
        assert "target_display" not in get(form_view(set_spec()), ada).context_data

    def test_an_output_marking_nothing_has_no_display(self, ada: Any) -> None:
        # Its choices alone would be spoken for an agent; a person's display
        # is driven by the markings, and this output declares none.
        author = BookAuthor.objects.create(name="Le Guin")
        book = Book.objects.create(author=author, title="Lathe", price=Decimal("9.50"))
        spec = entry_spec(
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda *, pk: Book.objects.filter(pk=pk),
                reads=PK,
            ),
            presenter=DataclassPresenter(UnmarkedBookOut),
        )
        assert "target_display" not in get(form_view(spec), ada, pk=book.pk).context_data

    def test_an_output_selector_s_presenter_drives_it(self, ada: Any) -> None:
        note = Note.objects.create(owner=ada, title="Ada's")
        spec = row_spec(
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda *, result: result,
                presenter=DataclassPresenter(NoteOut),
            )
        )
        display = get(form_view(spec), ada, pk=note.pk).context_data["target_display"]
        assert display["label"] == "Ada's"

    def test_an_output_selector_declaring_affordances_still_renders(self, ada: Any) -> None:
        # The kernel's ``present`` adds each row's answers, which only a row
        # read through that selector carries; the target was read through the
        # target selector, so the page presents it without them.
        note = Note.objects.create(owner=ada, title="Ada's")
        archivable = ServiceSpec(
            service=Record(),
            permissions=OPEN,
            affordances=[Affordance(code="live", reason="Live.", when=Q(archived=False))],
        )
        spec = row_spec(
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda *, result: result,
                presenter=DataclassPresenter(NoteOut),
                affordances={"archive": archivable},
            )
        )
        response = get(form_view(spec), ada, pk=note.pk)
        assert response.status_code == 200
        assert response.context_data["target_display"]["label"] == "Ada's"

    def test_a_refused_post_carries_the_display(self, ada: Any) -> None:
        note = Note.objects.create(owner=ada, title="Ada's")
        spec = row_spec(presenter=DataclassPresenter(NoteOut))
        response = post(form_view(spec), ada, {"title": "far too long"}, pk=note.pk)
        assert response.status_code == 400
        assert response.context_data["target_display"]["label"] == "Ada's"

    def test_a_handle_inside_an_array_and_beside_a_null_is_never_shown(self, ada: Any) -> None:
        shelf = Shelf(name="Poetry", books=[Ref(1, "Lathe"), Ref(2, "Earthsea")], lead=None)
        spec = entry_spec(
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=lambda *, pk: shelf, reads=PK
            ),
            presenter=DataclassPresenter(Shelf),
        )
        display = get(form_view(spec), ada, pk=1).context_data["target_display"]
        assert display == {
            "label": "Poetry",
            "fields": {
                "name": "Poetry",
                "books": [{"name": "Lathe"}, {"name": "Earthsea"}],
                "lead": None,
            },
        }

    def test_the_presenter_is_handed_the_principal_the_target_and_the_call_context(
        self, ada: Any
    ) -> None:
        seen: list[Any] = []

        class Seeing(DataclassPresenter):
            def presented(self, value: Any, context: Any) -> Any:
                seen.append(context)
                return super().presented(value, context)

        class Asked(PermissionCheck):
            def has_permission(self, principal: Any, spec: Any, call_context: Any) -> bool:
                seen.append(call_context)
                return True

        note = Note.objects.create(owner=ada, title="Ada's")
        spec = row_spec(permissions=[Asked()], presenter=Seeing(NoteOut))
        get(form_view(spec), ada, pk=note.pk)
        call_context, presented = seen
        assert presented.principal == ada
        assert presented.target.pk == note.pk
        assert presented.call_context is call_context
