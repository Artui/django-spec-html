"""The templates this package ships, rendered by a page built with no ``template_name``.

Each test reads the rendered body - what a person is sent - and asserts what
is in it and what is not. The suite's settings list ``django_spec_html`` with
``APP_DIRS`` on, as a project rendering the shipped templates does; the
tests of the refusal at ``as_view()`` take it out again. The module is its
own URLconf, since ``resolve_url`` reads the project's.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Any

import pytest
from django import forms
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import override_settings
from django.urls import path
from django_service_specs import (
    ActionUnavailable,
    AdditionalInputRequired,
    Affordance,
    DataclassPresenter,
    FieldMarking,
    FormValidator,
    SelectorKind,
    SelectorSpec,
    ServiceConflict,
    ServiceSpec,
)

from django_spec_html.views.spec_confirm_view import SpecConfirmView
from django_spec_html.views.spec_form_set_view import SpecFormSetView
from django_spec_html.views.spec_form_view import SpecFormView
from django_spec_html.views.utils import SpecPageBase
from tests.dispatch.utils import OPEN, PK, Record, make_user, note_by_pk
from tests.dispatch_app.models import Note
from tests.http.utils import FACTORY, signed_in

pytestmark = pytest.mark.django_db

urlpatterns = [path("done/", lambda request: HttpResponse(), name="done")]

HTMX = {"HTTP_HX_REQUEST": "true"}
DOCUMENT = "<!DOCTYPE html>"
# The suite's own settings, less this package: no engine finds what it ships.
WITHOUT_THE_APP = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "tests.adapter_app",
    "tests.dispatch_app",
]
ONE_NOTE = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=note_by_pk, reads=PK)
EVERY_NOTE = SelectorSpec(kind=SelectorKind.LIST, selector=lambda: Note.objects.all())


class NoteForm(forms.Form):
    title = forms.CharField(max_length=20)


class AskedForm(forms.Form):
    title = forms.CharField(max_length=20)
    confirmed = forms.BooleanField(required=False)


class AttachmentForm(forms.Form):
    title = forms.CharField(max_length=20)
    attachment = forms.FileField(required=False)


@dataclasses.dataclass
class NoteOut:
    pk: Annotated[int, FieldMarking.handle()]
    title: Annotated[str, FieldMarking.label()]


def form_spec(form_class: type[forms.BaseForm] = NoteForm, **kwargs: Any) -> ServiceSpec:
    kwargs.setdefault("service", Record())
    kwargs.setdefault("permissions", OPEN)
    return ServiceSpec(validator=FormValidator(form_class), **kwargs)


def edit_spec(**kwargs: Any) -> ServiceSpec:
    """An update of the note the route names, through ``NoteForm``."""
    return form_spec(instance_selector_spec=ONE_NOTE, **kwargs)


def many_spec(**kwargs: Any) -> ServiceSpec:
    return form_spec(many=True, **kwargs)


def confirm_spec(**kwargs: Any) -> ServiceSpec:
    kwargs.setdefault("service", Record())
    kwargs.setdefault("permissions", OPEN)
    kwargs.setdefault("instance_selector_spec", ONE_NOTE)
    return ServiceSpec(**kwargs)


def raising(exc: Exception) -> Callable[..., Any]:
    def service(**pool: Any) -> None:
        raise exc

    return service


def built(view_class: type[SpecPageBase], spec: ServiceSpec, **initkwargs: Any) -> Any:
    initkwargs.setdefault("success_url", "/done/")
    if "pk" in spec.parameters().names():
        initkwargs.setdefault("route_kwargs", ("pk",))
    return view_class.as_view(spec=spec, **initkwargs)


def get(view: Any, user: Any, headers: Mapping[str, str] | None = None, **url_kwargs: Any) -> Any:
    return view(signed_in(FACTORY.get("/", **(headers or {})), user), **url_kwargs)


def post(
    view: Any,
    user: Any,
    data: Mapping[str, Any],
    headers: Mapping[str, str] | None = None,
    **url_kwargs: Any,
) -> Any:
    return view(signed_in(FACTORY.post("/", data=data, **(headers or {})), user), **url_kwargs)


def body(response: Any) -> str:
    return response.render().content.decode()


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = __name__


@pytest.fixture
def ada() -> Any:
    return make_user("ada")


@pytest.fixture
def note(ada: Any) -> Note:
    return Note.objects.create(owner=ada, title="Buy milk")


class TestTheShippedNames:
    @pytest.mark.parametrize(
        ("view_class", "page", "fragment"),
        [
            (SpecFormView, "spec_form.html", "spec_form_fragment.html"),
            (SpecFormSetView, "spec_form_set.html", "spec_form_set_fragment.html"),
            (SpecConfirmView, "spec_confirm.html", "spec_confirm_fragment.html"),
        ],
    )
    def test_the_shipped_page_s_fragment_is_its_own_file(
        self, view_class: type[SpecPageBase], page: str, fragment: str
    ) -> None:
        view = view_class()
        assert view.get_template_names() == [f"django_spec_html/{page}"]
        assert view.get_fragment_template_names() == [f"django_spec_html/{fragment}"]

    def test_a_page_s_own_template_keeps_its_own_fragment_address(self) -> None:
        view = SpecFormView(template_name="books/form.html")
        assert view.get_fragment_template_names() == ["books/form.html#spec-form"]

    def test_a_project_s_file_of_the_same_name_overrides_it(self, ada: Any, tmp_path: Path) -> None:
        # The document every shipped page extends, and the form page's
        # fragment, each replaced by a directory searched before the app's.
        (tmp_path / "django_spec_html").mkdir()
        (tmp_path / "django_spec_html" / "base.html").write_text(
            "<body class=ours>{% block content %}{% endblock %}</body>"
        )
        (tmp_path / "django_spec_html" / "spec_form_fragment.html").write_text(
            "<form class=ours>{{ form.title }}</form>"
        )
        templates = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(tmp_path)],
                "APP_DIRS": True,
            }
        ]
        with override_settings(TEMPLATES=templates):
            view = built(SpecFormView, form_spec())
            page = body(get(view, ada))
            fragment = body(get(view, ada, HTMX))
        assert page.startswith("<body class=ours><form class=ours><input")
        assert fragment.startswith("<form class=ours><input")

    def test_a_page_s_own_template_wins(self, ada: Any) -> None:
        # The suite's ``spec_form.html`` is a form tag and nothing else.
        rendered = body(get(built(SpecFormView, form_spec(), template_name="spec_form.html"), ada))
        assert rendered.startswith('<form method="post">')
        assert DOCUMENT not in rendered


class TestTheFormPage:
    def test_get_renders_the_form_in_a_document(self, ada: Any) -> None:
        rendered = body(get(built(SpecFormView, form_spec()), ada))
        assert rendered.startswith(DOCUMENT)
        assert 'name="csrfmiddlewaretoken"' in rendered
        assert '<input type="text" name="title" maxlength="20" required' in rendered
        # No commit button set, so the submit button posts no name.
        assert '<button type="submit">Save</button>' in rendered
        assert "enctype" not in rendered
        assert "hx-" not in rendered

    def test_a_partial_page_renders_no_required_attribute(self, ada: Any, note: Note) -> None:
        view = built(SpecFormView, edit_spec(partial=True))
        rendered = body(get(view, ada, pk=note.pk))
        assert 'name="title" value="Buy milk"' in rendered
        assert " required" not in rendered
        assert "A field left blank is left as it is." in rendered

    def test_a_full_update_renders_the_attribute_and_no_note(self, ada: Any, note: Note) -> None:
        rendered = body(get(built(SpecFormView, edit_spec()), ada, pk=note.pk))
        assert " required" in rendered
        assert "left blank" not in rendered

    def test_a_file_field_makes_the_form_multipart(self, ada: Any) -> None:
        rendered = body(get(built(SpecFormView, form_spec(AttachmentForm)), ada))
        assert '<form method="post" enctype="multipart/form-data">' in rendered

    def test_a_refused_post_shows_the_field_s_error(self, ada: Any) -> None:
        response = post(built(SpecFormView, form_spec()), ada, {"title": "x" * 21})
        assert response.status_code == 400
        rendered = body(response)
        assert rendered.startswith(DOCUMENT)
        assert "Ensure this value has at most 20 characters" in rendered

    def test_a_refusal_s_message_is_escaped_and_its_code_is_on_the_form(self, ada: Any) -> None:
        refused = ActionUnavailable("<b>Closed</b> for now.", code="closed")
        response = post(
            built(SpecFormView, form_spec(service=raising(refused))), ada, {"title": "a"}
        )
        assert response.status_code == 409
        rendered = body(response)
        assert '<form method="post" data-code="closed">' in rendered
        assert "&lt;b&gt;Closed&lt;/b&gt; for now." in rendered
        assert "<b>Closed</b>" not in rendered

    def test_an_unmet_affordance_disables_the_form_and_says_why(self, ada: Any) -> None:
        paused = Affordance(code="paused", reason="Notes are paused.", when=lambda: False)
        rendered = body(get(built(SpecFormView, form_spec(affordances=[paused])), ada))
        assert '<p data-code="paused">Notes are paused.</p>' in rendered
        assert "<fieldset disabled>" in rendered

    def test_every_condition_met_leaves_the_form_enabled(self, ada: Any) -> None:
        rendered = body(get(built(SpecFormView, form_spec()), ada))
        assert "<fieldset>" in rendered
        assert "disabled" not in rendered
        assert "data-code" not in rendered

    def test_a_question_shows_its_message_and_its_field_once(self, ada: Any) -> None:
        asked = AdditionalInputRequired("Sure?", schema={"confirmed": {"type": "boolean"}})
        spec = form_spec(AskedForm, service=raising(asked))
        view = built(SpecFormView, spec, asked_fields=("confirmed",))
        assert 'name="confirmed"' not in body(get(view, ada))
        response = post(view, ada, {"title": "a"})
        assert response.status_code == 422
        rendered = body(response)
        assert rendered.count("Sure?") == 1
        assert rendered.count('name="confirmed"') == 1

    def test_the_submit_button_posts_the_commit_button_s_name(self, ada: Any) -> None:
        rendered = body(get(built(SpecFormView, form_spec(), commit_button="save"), ada))
        assert '<button type="submit" name="save">Save</button>' in rendered

    def test_a_post_without_the_button_shows_its_values_and_no_error(self, ada: Any) -> None:
        spec = form_spec()
        view = built(SpecFormView, spec, commit_button="save")
        response = post(view, ada, {"title": "x" * 21})
        assert (response.status_code, spec.service.calls) == (200, [])
        rendered = body(response)
        assert f'value="{"x" * 21}"' in rendered
        assert "errorlist" not in rendered
        assert post(view, ada, {"title": "Pay rent", "save": ""}).status_code == 302

    def test_the_fragment_is_the_form_alone(self, ada: Any) -> None:
        rendered = body(get(built(SpecFormView, form_spec()), ada, HTMX))
        assert DOCUMENT not in rendered
        assert "<main>" not in rendered
        assert '<form method="post">' in rendered

    def test_a_refused_post_under_htmx_is_the_fragment_at_200(self, ada: Any) -> None:
        response = post(built(SpecFormView, form_spec()), ada, {"title": "x" * 21}, HTMX)
        assert response.status_code == 200
        rendered = body(response)
        assert DOCUMENT not in rendered
        assert "Ensure this value has at most 20 characters" in rendered

    def test_a_success_under_htmx_is_a_client_redirect(self, ada: Any) -> None:
        # The shipped fragment defines no ``spec-result``, so no success is
        # rendered in place: it is the redirect the page would make.
        response = post(built(SpecFormView, form_spec()), ada, {"title": "a"}, HTMX)
        assert (response.status_code, response["HX-Redirect"]) == (200, "/done/")
        assert "HX-Trigger" not in response


class TestTheTargetPane:
    def test_a_row_is_shown_by_its_display_with_no_handle(self, ada: Any, note: Note) -> None:
        note.title = "<i>Milk</i>"
        note.save()
        spec = confirm_spec(presenter=DataclassPresenter(NoteOut))
        rendered = body(get(built(SpecConfirmView, spec, display_target=True), ada, pk=note.pk))
        assert "<p>&lt;i&gt;Milk&lt;/i&gt;</p>" in rendered
        assert "<dt>title</dt><dd>&lt;i&gt;Milk&lt;/i&gt;</dd>" in rendered
        assert "<dt>pk</dt>" not in rendered

    def test_the_form_page_shows_it_beside_the_form(self, ada: Any, note: Note) -> None:
        spec = edit_spec(presenter=DataclassPresenter(NoteOut))
        rendered = body(get(built(SpecFormView, spec, display_target=True), ada, pk=note.pk))
        assert rendered.index("<p>Buy milk</p>") < rendered.index("<form")

    def test_a_row_not_displayed_is_not_shown(self, ada: Any, note: Note) -> None:
        rendered = body(get(built(SpecConfirmView, confirm_spec()), ada, pk=note.pk))
        assert "Buy milk" not in rendered
        assert "<dl>" not in rendered

    def test_a_set_is_counted_whether_or_not_it_is_displayed(self, ada: Any, note: Note) -> None:
        Note.objects.create(owner=ada, title="Pay rent")
        spec = confirm_spec(collection_selector_spec=EVERY_NOTE, instance_selector_spec=None)
        rendered = body(get(built(SpecConfirmView, spec), ada))
        assert "2 rows selected, counted at" in rendered
        assert "Buy milk" not in rendered

    def test_a_displayed_set_lists_its_rows_up_to_the_limit(self, ada: Any, note: Note) -> None:
        Note.objects.create(owner=ada, title="Pay rent")
        spec = confirm_spec(
            collection_selector_spec=EVERY_NOTE,
            instance_selector_spec=None,
            presenter=DataclassPresenter(NoteOut),
        )
        view = built(SpecConfirmView, spec, display_target=True, target_display_limit=1)
        rendered = body(get(view, ada))
        assert "<li>Buy milk</li>" in rendered
        assert "Pay rent" not in rendered
        assert "The first row is shown." in rendered

    def test_one_row_counted_is_one_row(self, ada: Any, note: Note) -> None:
        spec = confirm_spec(collection_selector_spec=EVERY_NOTE, instance_selector_spec=None)
        assert "1 row selected, counted at" in body(get(built(SpecConfirmView, spec), ada))


class TestTheManyPage:
    def test_get_renders_the_management_form_and_each_row(self, ada: Any) -> None:
        rendered = body(get(built(SpecFormSetView, many_spec(), extra=2), ada))
        assert rendered.startswith(DOCUMENT)
        assert 'name="form-TOTAL_FORMS" value="2"' in rendered
        assert "<legend>Row 1</legend>" in rendered
        assert "<legend>Row 2</legend>" in rendered
        assert 'name="form-1-title"' in rendered
        assert "formnovalidate" not in rendered

    def test_no_target_pane(self, ada: Any) -> None:
        rendered = body(get(built(SpecFormSetView, many_spec()), ada))
        assert "selected, counted at" not in rendered
        assert "<dl>" not in rendered

    def test_the_commit_button_comes_before_the_add_button(self, ada: Any) -> None:
        view = built(SpecFormSetView, many_spec(), commit_button="save", add_button="add")
        rendered = body(get(view, ada))
        commit = '<button type="submit" name="save">Save</button>'
        add = '<button type="submit" name="add" formnovalidate>Add another</button>'
        assert rendered.index(commit) < rendered.index(add)

    def test_the_add_button_s_post_is_one_more_row(self, ada: Any) -> None:
        spec = many_spec()
        view = built(SpecFormSetView, spec, commit_button="save", add_button="add")
        data = {"form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-0-title": "a", "add": ""}
        rendered = body(post(view, ada, data))
        assert ("<legend>Row 2</legend>" in rendered, spec.service.calls) == (True, [])

    def test_a_refused_row_carries_its_error(self, ada: Any) -> None:
        data = {"form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-0-title": "x" * 21}
        response = post(built(SpecFormSetView, many_spec()), ada, data)
        assert response.status_code == 400
        assert "Ensure this value has at most 20 characters" in body(response)

    def test_a_question_is_its_message_alone(self, ada: Any) -> None:
        asked = AdditionalInputRequired("Sure?", schema=None)
        data = {"form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-0-title": "a"}
        response = post(built(SpecFormSetView, many_spec(service=raising(asked))), ada, data)
        assert response.status_code == 422
        rendered = body(response)
        assert rendered.count("Sure?") == 1
        assert "confirmed" not in rendered

    def test_the_fragment_is_the_formset_alone(self, ada: Any) -> None:
        rendered = body(get(built(SpecFormSetView, many_spec()), ada, HTMX))
        assert DOCUMENT not in rendered
        assert 'name="form-TOTAL_FORMS"' in rendered


class TestTheConfirmationPage:
    def test_get_renders_a_button_and_no_field(self, ada: Any, note: Note) -> None:
        rendered = body(get(built(SpecConfirmView, confirm_spec()), ada, pk=note.pk))
        assert rendered.startswith(DOCUMENT)
        assert 'name="csrfmiddlewaretoken"' in rendered
        assert '<button type="submit">Confirm</button>' in rendered
        assert "<input" not in rendered.replace(
            '<input type="hidden" name="csrfmiddlewaretoken"', ""
        )

    def test_a_refusal_s_messages_are_listed(self, ada: Any, note: Note) -> None:
        refused = ServiceConflict("<b>Already</b> withdrawn.")
        view = built(SpecConfirmView, confirm_spec(service=raising(refused)))
        response = post(view, ada, {}, pk=note.pk)
        assert response.status_code == 409
        rendered = body(response)
        assert (
            '<ul class="errorlist"><li>&lt;b&gt;Already&lt;/b&gt; withdrawn.</li></ul>' in rendered
        )

    def test_an_unavailable_refusal_s_code_is_on_the_form(self, ada: Any, note: Note) -> None:
        refused = ActionUnavailable("Paused.", code="paused")
        view = built(SpecConfirmView, confirm_spec(service=raising(refused)))
        rendered = body(post(view, ada, {}, pk=note.pk))
        assert '<form method="post" data-code="paused">' in rendered

    def test_a_question_is_its_message_and_no_control(self, ada: Any, note: Note) -> None:
        asked = AdditionalInputRequired("Withdraw it anyway?", schema=None)
        view = built(SpecConfirmView, confirm_spec(service=raising(asked)))
        response = post(view, ada, {}, pk=note.pk)
        assert response.status_code == 422
        rendered = body(response)
        assert "<p>Withdraw it anyway?</p>" in rendered
        assert "errorlist" not in rendered

    def test_an_unmet_affordance_disables_the_button(self, ada: Any, note: Note) -> None:
        paused = Affordance(code="paused", reason="Withdrawing is paused.", when=lambda: False)
        view = built(SpecConfirmView, confirm_spec(affordances=[paused]))
        rendered = body(get(view, ada, pk=note.pk))
        assert '<p data-code="paused">Withdrawing is paused.</p>' in rendered
        assert '<button type="submit" disabled>Confirm</button>' in rendered

    def test_a_post_without_the_commit_button_dispatches_nothing(
        self, ada: Any, note: Note
    ) -> None:
        spec = confirm_spec()
        view = built(SpecConfirmView, spec, commit_button="withdraw")
        assert '<button type="submit" name="withdraw">' in body(get(view, ada, pk=note.pk))
        response = post(view, ada, {}, pk=note.pk)
        assert (response.status_code, spec.service.calls) == (200, [])
        assert post(view, ada, {"withdraw": ""}, pk=note.pk).status_code == 302

    def test_the_fragment_is_the_button_alone(self, ada: Any, note: Note) -> None:
        rendered = body(get(built(SpecConfirmView, confirm_spec()), ada, HTMX, pk=note.pk))
        assert DOCUMENT not in rendered
        assert "<button" in rendered


class TestAPageNoEngineCanRender:
    @pytest.mark.parametrize(
        ("view_class", "spec", "page"),
        [
            (SpecFormView, form_spec(), "spec_form.html"),
            (SpecFormSetView, many_spec(), "spec_form_set.html"),
            (SpecConfirmView, confirm_spec(), "spec_confirm.html"),
        ],
    )
    def test_is_refused_at_as_view_without_the_app(
        self, view_class: type[SpecPageBase], spec: ServiceSpec, page: str
    ) -> None:
        with (
            override_settings(INSTALLED_APPS=WITHOUT_THE_APP),
            pytest.raises(ImproperlyConfigured) as refused,
        ):
            built(view_class, spec)
        assert str(refused.value) == (
            f"{view_class.__name__} renders django_spec_html/{page}, a template "
            "django-spec-html ships, and no template engine finds it: add 'django_spec_html' "
            "to INSTALLED_APPS with APP_DIRS on, or set template_name to a template of the "
            "project's own."
        )

    def test_the_fragment_is_looked_for_beside_the_page(self, tmp_path: Path) -> None:
        # A project overriding the page in a directory of its own, without
        # the app: the page is found, and the fragment the page includes not.
        (tmp_path / "django_spec_html").mkdir()
        (tmp_path / "django_spec_html" / "spec_form.html").write_text("<p>Ours</p>")
        templates = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(tmp_path)],
            }
        ]
        with (
            override_settings(INSTALLED_APPS=WITHOUT_THE_APP, TEMPLATES=templates),
            pytest.raises(ImproperlyConfigured, match="spec_form_fragment.html"),
        ):
            built(SpecFormView, form_spec())

    def test_a_page_s_own_template_is_not_looked_for(self) -> None:
        with override_settings(INSTALLED_APPS=WITHOUT_THE_APP):
            built(SpecFormView, form_spec(), template_name="nowhere.html")

    def test_an_overridden_get_template_names_is_not_asked(self) -> None:
        class Chosen(SpecFormView):
            def get_template_names(self) -> list[str]:
                return [f"notes/{self.request.method.lower()}.html"]

        with override_settings(INSTALLED_APPS=WITHOUT_THE_APP):
            built(Chosen, form_spec())
