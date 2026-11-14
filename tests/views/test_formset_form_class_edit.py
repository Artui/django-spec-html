from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

import pytest
from django import forms
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.urls import path
from django_service_specs import (
    DataclassValidator,
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

OWN_NOTES = SelectorSpec(kind=SelectorKind.LIST, selector=notes_of)


def notes_from(*, user: Any, first: int) -> Any:
    return notes_of(user=user).filter(pk__gte=first)


FROM_FIRST = SelectorSpec(
    kind=SelectorKind.LIST,
    selector=notes_from,
    reads=Parameters.of(Parameter("first", "integer", required=True)),
)
CHOICE = "Select a valid choice. That choice is not one of the available choices."


@dataclasses.dataclass
class EditedNote:
    title: str
    id: int | None = None
    DELETE: bool = False


@dataclasses.dataclass
class KeptNote:
    title: str
    id: int | None = None


@dataclasses.dataclass
class UnkeyedNote:
    title: str


@dataclasses.dataclass
class SpelledDelete:
    title: str
    id: int | None = None
    DELETE: str = ""


@dataclasses.dataclass
class RequiredDelete:
    title: str
    DELETE: bool
    id: int | None = None


class TitleRow(forms.Form):
    title = forms.CharField()


def edit_spec(row: type = EditedNote, **kwargs: Any) -> ServiceSpec:
    kwargs.setdefault("collection_selector_spec", OWN_NOTES)
    return ServiceSpec(
        service=Record(),
        validator=DataclassValidator(row),
        permissions=OPEN,
        many=True,
        **kwargs,
    )


def page(spec: ServiceSpec, **initkwargs: Any) -> Any:
    return SpecFormSetView.as_view(
        spec=spec, form_class=TitleRow, success_url="/done/", **initkwargs
    )


def get(view: Any, user: Any) -> Any:
    return view(signed_in(FACTORY.get("/"), user))


def post(view: Any, user: Any, data: Mapping[str, Any]) -> Any:
    return view(signed_in(FACTORY.post("/", data=data), user))


def management(total: int, initial: int) -> dict[str, str]:
    return {"form-TOTAL_FORMS": str(total), "form-INITIAL_FORMS": str(initial)}


def sent(spec: ServiceSpec) -> list[dict[str, Any]]:
    return [dict(row) for row in spec.service.calls[0][spec.many_argument]]


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = __name__


@pytest.fixture
def ada() -> Any:
    return make_user("ada")


@pytest.fixture
def notes(ada: Any) -> list[Note]:
    return [Note.objects.create(owner=ada, title=title) for title in ("Emma", "Persuasion")]


class TestGet:
    def test_one_prefilled_form_a_row_then_the_extra_forms(self, ada: Any, notes: Any) -> None:
        formset = get(page(edit_spec()), ada).context_data["formset"]
        assert formset.initial_form_count() == 2
        assert len(formset.forms) == 3
        assert [form["title"].value() for form in formset.forms[:2]] == ["Emma", "Persuasion"]
        assert [form["id"].value() for form in formset.forms[:2]] == [n.pk for n in notes]
        assert "id" not in formset.forms[2].fields

    def test_the_management_form_carries_the_set_s_count(self, ada: Any, notes: Any) -> None:
        content = get(page(edit_spec()), ada).rendered_content
        assert 'name="form-INITIAL_FORMS" value="2"' in content
        assert f'name="form-0-id" value="{notes[0].pk}"' in content

    def test_only_the_principal_s_set_is_shown(self, ada: Any, notes: Any) -> None:
        Note.objects.create(owner=make_user("bob"), title="Not ada's")
        formset = get(page(edit_spec()), ada).context_data["formset"]
        assert formset.initial_form_count() == 2

    def test_a_delete_box_is_offered_where_the_rows_declare_one(self, ada: Any, notes: Any) -> None:
        formset = get(page(edit_spec()), ada).context_data["formset"]
        assert "DELETE" in formset.forms[0].fields

    def test_no_delete_box_where_the_rows_declare_none(self, ada: Any, notes: Any) -> None:
        formset = get(page(edit_spec(KeptNote)), ada).context_data["formset"]
        assert "DELETE" not in formset.forms[0].fields
        assert formset.forms[0]["id"].value() == notes[0].pk


class TestPost:
    def test_each_initial_row_is_sent_with_its_key(self, ada: Any, notes: Any) -> None:
        spec = edit_spec()
        data = {
            **management(3, 2),
            "form-0-id": str(notes[0].pk),
            "form-0-title": "Emma!",
            "form-1-id": str(notes[1].pk),
            "form-1-title": "Persuasion",
            "form-1-DELETE": "on",
            "form-2-title": "Sanditon",
        }
        assert post(page(spec), ada, data).status_code == 302
        assert sent(spec) == [
            {"title": "Emma!", "id": notes[0].pk, "DELETE": False},
            {"title": "Persuasion", "id": notes[1].pk, "DELETE": True},
            {"title": "Sanditon", "id": None, "DELETE": False},
        ]

    def test_an_extra_form_ticked_for_deletion_sends_no_row(self, ada: Any, notes: Any) -> None:
        spec = edit_spec()
        data = {
            **management(2, 1),
            "form-0-id": str(notes[0].pk),
            "form-0-title": "Emma",
            "form-1-title": "Sanditon",
            "form-1-DELETE": "on",
        }
        assert post(page(spec), ada, data).status_code == 302
        assert sent(spec) == [{"title": "Emma", "id": notes[0].pk, "DELETE": False}]

    def test_a_row_left_out_is_not_sent(self, ada: Any, notes: Any) -> None:
        spec = edit_spec(KeptNote)
        data = {**management(1, 1), "form-0-id": str(notes[1].pk), "form-0-title": "P"}
        assert post(page(spec), ada, data).status_code == 302
        assert sent(spec) == [{"title": "P", "id": notes[1].pk}]

    def test_a_key_outside_the_set_is_refused_on_its_row(self, ada: Any, notes: Any) -> None:
        theirs = Note.objects.create(owner=make_user("bob"), title="Not ada's")
        spec = edit_spec()
        data = {**management(1, 1), "form-0-id": str(theirs.pk), "form-0-title": "Mine"}
        response = post(page(spec), ada, data)
        assert response.status_code == 400
        assert dict(response.context_data["formset"].forms[0].errors) == {"id": [CHOICE]}
        assert spec.service.calls == []

    def test_an_initial_form_without_its_key_is_refused(self, ada: Any, notes: Any) -> None:
        spec = edit_spec()
        data = {**management(1, 1), "form-0-title": "Mine"}
        response = post(page(spec), ada, data)
        assert response.status_code == 400
        assert dict(response.context_data["formset"].forms[0].errors) == {
            "id": ["This field is required."]
        }

    def test_a_post_whose_set_went_unresolved_builds_no_key(self, ada: Any, notes: Any) -> None:
        spec = edit_spec(collection_selector_spec=FROM_FIRST)
        data = {**management(1, 1), "form-0-id": str(notes[0].pk), "form-0-title": "Emma"}
        response = post(page(spec), ada, data)
        assert response.status_code == 400
        assert set(response.context_data["formset"].forms[0].fields) == {"title", "DELETE"}
        assert spec.service.calls == []


class TestRefused:
    def test_rows_that_declare_no_key_are_refused_when_the_set_is_built(
        self, ada: Any, notes: Any
    ) -> None:
        view = page(edit_spec(UnkeyedNote))
        with pytest.raises(ImproperlyConfigured) as refused:
            get(view, ada)
        assert str(refused.value) == (
            "SpecFormSetView edits the rows its spec's collection_selector_spec resolves, "
            "each sent with its key 'id', which the spec's rows do not declare."
        )

    def test_a_delete_the_rows_declare_as_anything_but_a_boolean(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            page(edit_spec(SpelledDelete))
        assert str(refused.value) == (
            "SpecFormSetView edits a set, whose deleted rows carry 'DELETE' as a boolean, and "
            "the spec's rows declare string."
        )

    def test_a_delete_on_a_page_that_edits_no_set_is_any_row_field(self) -> None:
        page(edit_spec(SpelledDelete, collection_selector_spec=None))

    def test_a_required_delete_is_the_formset_s_to_post(self) -> None:
        page(edit_spec(RequiredDelete))

    def test_a_required_delete_on_a_page_that_edits_no_set_is_the_form_s(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="does not post 'DELETE'"):
            page(edit_spec(RequiredDelete, collection_selector_spec=None))


class TestAddingARow:
    def page(self) -> Any:
        return page(edit_spec(), commit_button="save", add_button="add")

    def data(self, notes: list[Note]) -> dict[str, str]:
        return {
            **management(3, 2),
            "form-0-id": str(notes[0].pk),
            "form-0-title": "Emma",
            "form-1-id": str(notes[1].pk),
            "form-1-title": "Persuasion",
            "add": "",
        }

    def test_the_set_comes_back_keyed_with_one_more_form(self, ada: Any, notes: Any) -> None:
        formset = post(self.page(), ada, self.data(notes)).context_data["formset"]
        assert len(formset.forms) == 4
        assert [form["id"].value() for form in formset.forms[:2]] == [str(n.pk) for n in notes]
        assert "id" not in formset.forms[3].fields

    def test_an_htmx_add_answers_the_new_form_unkeyed(self, ada: Any, notes: Any) -> None:
        request = FACTORY.post(
            "/",
            data=self.data(notes),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="spec-form-set-rows",
        )
        response = self.page()(signed_in(request, ada))
        content = response.content.decode()
        assert 'name="form-3-title"' in content
        assert "form-3-id" not in content
