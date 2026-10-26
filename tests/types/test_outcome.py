"""``Outcome``: the five ways a page's post ends."""

from __future__ import annotations

from django_spec_html.types.outcome import Outcome


def test_the_five_outcomes_and_their_values() -> None:
    assert {member.name: member.value for member in Outcome} == {
        "OK": "ok",
        "INVALID": "invalid",
        "NEEDS_INPUT": "needs_input",
        "DENIED": "denied",
        "FAILED": "failed",
    }


def test_an_outcome_is_its_value() -> None:
    # A ``str`` enum, so a template or a log compares one with its value,
    # read back from it, on every Python this package supports.
    assert Outcome.NEEDS_INPUT == "needs_input"
    assert Outcome("needs_input") is Outcome.NEEDS_INPUT
    assert isinstance(Outcome.OK, str)
