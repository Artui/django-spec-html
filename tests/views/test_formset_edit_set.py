from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from django import forms
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.http import HttpResponse
from django.urls import path
from django_service_specs import (
    FormValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
)

from django_spec_html.views.spec_form_set_view import SpecFormSetView
from tests.dispatch.utils import OPEN, Record, make_user, notes_of
from tests.dispatch_app.models import Note
from tests.http.utils import FACTORY, signed_in

pytestmark = pytest.mark.django_db

urlpatterns = [path("done/", lambda request: HttpResponse(), name="done")]

NoteSet = forms.modelformset_factory(Note, fields=["title"], can_delete=True, extra=1)
OWN_NOTES = SelectorSpec(kind=SelectorKind.LIST, selector=notes_of)


def notes_from(*, user: Any, first: int) -> Any:
    return notes_of(user=user).filter(pk__gte=first)


FROM_FIRST = SelectorSpec(
    kind=SelectorKind.LIST,
    selector=notes_from,
    reads=Parameters.of(Parameter("first", "integer", required=True)),
)


class TitleRow(forms.Form):
    title = forms.CharField(max_length=20)


class NoTwoAlike(forms.BaseFormSet):
    def clean(self) -> None:
        titles = [form.cleaned_data.get("title") for form in self.forms]
        if len(set(titles)) != len(titles):
            raise ValidationError("Two rows carry one title.", code="repeated")


TitleSet = forms.formset_factory(TitleRow, formset=NoTwoAlike, extra=2)


def edit_spec(**kwargs: Any) -> ServiceSpec:
    kwargs.setdefault("validator", FormValidator(NoteSet.form, formset_class=NoteSet))
    kwargs.setdefault("collection_selector_spec", OWN_NOTES)
    return ServiceSpec(service=Record(), permissions=OPEN, many=True, **kwargs)


def page(spec: ServiceSpec, **initkwargs: Any) -> Any:
    return SpecFormSetView.as_view(spec=spec, success_url="/done/", **initkwargs)


def get(view: Any, user: Any) -> Any:
    return view(signed_in(FACTORY.get("/"), user))


def post(view: Any, user: Any, data: Mapping[str, Any]) -> Any:
    return view(signed_in(FACTORY.post("/", data=data), user))


def management(total: int, initial: int) -> dict[str, str]:
    return {"form-TOTAL_FORMS": str(total), "form-INITIAL_FORMS": str(initial)}


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = __name__


@pytest.fixture
def ada() -> Any:
    return make_user("ada")


@pytest.fixture
def notes(ada: Any) -> list[Note]:
    make_user("bob").notes.create(title="Not ada's")
    return [ada.notes.create(title=title) for title in ("First", "Second", "Third")]


class TestAsView:
    def test_displaying_the_set_is_still_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="display_target"):
            page(edit_spec(), display_target=True)

    def test_a_model_formset_with_no_collection_is_refused(self) -> None:
        spec = edit_spec(collection_selector_spec=None)
        with pytest.raises(ImproperlyConfigured, match="collection_selector_spec"):
            page(spec)

    def test_a_plain_formset_class_needs_no_collection(self) -> None:
        page(edit_spec(validator=FormValidator(TitleRow, formset_class=TitleSet)))


class TestGet:
    def test_one_prefilled_form_per_row_then_the_extra(self, ada: Any, notes: list[Note]) -> None:
        response = get(page(edit_spec()), ada)
        formset = response.context_data["formset"]
        assert isinstance(formset, NoteSet)
        assert [form.instance for form in formset.forms[:3]] == notes
        assert [form["title"].value() for form in formset.forms] == [
            "First",
            "Second",
            "Third",
            None,
        ]
        assert formset.management_form["INITIAL_FORMS"].value() == 3
        content = response.render().content.decode()
        assert f'name="form-0-id" value="{notes[0].pk}"' in content
        assert 'name="form-0-DELETE"' in content

    def test_the_page_shows_the_set_s_count_not_its_rows(self, ada: Any, notes: list[Note]) -> None:
        context = get(page(edit_spec()), ada).context_data
        assert (context["target_count"], "target_display" in context) == (3, False)

    def test_a_plain_formset_class_is_the_page_s(self, ada: Any) -> None:
        spec = edit_spec(validator=FormValidator(TitleRow, formset_class=TitleSet))
        formset = get(page(spec), ada).context_data["formset"]
        assert (type(formset), len(formset.forms)) == (TitleSet, 2)


class TestPost:
    def test_an_update_a_delete_and_a_create_reach_the_service(
        self, ada: Any, notes: list[Note]
    ) -> None:
        first, second, third = notes
        spec = edit_spec()
        data = {
            **management(4, 3),
            "form-0-id": str(first.pk),
            "form-0-title": "First, renamed",
            "form-1-id": str(second.pk),
            "form-1-title": "Second",
            "form-1-DELETE": "on",
            "form-2-id": str(third.pk),
            "form-2-title": "Third",
            "form-3-title": "Fourth",
        }
        response = post(page(spec), ada, data)
        assert response.status_code == 302
        [call] = spec.service.calls
        sent = call[spec.many_argument]
        assert [(row["id"], row["title"], row.get("DELETE")) for row in sent] == [
            (first, "First, renamed", False),
            (second, "Second", True),
            (third, "Third", False),
            (None, "Fourth", False),
        ]
        assert list(call["collection"]) == notes

    def test_an_unchanged_extra_form_sends_no_row(self, ada: Any, notes: list[Note]) -> None:
        spec = edit_spec()
        data = {**management(4, 3), **_unchanged(notes)}
        post(page(spec), ada, data)
        [call] = spec.service.calls
        assert [row["id"] for row in call[spec.many_argument]] == notes

    def test_an_extra_form_only_marked_deleted_sends_no_row(
        self, ada: Any, notes: list[Note]
    ) -> None:
        spec = edit_spec()
        data = {**management(4, 3), **_unchanged(notes), "form-3-DELETE": "on"}
        assert post(page(spec), ada, data).status_code == 302
        [call] = spec.service.calls
        assert len(call[spec.many_argument]) == 3

    def test_a_row_outside_the_set_lands_on_its_form(self, ada: Any, notes: list[Note]) -> None:
        stranger = Note.objects.get(title="Not ada's")
        spec = edit_spec()
        data = {**management(4, 3), **_unchanged(notes), "form-2-id": str(stranger.pk)}
        response = post(page(spec), ada, data)
        assert response.status_code == 400
        assert list(response.context_data["formset"].forms[2].errors) == ["id"]
        assert spec.service.calls == []

    def test_a_set_wide_refusal_is_the_formset_s_non_form_error(self, ada: Any) -> None:
        spec = edit_spec(validator=FormValidator(TitleRow, formset_class=TitleSet))
        data = {**management(2, 0), "form-0-title": "Same", "form-1-title": "Same"}
        response = post(page(spec), ada, data)
        formset = response.context_data["formset"]
        assert list(formset.non_form_errors()) == ["Two rows carry one title."]
        assert spec.service.calls == []

    def test_a_post_whose_set_is_unresolved_edits_no_row(self, ada: Any, notes: list[Note]) -> None:
        spec = edit_spec(collection_selector_spec=FROM_FIRST)
        request = signed_in(FACTORY.post("/", data={**management(1, 0), "form-0-title": "A"}), ada)
        response = page(spec, route_refusal_404=False)(request, first="soon")
        formset = response.context_data["formset"]
        assert (response.status_code, list(formset.get_queryset())) == (400, [])
        assert spec.service.calls == []

    def test_a_non_committing_post_keeps_the_set(self, ada: Any, notes: list[Note]) -> None:
        view = type(
            "Adding", (SpecFormSetView,), {"commit_button": "save", "add_button": "add"}
        ).as_view(spec=edit_spec(), success_url="/done/")
        data = {**management(4, 3), **_unchanged(notes), "add": "+"}
        formset = post(view, ada, data).context_data["formset"]
        assert [form.instance for form in formset.forms[:3]] == notes
        assert len(formset.forms) == 5


def _unchanged(notes: list[Note]) -> dict[str, str]:
    data: dict[str, str] = {}
    for index, note in enumerate(notes):
        data[f"form-{index}-id"] = str(note.pk)
        data[f"form-{index}-title"] = note.title
    return data
