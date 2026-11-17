from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from django.http import HttpResponse
from django.template import Context, Template, TemplateSyntaxError
from django.urls import include, path
from django_service_specs import (
    Output,
    OutputField,
    Parameter,
    Parameters,
    Presenter,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
)

from django_spec_html.templatetags.django_spec_html import available, register
from django_spec_html.views.utils import answered_rows
from tests.dispatch.utils import (
    BOOKS_OPEN,
    NOT_ARCHIVED,
    OPEN,
    PK,
    REFUSED_ARCHIVED,
    RENAME,
    Record,
    note_by_pk,
    notes_of,
)

SPEC = SelectorSpec(
    kind=SelectorKind.LIST, selector=notes_of, permissions=OPEN, affordances={"rename": RENAME}
)
BARE = SelectorSpec(kind=SelectorKind.LIST, selector=notes_of, permissions=OPEN)
LIVE = {"affordance__rename__note_archived": True, "affordance__rename__books_closed": True}
ARCHIVED = {"affordance__rename__note_archived": False, "affordance__rename__books_closed": True}


def rendered(source: str, row: Any) -> str:
    return Template("{% load django_spec_html %}" + source).render(Context({"row": row}))


def answered(spec: SelectorSpec, row: Any, link_routes: SpecRegistry | None = None) -> Any:
    (each,) = answered_rows(spec, [row], link_routes)
    return each


@pytest.mark.parametrize(
    ("flags", "shown"), [(LIVE, "rename"), (ARCHIVED, "-rename")], ids=["live", "archived"]
)
@pytest.mark.parametrize(
    "carrier", [dict, lambda flags: SimpleNamespace(**flags)], ids=["mapping", "object"]
)
def test_a_row_answers_what_its_flags_say(flags: dict[str, bool], shown: str, carrier: Any) -> None:
    row = answered(SPEC, carrier(flags))

    assert (
        rendered('{% if row|available:"rename" %}rename{% else %}-rename{% endif %}', row) == shown
    )


def test_the_answer_is_a_bool() -> None:
    assert available(answered(SPEC, dict(LIVE)), "rename") is True


def test_a_name_the_spec_does_not_declare_raises_naming_those_it_does() -> None:
    row = answered(SPEC, dict(LIVE))

    with pytest.raises(TemplateSyntaxError) as refused:
        rendered('{% if row|available:"renmae" %}x{% endif %}', row)

    assert str(refused.value) == (
        "available was asked about 'renmae', which this row's spec does not declare among its "
        "affordances; it declares 'rename'."
    )


def test_a_spec_presenting_no_affordances_declares_none() -> None:
    with pytest.raises(TemplateSyntaxError, match=r"; it declares none\.$"):
        available(answered(BARE, {}), "rename")


@pytest.mark.parametrize("asked", ["available", "unavailable"])
@pytest.mark.parametrize("row", [dict(LIVE), ("live",), None], ids=["unserved", "tuple", "none"])
def test_a_row_the_page_did_not_answer_raises_naming_its_type(row: Any, asked: str) -> None:
    # Resolved by name, so the module still imports where a filter is missing.
    with pytest.raises(TemplateSyntaxError) as refused:
        register.filters[asked](row, "rename")

    assert str(refused.value).startswith(
        f"{asked} was asked about a {type(row).__name__} that carries no affordance answers"
    )


def test_a_row_that_takes_no_attribute_is_served_without_answers() -> None:
    row = answered(BARE, ("live",))

    assert row == ("live",)
    with pytest.raises(TemplateSyntaxError, match=r"about a tuple that carries no"):
        available(row, "rename")


def test_a_mapping_row_is_copied_and_the_selector_s_own_left_alone() -> None:
    own = dict(LIVE)

    row = answered(SPEC, own)

    assert row == own
    assert row is not own
    with pytest.raises(TemplateSyntaxError, match=r"about a dict that carries no"):
        available(own, "rename")


@pytest.mark.parametrize("asked", ["available", "unavailable"])
def test_a_misspelt_name_under_not_is_swallowed_by_django(asked: str) -> None:
    # The filter's docstring says to read a negated condition through {% with %}.
    row = answered(SPEC, dict(ARCHIVED))

    assert rendered(f'{{% if not row|{asked}:"renmae" %}}archived{{% endif %}}', row) == ""
    with pytest.raises(TemplateSyntaxError, match=rf"^{asked} was asked about 'renmae'"):
        rendered(f'{{% if row|{asked}:"renmae" %}}x{{% else %}}archived{{% endif %}}', row)


WHY = (
    '{% with why=row|unavailable:"rename" %}'
    "{% if why %}[{{ why.code }}|{{ why.reason }}]{% else %}open{% endif %}{% endwith %}"
)
GONE = {"affordance__rename__note_archived": None, "affordance__rename__books_closed": True}


def test_an_unmet_affordance_renders_its_code_and_reason() -> None:
    row = answered(SPEC, dict(ARCHIVED))

    assert rendered(WHY, row) == "[note_archived|An archived note cannot be renamed.]"


def test_an_available_action_has_no_reason() -> None:
    assert rendered(WHY, answered(SPEC, dict(LIVE))) == "open"


def test_unavailable_is_none_for_an_available_action_and_the_answer_otherwise() -> None:
    unavailable = register.filters["unavailable"]

    assert unavailable(answered(SPEC, dict(LIVE)), "rename") is None
    assert dict(unavailable(answered(SPEC, dict(ARCHIVED)), "rename")) == REFUSED_ARCHIVED


def test_a_row_that_no_longer_exists_is_unavailable_with_no_reason() -> None:
    # A gone row's condition has no code or reason; {% if why %} still holds.
    assert rendered(WHY, answered(SPEC, dict(GONE))) == "[|]"


def test_a_template_cannot_change_the_answers_the_row_carries() -> None:
    # Django calls zero-argument callables, so each unmarked method renders here.
    row = answered(SPEC, dict(ARCHIVED))

    cleared = rendered(
        '{% with why=row|unavailable:"rename" %}{{ why.clear }}{{ why.popitem }}'
        "{% if why %}held{% endif %}"
        "{% endwith %}",
        row,
    )

    assert cleared == "held"

    assert rendered(WHY, row) == "[note_archived|An archived note cannot be renamed.]"
    assert rendered('{% if row|available:"rename" %}x{% else %}-x{% endif %}', row) == "-x"


def test_python_may_change_its_copy_and_the_row_keeps_its_answer() -> None:
    # Only a template is refused the two methods; a Python caller holds a fresh copy per call.
    unavailable = register.filters["unavailable"]
    row = answered(SPEC, dict(ARCHIVED))
    popped = unavailable(row, "rename")
    cleared = unavailable(row, "rename")

    assert popped.popitem() == ("reason", "An archived note cannot be renamed.")
    cleared.clear()

    assert cleared == {}
    assert dict(unavailable(row, "rename")) == REFUSED_ARCHIVED


def test_the_answer_renders_through_json_script() -> None:
    # The answer is a dict, so a template can hand it to a script.
    row = answered(SPEC, dict(ARCHIVED))

    script = rendered('{{ row|unavailable:"rename"|json_script:"why" }}', row)

    assert script.startswith('<script id="why" type="application/json">')
    assert json.loads(script.split(">", 1)[1].rsplit("<", 1)[0]) == REFUSED_ARCHIVED


def test_unavailable_raises_for_a_name_the_spec_does_not_declare() -> None:
    row = answered(SPEC, dict(LIVE))

    with pytest.raises(TemplateSyntaxError) as refused:
        rendered('{% with why=row|unavailable:"renmae" %}{% endwith %}', row)

    assert str(refused.value) == (
        "unavailable was asked about 'renmae', which this row's spec does not declare among its "
        "affordances; it declares 'rename'."
    )


@pytest.mark.parametrize("asked", ["available", "unavailable"])
def test_a_misspelt_name_read_through_with_raises(asked: str) -> None:
    row = answered(SPEC, dict(ARCHIVED))

    with pytest.raises(TemplateSyntaxError) as refused:
        rendered(
            f'{{% with ok=row|{asked}:"renmae" %}}{{% if not ok %}}x{{% endif %}}{{% endwith %}}',
            row,
        )

    assert str(refused.value).startswith(f"{asked} was asked about 'renmae'")


class NoteRows(Presenter):
    """A note as its ``pk`` and ``title``, the fields a rename's link binds."""

    def output(self) -> Output:
        return Output((OutputField("pk", "integer"), OutputField("title", "string")))

    def present(self, value: Any) -> Any:
        return {"pk": value.pk, "title": value.title}


# The rename reads ``title`` too, which the row carries, and no route captures.
RENAME_NOTE = ServiceSpec(
    service=Record(),
    permissions=OPEN,
    reads=Parameters.of(Parameter("title", "string")),
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=note_by_pk, reads=PK),
    affordances=[NOT_ARCHIVED, BOOKS_OPEN],
)
LINKED = SelectorSpec(
    kind=SelectorKind.LIST,
    selector=notes_of,
    permissions=OPEN,
    presenter=NoteRows(),
    affordances={"rename": RENAME_NOTE},
)


def _view(request: Any, **kwargs: Any) -> HttpResponse:
    return HttpResponse()


urlpatterns = [
    path("notes/<int:pk>/rename/", _view, name="rename_note"),
    path("notes/<slug:slug>/", _view, name="note_by_slug"),
    path("notes/<str:pk>/", _view, name="note_by_str"),
    path("ns/", include(([path("<int:pk>/rename/", _view, name="rename")], "notes"))),
]


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = __name__


def routes(**named: ServiceSpec) -> SpecRegistry:
    registry = SpecRegistry()
    for name, spec in named.items():
        registry.register(name.replace("__", ":"), spec)
    return registry


def linked(flags: dict[str, Any], **named: ServiceSpec) -> Any:
    return answered(LINKED, {**flags, "pk": 7, "title": "old"}, routes(**named))


LINK = (
    '{% spec_link row "rename" as link %}'
    "{% if link.available %}{{ link.url }}"
    "{% else %}[{{ link.code }}|{{ link.reason }}|{{ link.url }}]{% endif %}"
)


def test_a_link_renders_the_bound_url() -> None:
    row = linked(LIVE, rename_note=RENAME_NOTE)

    assert rendered('{% spec_link row "rename" %}', row) == "/notes/7/rename/"


def test_a_link_keeps_its_answer_under_as() -> None:
    row = linked(LIVE, rename_note=RENAME_NOTE)

    link = register.tags["spec_link"]
    assert rendered(LINK, row) == "/notes/7/rename/"
    assert dict(link.__wrapped__(row, "rename")) == {
        "available": True,
        "name": "rename_note",
        "arguments": {"pk": 7, "title": "old"},
        "url": "/notes/7/rename/",
    }


def test_an_unavailable_link_carries_its_reason() -> None:
    row = linked(ARCHIVED, rename_note=RENAME_NOTE)

    assert rendered(LINK, row) == (
        "[note_archived|An archived note cannot be renamed.|/notes/7/rename/]"
    )


def test_a_namespaced_pattern_name_is_reversed() -> None:
    row = linked(LIVE, notes__rename=RENAME_NOTE)

    assert rendered('{% spec_link row "rename" %}', row) == "/ns/7/rename/"


def test_the_url_is_escaped_like_any_value() -> None:
    row = answered(LINKED, {**LIVE, "pk": "a&b", "title": "old"}, routes(note_by_str=RENAME_NOTE))

    assert rendered('{% spec_link row "rename" %}', row) == "/notes/a&amp;b/"


def test_a_page_with_no_link_routes_raises_naming_the_link() -> None:
    row = answered(LINKED, {**LIVE, "pk": 7, "title": "old"})

    with pytest.raises(TemplateSyntaxError) as refused:
        rendered('{% spec_link row "rename" %}', row)

    assert str(refused.value) == (
        "spec_link 'rename' was asked about a row a SpecSelectorView served with no link_routes: "
        "set link_routes to a SpecRegistry naming each operation by its URL pattern name."
    )


@pytest.mark.parametrize("flags", [LIVE, ARCHIVED], ids=["available", "unavailable"])
def test_an_operation_link_routes_does_not_name_raises(flags: dict[str, Any]) -> None:
    row = linked(flags, rename_note=RENAME)

    with pytest.raises(TemplateSyntaxError) as refused:
        rendered('{% spec_link row "rename" %}', row)

    assert str(refused.value) == (
        "spec_link 'rename' leads to an operation link_routes does not name: register its spec "
        "there under the name of the URL pattern serving it."
    )


def test_an_operation_named_twice_raises_naming_the_link() -> None:
    row = linked(LIVE, rename_note=RENAME_NOTE, notes__rename=RENAME_NOTE)

    with pytest.raises(TemplateSyntaxError) as refused:
        rendered('{% spec_link row "rename" %}', row)

    assert str(refused.value).startswith(
        "spec_link 'rename': The spec is registered under ['rename_note', 'notes:rename']"
    )


@pytest.mark.parametrize(
    "name", ["note_by_slug", "missing__rename", "no_such_pattern"], ids=["kwargs", "ns", "name"]
)
@pytest.mark.parametrize("flags", [LIVE, ARCHIVED], ids=["available", "unavailable"])
def test_a_pattern_reverse_cannot_fill_raises_naming_the_link(
    name: str, flags: dict[str, Any]
) -> None:
    row = answered(LINKED, {**flags, "title": "old"} | {"pk": 7}, routes(**{name: RENAME_NOTE}))

    with pytest.raises(TemplateSyntaxError) as refused:
        rendered('{% spec_link row "rename" %}', row)

    assert str(refused.value).startswith(
        f"spec_link 'rename' leads to URL pattern {name.replace('__', ':')!r}, which cannot be "
        "reversed with {'pk': 7, 'title': 'old'}: "
    )


def test_spec_link_raises_for_a_row_the_page_did_not_serve() -> None:
    with pytest.raises(TemplateSyntaxError, match=r"^spec_link was asked about a dict that"):
        rendered('{% spec_link row "rename" %}', {"pk": 7})


def test_spec_link_raises_for_a_name_the_spec_does_not_declare() -> None:
    row = linked(LIVE, rename_note=RENAME_NOTE)

    with pytest.raises(TemplateSyntaxError, match=r"^spec_link was asked about 'renmae'"):
        rendered('{% spec_link row "renmae" %}', row)


def test_a_row_missing_a_required_read_s_field_raises_naming_the_link() -> None:
    row = answered(LINKED, {**LIVE, "title": "old"}, routes(rename_note=RENAME_NOTE))

    with pytest.raises(TemplateSyntaxError) as refused:
        rendered('{% spec_link row "rename" %}', row)

    assert str(refused.value).startswith(
        "spec_link 'rename': SelectorSpec.affordances['rename'] binds a required read to ['pk'], "
        "which a dict row does not carry."
    )
