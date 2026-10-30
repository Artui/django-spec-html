"""``{% load django_spec_html %}`` - the ``available`` and ``unavailable`` filters, a served row asked what may be done to it.

The file is named for the library, since ``{% load %}`` names the file, and
the library for the package, so a template reads which package it loads.
"""

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
    """Whether the affordance ``name`` is available for ``row``: ``{% if row|available:"rename" %}``.

    Read off what ``SpecSelectorView`` attached to each row it served -
    ``rows``, ``row``, each display's ``row`` and each record of its
    ``table`` - which is the kernel's ``row_affordance_answers``, the flags
    dispatch answered for every row in bulk. So asking costs no query, and a
    row's button follows the rule the operation's dispatch enforces: the
    first of its conditions the row does not meet makes it unavailable.

    **A name the row's spec does not declare raises**, rather than reading
    ``False``, since a misspelt name would otherwise hide a button for good.
    A spec presenting no affordances declares none, so every name raises
    there. A row the page did not serve carries no answers and raises too,
    and so does one that cannot carry an attribute, such as a tuple from
    ``values_list()``.

    The error is a ``TemplateSyntaxError``, the template's own mistake,
    which Django reports with the template's line under ``DEBUG`` and no
    tag silences as a missing variable. **But Django's** ``{% if %}``
    **swallows it inside** ``not``, ``and``, ``or`` **or a comparison**,
    reading the error as that whole expression being false, so
    ``{% if not row|available:"renmae" %}`` draws its branch for no row and
    raises nothing (test_a_misspelt_name_under_not_is_swallowed_by_django).
    Write a condition on its own as ``{% if row|available:"rename" %}``, and
    every negated or combined one through ``{% with %}``, which resolves the
    filter outside ``{% if %}``, so a misspelt name raises
    (test_a_misspelt_name_read_through_with_raises):

        {% with ok=row|available:"rename" %}{% if not ok %}...{% endif %}{% endwith %}

    Raises:
        TemplateSyntaxError: ``row`` carries no answers, or none for ``name``,
            naming the names it carries.
    """
    return bool(_answer(row, name, "available")["available"])


@register.filter
def unavailable(row: Any, name: str) -> dict[str, Any] | None:
    """Why the affordance ``name`` is unavailable for ``row``, or ``None`` when it is available.

    ``{% with why=row|unavailable:"rename" %}{% if why %}{{ why.reason }}{% endif %}{% endwith %}``

    The answer is the one ``available`` reads, so it costs no query: the
    kernel's ``row_affordance_answers`` for the row, ``available`` with the
    ``code`` and ``reason`` of the first condition the row does not meet
    (test_an_unmet_affordance_renders_its_code_and_reason). A condition on
    the row asked of a row that no longer exists answers with no code and no
    reason, since no condition's sentence is true of a missing row: the
    answer is still returned, so ``{% if why %}`` holds and ``why.reason``
    renders as a missing variable
    (test_a_row_that_no_longer_exists_is_unavailable_with_no_reason).

    It raises as ``available`` does, for the same rows and names, and the
    same ``{% if %}`` swallows the error inside ``not``, ``and``, ``or`` or a
    comparison: read it through ``{% with %}``, which resolves the filter
    outside ``{% if %}`` (test_a_misspelt_name_read_through_with_raises).

    Raises:
        TemplateSyntaxError: ``row`` carries no answers, or none for ``name``,
            naming the names it carries.
    """
    answer = _answer(row, name, "unavailable")
    if answer["available"]:
        return None
    return _Answer(answer)


def _alters_data(method: _Method) -> _Method:
    """``method``, marked as Django marks a model's ``save``, so no template calls it."""
    # Through the function's namespace, since a type checker sees no
    # ``alters_data`` attribute on a function and refuses one set directly.
    vars(method)["alters_data"] = True
    return method


class _Answer(dict):
    """A copy of one row's answer that a template cannot empty.

    Django calls a zero-argument callable a template names, so the row's own
    answer would be emptied by ``{{ why.clear }}`` for every later read of the
    row on the page, and a plain copy would still be emptied under the rest
    of the template. Django refuses to call a method marked ``alters_data``,
    so the two that take no argument are marked
    (test_a_template_cannot_change_the_answers_the_row_carries). A ``dict``
    rather than a read-only view, which ``json_script`` and ``json.dumps``
    refuse (test_the_answer_renders_through_json_script). A fresh copy per
    call, so nothing a caller in Python does to it reaches the row.
    """

    @_alters_data
    def clear(self) -> None:
        super().clear()

    @_alters_data
    def popitem(self) -> tuple[str, Any]:
        return super().popitem()


def _answer(row: Any, name: str, asked: str) -> dict[str, Any]:
    """``row``'s answer for ``name``, raising the template's error in the words of the filter ``asked``."""
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
