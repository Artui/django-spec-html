from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from django import template
from django.template import TemplateSyntaxError

from django_spec_html.views.utils import row_answers

register = template.Library()

_Method = TypeVar("_Method", bound=Callable[..., Any])


@register.filter
def available(row: Any, name: str) -> bool:
    """Whether the affordance ``name`` is available for ``row``, as a template reads it."""
    return bool(_answer(row, name, "available")["available"])


@register.filter
def unavailable(row: Any, name: str) -> dict[str, Any] | None:
    """Why the affordance ``name`` is unavailable for ``row``, or ``None`` when it is available."""
    answer = _answer(row, name, "unavailable")
    if answer["available"]:
        return None
    return _Answer(answer)


def _alters_data(method: _Method) -> _Method:
    # A type checker refuses an ``alters_data`` attribute set directly on a function.
    vars(method)["alters_data"] = True
    return method


class _Answer(dict):
    """A copy of one row's answer that a template cannot empty."""

    @_alters_data
    def clear(self) -> None:
        super().clear()

    @_alters_data
    def popitem(self) -> tuple[str, Any]:
        return super().popitem()


def _answer(row: Any, name: str, asked: str) -> dict[str, Any]:
    """``row``'s answer for ``name``, raising the template's error in the filter's words."""
    answers = row_answers(row)
    if answers is None:
        raise TemplateSyntaxError(
            f"{asked} was asked about a {type(row).__name__} that carries no affordance "
            "answers: only a row SpecSelectorView served carries them - its rows, row, a "
            "display's row, or its table's records - and a row that takes no attribute, "
            "such as a tuple, carries none."
        )
    answer = answers.get(name)
    if answer is None:
        declared = ", ".join(repr(each) for each in answers) or "none"
        raise TemplateSyntaxError(
            f"{asked} was asked about {name!r}, which this row's spec does not declare among "
            f"its affordances; it declares {declared}."
        )
    return answer
