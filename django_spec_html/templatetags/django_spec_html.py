from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

from django import template
from django.core.exceptions import ImproperlyConfigured
from django.template import Context, TemplateSyntaxError
from django.urls import NoReverseMatch, reverse
from django_service_specs import row_links

from django_spec_html.views.utils import RowSource, row_answers, row_source

register = template.Library()

_Method = TypeVar("_Method", bound=Callable[..., Any])


@register.filter
def available(row: Any, name: str) -> bool:
    """Whether affordance ``name`` is available for ``row``: ``{% if row|available:"rename" %}``.

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


@register.simple_tag(takes_context=True)
def spec_link(context: Context, row: Any, name: str) -> _Link:
    """The link affordance ``name`` leads to from ``row``: ``{% spec_link row "rename" %}``.

    Renders the URL of the view serving the operation, reversed at render
    from the URL pattern name ``SpecSelectorView.link_routes`` holds the
    operation under, with the row's values for the link's required reads,
    those naming the row, as the pattern's kwargs: the kernel's ``row_links``
    for the row, read with ``link_routes`` as its registry
    (test_a_link_renders_the_bound_url). An optional read stays out of the
    URL, for the page it leads to to ask.

    ``{% spec_link row "rename" as link %}`` keeps the whole answer:
    ``link.url``, ``link.available``, and, when it is not, ``link.code`` and
    ``link.reason``, as ``unavailable`` answers them, so a row draws the
    link or why it cannot (test_an_unavailable_link_carries_its_reason). An
    unavailable link still has its URL, since dispatch, not the link,
    refuses the call. A row's links are read once, however many tags ask,
    and an application namespace resolves to the request's ``current_app``
    as ``{% url %}`` resolves it.

    **What cannot be resolved raises at render**, naming the link: a row the
    page did not serve, a name its spec does not declare, a page with no
    ``link_routes``, a row missing the field a required read binds to, an
    operation ``link_routes`` does not name or names twice, and a pattern
    ``reverse`` cannot fill with exactly the required reads. It raises for an
    unavailable row too, so a broken route shows on the first render. A
    tag is never inside an ``{% if %}``, so nothing swallows the error, as
    ``{% if %}`` swallows the ``available`` filter's inside ``not``.

    Raises:
        TemplateSyntaxError: the link cannot be resolved, naming it.
    """
    _answer(row, name, "spec_link")
    source = cast(RowSource, row_source(row))
    where = f"spec_link {name!r}"
    if source.link_routes is None:
        raise TemplateSyntaxError(
            f"{where} was asked about a row a SpecSelectorView served with no link_routes: set "
            "link_routes to a SpecRegistry naming each operation by its URL pattern name."
        )
    if not source.links:
        try:
            source.links.update(row_links(source.spec, source.row, registry=source.link_routes))
        except (ImproperlyConfigured, ValueError) as refused:
            raise TemplateSyntaxError(f"{where}: {refused}") from refused
    link = source.links[name]
    if "name" not in link:
        raise TemplateSyntaxError(
            f"{where} leads to an operation link_routes does not name: register its spec there "
            "under the name of the URL pattern serving it."
        )
    kwargs = {read: link["arguments"][read] for read in link["required"]}
    try:
        url = reverse(link["name"], kwargs=kwargs, current_app=_current_app(context))
    except NoReverseMatch as refused:
        raise TemplateSyntaxError(
            f"{where} leads to URL pattern {link['name']!r}, which cannot be reversed with its "
            f"required reads {kwargs!r}: {refused}"
        ) from refused
    return _Link(
        {
            key: link[key]
            for key in ("available", "code", "reason", "name", "arguments")
            if key in link
        },
        url=url,
    )


def _current_app(context: Context) -> str | None:
    """The ``current_app`` ``{% url %}`` reverses with: the request's, else its namespace."""
    request = getattr(context, "request", None)
    current_app = getattr(request, "current_app", None)
    if current_app is not None:
        return current_app
    return getattr(getattr(request, "resolver_match", None), "namespace", None)


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


class _Link(_Answer):
    """One row's link: its answer, and the URL it renders as."""

    def __init__(self, answer: dict[str, Any], *, url: str) -> None:
        super().__init__(answer, url=url)

    def __str__(self) -> str:
        return self["url"]


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
