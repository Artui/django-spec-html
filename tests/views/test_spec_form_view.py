"""``SpecFormView``: a spec validated by a form, served as the page that form is.

Assertions read ``response.context_data["form"].errors`` - the bound form the
page re-renders - rather than parsing HTML, and each refusal test says which
check answered: the Validator records every call, so a test naming the shape
check asserts the Validator never ran, and one naming the form asserts it did.
The module is its own URLconf, since ``resolve_url`` reads the project's.
"""

from __future__ import annotations

import datetime
import tempfile
from collections.abc import Iterator, Mapping
from decimal import Decimal
from typing import Any

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import BadRequest, ImproperlyConfigured, PermissionDenied
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404, HttpResponse
from django.urls import path, reverse_lazy
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy
from django_service_specs import (
    ActionUnavailable,
    AdditionalInputRequired,
    DispatchError,
    DispatchResult,
    FormValidator,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceConflict,
    ServiceError,
    ServiceNotFound,
    ServiceSpec,
    ServiceValidationError,
    UnknownArguments,
    ValidationContext,
)

from django_spec_html.http.unsupported_media_type import UnsupportedMediaType
from django_spec_html.views.spec_form_view import SpecFormView
from tests.adapter_app.models import Author as BookAuthor
from tests.adapter_app.models import Book, Document, Edition
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
from tests.http.utils import FACTORY, note_spec, signed_in

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

    def test_the_page_form_s_own_checks_add_nothing_to_dispatch_s_refusal(self, ada: Any) -> None:
        # The shape check refuses the count before the Validator runs, so the
        # form's ``clean()`` never decided anything: the page shows what
        # dispatch did, and not what the page's form would have said.
        spec = entry_spec()
        response = post(form_view(spec), ada, {"title": "shut", "count": "x"})
        assert (response.status_code, refused(response)) == (400, {"count": [WHOLE]})
        assert validated(spec) == 0

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
    """A post the Validator refuses for a value no column holds re-renders the page.

    The page's own form is never validated again to show the refusal: it has
    none of the Validator's guards, and its own validation would hand the
    value to the database driver, which raises ``OverflowError``.
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
        # so nothing has judged the key: the page shows the refusal alone.
        spec = ServiceSpec(
            service=Record(), permissions=OPEN, validator=FormValidator(AssigneesForm)
        )
        response = post(form_view(spec), ada, {"assignees": [2**63]})
        assert (response.status_code, refused(response)) == (400, {"title": [REQUIRED]})

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
