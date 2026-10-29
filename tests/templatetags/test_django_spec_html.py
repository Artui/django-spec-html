"""The ``available`` filter, asked of rows as ``SpecSelectorView`` serves them.

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

from django_spec_html.templatetags.django_spec_html import available
from django_spec_html.views.utils import answered_rows
from tests.dispatch.utils import OPEN, RENAME, notes_of

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


@pytest.mark.parametrize("row", [dict(LIVE), ("live",), None], ids=["unserved", "tuple", "none"])
def test_a_row_the_page_did_not_answer_raises_naming_its_type(row: Any) -> None:
    with pytest.raises(TemplateSyntaxError) as refused:
        available(row, "rename")

    assert str(refused.value).startswith(
        f"available was asked about a {type(row).__name__} that carries no affordance answers"
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


def test_a_misspelt_name_under_not_is_swallowed_by_django() -> None:
    # Held because the filter's docstring tells a template author to write the
    # unavailable branch as the ``{% else %}``: under ``not`` Django's
    # ``{% if %}`` reads the error as the whole condition false, so an
    # archived row's notice is never drawn and nothing raises.
    row = answered(SPEC, dict(ARCHIVED))

    assert rendered('{% if not row|available:"renmae" %}archived{% endif %}', row) == ""
    with pytest.raises(TemplateSyntaxError):
        rendered('{% if row|available:"renmae" %}x{% else %}archived{% endif %}', row)
