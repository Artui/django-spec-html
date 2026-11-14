from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from django import forms
from django.http import HttpResponse
from django.test.signals import template_rendered
from django.urls import path
from django_service_specs import FormValidator, ServiceSpec

from django_spec_html.views.spec_form_set_view import SpecFormSetView
from tests.dispatch.utils import OPEN, Record, make_user
from tests.http.utils import FACTORY, signed_in

pytestmark = pytest.mark.django_db

urlpatterns = [path("done/", lambda request: HttpResponse(), name="done")]

ROWS = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "spec-form-set-rows"}
ROW_TEMPLATE = "django_spec_html/spec_form_set_row.html"


class TitleRow(forms.Form):
    title = forms.CharField(max_length=20)


class OwnedRow(forms.Form):
    """A row whose ``__init__`` takes a keyword, which it shows as the title's initial value."""

    title = forms.CharField()

    def __init__(self, *args: Any, owner: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["title"].initial = f"by {owner.username}"


def many_spec(validator: FormValidator | None = None, **kwargs: Any) -> ServiceSpec:
    validator = validator or FormValidator(TitleRow)
    return ServiceSpec(service=Record(), permissions=OPEN, many=True, validator=validator, **kwargs)


def adding(spec: ServiceSpec) -> Any:
    return SpecFormSetView.as_view(
        spec=spec, success_url="/done/", commit_button="save", add_button="add"
    )


def posted(*titles: str, **extra: str) -> dict[str, str]:
    data = {"form-TOTAL_FORMS": str(len(titles)), "form-INITIAL_FORMS": "0", **extra}
    data.update({f"form-{index}-title": title for index, title in enumerate(titles)})
    return data


def post(view: Any, user: Any, data: Mapping[str, str], headers: Mapping[str, str]) -> Any:
    return view(signed_in(FACTORY.post("/", data=data, **headers), user))


def oob_total(content: str) -> str:
    """The one out-of-band input the answer carries, its markup whole."""
    [found] = re.findall(r"<input[^>]*hx-swap-oob[^>]*>", content)
    return found


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = __name__


@pytest.fixture
def ada() -> Any:
    return make_user("ada")


@pytest.fixture
def rendered_templates() -> Iterator[list[str]]:
    names: list[str] = []

    def record(sender: Any, template: Any, **kwargs: Any) -> None:
        if (template.name or "").startswith("django_spec_html/"):
            names.append(template.name)

    template_rendered.connect(record)
    yield names
    template_rendered.disconnect(record)


class TestAnHtmxAdd:
    def test_answers_only_the_new_row_and_an_out_of_band_total(self, ada: Any) -> None:
        spec = many_spec()
        response = post(adding(spec), ada, posted("One", add="Add another"), ROWS)
        content = response.content.decode()
        assert response.status_code == 200
        assert content.lstrip().startswith("<fieldset>")
        assert "<legend>Row 2</legend>" in content
        assert 'name="form-1-title"' in content
        assert "form-0-title" not in content
        assert "<form" not in content
        total = oob_total(content)
        assert 'name="form-TOTAL_FORMS"' in total
        assert 'value="2"' in total
        assert 'id="id_form-TOTAL_FORMS"' in total
        assert 'hx-swap-oob="true"' in total
        assert spec.service.calls == []

    def test_the_new_row_is_built_with_the_form_kwargs(self, ada: Any) -> None:
        validator = FormValidator(OwnedRow, form_kwargs=lambda *, user: {"owner": user})
        response = post(adding(many_spec(validator)), ada, posted("One", add="+"), ROWS)
        assert 'name="form-1-title" value="by ada"' in response.content.decode()

    def test_the_row_renders_through_the_row_template(
        self, ada: Any, rendered_templates: list[str]
    ) -> None:
        post(adding(many_spec()), ada, posted("One", add="+"), ROWS)
        assert rendered_templates == [ROW_TEMPLATE]

    def test_at_the_list_s_maximum_it_is_204_with_no_body(self, ada: Any) -> None:
        spec = many_spec(many_max_length=2)
        response = post(adding(spec), ada, posted("One", "Two", add="+"), ROWS)
        assert (response.status_code, response.content) == (204, b"")

    def test_a_broken_management_form_is_the_formset_again(self, ada: Any) -> None:
        response = post(adding(many_spec()), ada, {"form-0-title": "One", "add": "+"}, ROWS)
        assert (response.status_code, len(response.context_data["formset"].forms)) == (200, 0)


class TestEveryOtherPostIsTheFormsetAgain:
    """An add the rows container did not ask for re-renders the formset with one more form."""

    def test_an_htmx_add_targeting_elsewhere(self, ada: Any) -> None:
        headers = {"HTTP_HX_REQUEST": "true"}
        response = post(adding(many_spec()), ada, posted("One", add="+"), headers)
        assert len(response.context_data["formset"].forms) == 2

    def test_a_plain_add_carrying_the_target_header(self, ada: Any) -> None:
        headers = {"HTTP_HX_TARGET": "spec-form-set-rows"}
        response = post(adding(many_spec()), ada, posted("One", add="+"), headers)
        assert len(response.context_data["formset"].forms) == 2

    def test_an_htmx_post_that_neither_adds_nor_commits(self, ada: Any) -> None:
        response = post(adding(many_spec()), ada, posted("One"), ROWS)
        assert len(response.context_data["formset"].forms) == 1


class TestTheShippedFragment:
    def test_renders_its_rows_through_the_row_template_inside_the_container(
        self, ada: Any, rendered_templates: list[str]
    ) -> None:
        view = SpecFormSetView.as_view(spec=many_spec(), success_url="/done/", extra=2)
        content = view(signed_in(FACTORY.get("/"), ada)).render().content.decode()
        rows = content[content.index('<div id="spec-form-set-rows">') :]
        rows = rows[: rows.index("</div>\n<button")]
        assert rows.count("<fieldset>") == 2
        assert "<legend>Row 2</legend>" in rows
        assert rendered_templates.count(ROW_TEMPLATE) == 2

    def test_the_add_button_posts_its_row_into_the_container(self, ada: Any) -> None:
        content = adding(many_spec())(signed_in(FACTORY.get("/"), ada)).render().content.decode()
        add = (
            '<button type="submit" name="add" formnovalidate hx-post="" '
            'hx-target="#spec-form-set-rows" hx-swap="beforeend">Add another</button>'
        )
        assert add in content
