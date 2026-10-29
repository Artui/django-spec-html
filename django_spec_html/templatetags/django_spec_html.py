"""``{% load django_spec_html %}`` - the ``available`` filter, a served row asked what may be done to it.

The file is named for the library, since ``{% load %}`` names the file, and
the library for the package, so a template reads which package it loads.
"""

from __future__ import annotations

from typing import Any

from django import template
from django.template import TemplateSyntaxError

from django_spec_html.views.utils import row_answers

register = template.Library()


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
    tag silences as a missing variable. **Use it bare, as an** ``{% if %}``
    **or** ``{% elif %}`` **condition on its own**: Django's ``{% if %}``
    reads any error raised inside ``not``, ``and``, ``or`` or a comparison
    as that whole expression being false, so
    ``{% if not row|available:"renmae" %}`` draws its branch for no row and
    raises nothing (test_a_misspelt_name_under_not_is_swallowed_by_django).
    Write the unavailable branch as the ``{% else %}``.

    Raises:
        TemplateSyntaxError: ``row`` carries no answers, or none for ``name``,
            naming the names it carries.
    """
    answers = row_answers(row)
    if answers is None:
        raise TemplateSyntaxError(
            f"available was asked about a {type(row).__name__} that carries no affordance "
            "answers: only a row SpecSelectorView served carries them - its rows, row, a "
            "display's row, or its table's records - and a row that takes no attribute, "
            "such as a tuple, carries none."
        )
    answer = answers.get(name)
    if answer is None:
        declared = ", ".join(repr(each) for each in answers) or "none"
        raise TemplateSyntaxError(
            f"available was asked about {name!r}, which this row's spec does not declare among "
            f"its affordances; it declares {declared}."
        )
    return bool(answer["available"])
