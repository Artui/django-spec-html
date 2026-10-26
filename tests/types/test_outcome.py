"""``Outcome``: the five ways a page's post ends."""

from __future__ import annotations

import sys

from django_spec_html.types.outcome import Outcome


def test_the_five_outcomes_and_their_values() -> None:
    assert {member.name: member.value for member in Outcome} == {
        "OK": "ok",
        "INVALID": "invalid",
        "NEEDS_INPUT": "needs_input",
        "DENIED": "denied",
        "FAILED": "failed",
    }


def test_an_outcome_compares_equal_to_its_value() -> None:
    # A ``str`` enum, so a template or a log compares one with its value,
    # and reads one back from it, on every Python this package supports.
    assert Outcome.NEEDS_INPUT == "needs_input"
    assert Outcome("needs_input") is Outcome.NEEDS_INPUT
    assert isinstance(Outcome.OK, str)


def test_its_spelling_is_not_its_value() -> None:
    # Holds the docstring: ``str()`` spells the member on every Python, and
    # an f-string spells its value only before 3.11, so neither is the value.
    assert str(Outcome.OK) == "Outcome.OK"
    assert f"{Outcome.OK}" == ("ok" if sys.version_info < (3, 11) else "Outcome.OK")
    assert Outcome.OK.value == "ok"
