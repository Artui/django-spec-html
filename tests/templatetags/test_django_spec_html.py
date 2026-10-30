"""The ``available`` and ``unavailable`` filters, asked of rows as ``SpecSelectorView`` serves them.

The rows are answered here by the helper the view answers them with, from
flags written by hand in the kernel's spelling, so each test reads one
template against one row with no page around it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.template import Context, Template, TemplateSyntaxError
from django_service_specs import SelectorKind, SelectorSpec

from django_spec_html.templatetags.django_spec_html import available, register
from django_spec_html.views.utils import answered_rows
from tests.dispatch.utils import OPEN, REFUSED_ARCHIVED, RENAME, notes_of

SPEC = SelectorSpec(
    kind=SelectorKind.LIST, selector=notes_of, permissions=OPEN, affordances={"rename": RENAME}
)
BARE = SelectorSpec(kind=SelectorKind.LIST, selector=notes_of, permissions=OPEN)
LIVE = {"affordance__rename__note_archived": True, "affordance__rename__books_closed": True}
ARCHIVED = {"affordance__rename__note_archived": False, "affordance__rename__books_closed": True}


def rendered(source: str, row: Any) -> str:
    return Template("{% load django_spec_html %}" + source).render(Context({"row": row}))


def answered(spec: SelectorSpec, row: Any) -> Any:
    (each,) = answered_rows(spec, [row])
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
    # Held because the filter's docstring tells a template author to read a
    # negated condition through ``{% with %}``: under ``not`` Django's
    # ``{% if %}`` reads the error as the whole condition false, so an
    # archived row's notice is never drawn and nothing raises.
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
    # The kernel answers a condition on a row that is gone with no code and no
    # reason, since no condition's sentence is true of it; ``{% if why %}``
    # still holds, and the code and reason render as missing variables.
    assert rendered(WHY, answered(SPEC, dict(GONE))) == "[|]"


def test_a_template_cannot_change_the_answers_the_row_carries() -> None:
    # Django calls a zero-argument callable a template names, so the row's
    # own answer would be emptied by ``{{ why.clear }}`` for every later read,
    # and a copy of it would still be emptied under the rest of the block.
    row = answered(SPEC, dict(ARCHIVED))

    cleared = rendered(
        '{% with why=row|unavailable:"rename" %}{{ why.clear }}{% if why %}held{% endif %}'
        "{% endwith %}",
        row,
    )

    assert cleared == "held"

    assert rendered(WHY, row) == "[note_archived|An archived note cannot be renamed.]"
    assert rendered('{% if row|available:"rename" %}x{% else %}-x{% endif %}', row) == "-x"


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
