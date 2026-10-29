"""``SpecSelectorView`` - a selector spec served as a page: a list of rows, or one row."""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Callable, Mapping
from typing import Any, cast

from django.core.exceptions import ImproperlyConfigured
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBase, QueryDict
from django.utils.decorators import classonlymethod
from django_service_specs import (
    DispatchError,
    DispatchResult,
    OutputPage,
    SelectorKind,
    SelectorSpec,
    ServiceError,
    SpecSource,
    dispatch,
    kernel_spec,
    paginate_output,
    refusal_messages,
)

from django_spec_html.http.add_argument_errors import add_argument_errors
from django_spec_html.http.request_arguments import request_arguments
from django_spec_html.http.utils import check_request_hooks, refusal_outcome, refusal_status
from django_spec_html.types.outcome import Outcome
from django_spec_html.views.utils import (
    SPEC_RESULTS,
    SpecRequestBase,
    admitted,
    answered_rows,
    check_display,
    check_shipped_templates,
    denial,
    display_of,
    displayed,
    refusal_code,
    refusal_tree,
    with_binding,
    without_errors,
)


class SpecSelectorView(SpecRequestBase):
    """A ``SelectorSpec`` served as a page: a list's rows a page at a time, or one row.

    The read side of the pages: what a person browses, filters and picks a
    row from, declared once on the spec every other reader serves too. A
    ``LIST`` spec is a list page and a ``RETRIEVE`` spec the page of one row:

        urlpatterns = [
            path("books/", SpecSelectorView.as_view(spec=books_spec)),
            path(
                "books/<int:pk>/",
                SpecSelectorView.as_view(spec=book_spec, route_kwargs=("pk",)),
            ),
        ]

    It is a spec's read page, not a model's list view: what it lists, who
    may see it, which filters it takes and what may be done to each row are
    the spec's, and the page adds only the paging and the wrapper.

    **GET**, and HEAD, which Django answers by the same handler, are the
    methods it answers; anything else is 405. A request is:

    1. **admitted**, as every page admits it: the route checked against the
       spec's parameters, then the principal the class-level check admits,
       a denial raised for the host's 403 or 404 page;
    2. **read** by
       [`request_arguments`][django_spec_html.http.request_arguments.request_arguments]'
       query-string branch - the URL kwargs under ``route_kwargs``, the names
       ``server_arguments`` answers for dropped and then bound - as the JSON
       views read it, so a list page and a JSON list answer one query string
       the same way. On a list, the paging keys are taken off first, so
       ``?page=2`` never reaches the spec's closed argument set;
    3. **dispatched** by the kernel's ``dispatch``, with the grant the
       admission made, so the class-level check is asked once;
    4. **rendered** with the raw rows dispatch answered, and no
       ``present()``: a template reads the rows themselves.

    **The context.** Beside ``view`` and ``spec``:

    - ``rows``: the rows served, a list. On a list page they are the page's
      rows, read in one query; on an instance page, the one row, or none for
      a ``RETRIEVE`` with ``allow_none`` that found nothing.
    - ``row``: the one row, on an instance page that found one.
    - ``page``: on a list page, the kernel's ``OutputPage`` - ``page``,
      ``limit``, ``total``, ``total_pages``, ``has_next`` - its ``items``
      the same list as ``rows``, so a template reading either reads no row
      twice. ``previous_page_query`` and ``next_page_query``, where those
      pages exist, are the request's own query string with the page key
      replaced, every filter kept, for a link's ``?``.
    - ``filter``: the spec's ``filter_set``, a django-filter ``FilterSet``,
      bound to the query for display only, where the spec declares one.
    - ``table``: ``table_class`` built over ``rows``, where it is set.
    - ``row_displays``: with ``display_rows`` on and no ``table_class``,
      each row as its Output marks it for a person - ``label``, ``fields``
      and ``pairs``, as the form page's ``target_display`` - with the raw
      row itself as ``row``, so a template shows the one and asks
      ``available`` of the other.

    **Each row carries its affordances.** Dispatch answers every row's
    affordances in bulk, as the spec's ``affordances`` declare them, and the
    page reads each row's answers once, so ``{% load django_spec_html %}``
    and ``{% if row|available:"rename" %}`` asks a row what the operation's
    own dispatch would enforce, in no query of its own. See
    [`available`][django_spec_html.templatetags.django_spec_html.available].

    **Paging.** A list is paged by the kernel's ``paginate_output``: the
    page named by ``page_kwarg``, ``paginate_by`` rows to a page, never more
    than ``max_page_size``. A page that is not a whole number is page 1, and
    one past the last is the last, rather than a refusal: a public page
    clamps a link someone edited, and the page served says which it is. A
    client chooses the page size only where ``page_size_kwarg`` names the
    key it sends, bounded by ``max_page_size``, which ``as_view()`` then
    requires; a value that is not a whole number is ``paginate_by``, and a
    whole number below one is a page of one row, as ``paginate_output``
    clamps it.

    **The filters are the spec's.** ``filter.form`` is the ``FilterSet``'s
    form bound to the query for display, its own validation never run:
    dispatch reads the query, through the filter set's parameters, and is
    the one reader that decides, since two readers of one query string could
    disagree - django-filter's comma-separated filters split a value their
    own way.

    **A refusal is the page again**, at the status of the one map: an
    argument refusal at 400, a precondition's ``ServiceError`` at 422, and
    so on, with ``errors``, every message the refusal holds as a string, a
    message about a parameter prefixed with its path, and ``code`` for a
    refusal carrying one. The refusal is placed on ``filter.form`` too, an
    argument refusal at the fields it names. A denial is raised, for the
    host's 403 or 404 page, and a ``RETRIEVE`` that finds no row is
    ``Http404``.

    **The fragment.** An htmx request, unless boosted or a history restore,
    is answered with the results alone, at 200 whatever the outcome, as
    every page answers a fragment: ``fragment_template_name``, or the
    ``spec-results`` fragment of ``template_name``, or the shipped page's
    own fragment file. Every answer varies on the three headers that choice
    reads. A filter form's ``hx-get`` answered with it is a live search, and
    a next-page link's is a load-more.

    **A django-tables2 table is composed, never imported.** ``table_class``
    is built with the page's rows and ``order_by=()``, so the table neither
    pages nor orders them: the spec ordered them, and ``paginate_output``
    paged them. Its columns are not orderable unless
    ``table_order_by_field`` names the spec's own ordering parameter, which
    a header link then sets, for dispatch to read like any filter.

    **The page's template is optional**:
    ``django_spec_html/spec_selector.html`` by default, which this package
    ships, its results in ``django_spec_html/spec_selector_fragment.html``.
    It renders the filter form's fields, the table or each row's display,
    and the previous and next links. Found, refused at ``as_view()`` and
    overridden as ``SpecFormView``'s is.

    ``as_view()`` refuses, when the URLconf is imported, a spec that is not
    a ``SelectorSpec`` and does not compile to one; the request hooks as
    every page refuses them; paging attributes of the wrong type, a client
    page size with no ceiling, and a list spec declaring a parameter under a
    paging key, whose value could never reach it; a
    ``table_order_by_field`` the spec does not declare, since every header
    link would then be a 400; and ``display_rows`` on a spec whose Output
    cannot display a row.

    Attributes:
        spec: The read served: a ``SelectorSpec``, or a ``SpecSource``
            compiling to one. Required.
        template_name: The page's template, as for any Django template view:
            ``django_spec_html/spec_selector.html`` by default.
        fragment_template_name: The template an htmx request is answered
            with; ``None`` addresses ``<template_name>#spec-results``, or a
            shipped page's own fragment file.
        route_kwargs: Which URL kwargs fill which parameters, as
            ``route_arguments`` reads it.
        pool_seeds: The seeds the spec's callables are resolved with, unless
            ``get_pool_seeds`` answers others for a request.
        server_arguments: The kernel's ``ServerArguments`` the server answers
            for, checked at ``as_view()``.
        unknown_arguments: What dispatch does with a query key the spec does
            not declare: refused by default, as on the JSON views.
        page_kwarg: The query key naming the page, ``"page"``, as Django's
            ``ListView`` names it.
        paginate_by: Rows to a page; ``None`` is the kernel's
            ``DEFAULT_PAGE_SIZE``.
        max_page_size: The most rows a page holds, whatever ``paginate_by``
            or a client asks; ``None`` sets no ceiling.
        page_size_kwarg: The query key a client chooses the page size by;
            ``None``, the default, lets no client choose.
        table_class: A django-tables2 ``Table`` class, built over the
            page's rows as ``table``; ``None`` builds none.
        table_order_by_field: The spec's ordering parameter, which the
            table's header links set; ``None`` builds the table with no
            orderable column.
        display_rows: Whether the context carries ``row_displays``, which
            the shipped template renders a row by when there is no table.
    """

    spec: SelectorSpec | SpecSource | None = None
    template_name = "django_spec_html/spec_selector.html"
    fragment_name = SPEC_RESULTS
    http_method_names = ["get", "head"]
    page_kwarg: str = "page"
    paginate_by: int | None = None
    max_page_size: int | None = None
    page_size_kwarg: str | None = None
    table_class: Any = None
    table_order_by_field: str | None = None
    display_rows: bool = True

    @classonlymethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., HttpResponseBase]:
        """Django's ``as_view``, then the spec, the request hooks, the paging, the table and the templates checked, once.

        The spec first, so a view with no spec is refused as one whatever
        else it sets; then the request hooks, by the code every page and the
        JSON views check them with; then this page's own attributes, each
        read against the spec; and the shipped templates last.
        """
        # Django's own check of the keywords first, so a misspelt ``sepc=`` is
        # refused as the typo it is rather than read as a view with no spec.
        view = super().as_view(**initkwargs)
        label = cls.__name__
        spec = _selector(label, initkwargs.get("spec", cls.spec))
        check_request_hooks(cls, initkwargs, spec)
        _check_paging(label, spec, {**_attributes(cls), **initkwargs})
        _check_table(
            label,
            spec,
            initkwargs.get("table_class", cls.table_class),
            initkwargs.get("table_order_by_field", cls.table_order_by_field),
        )
        # One branch to coverage, so each condition is held by its own test:
        # test_a_spec_with_no_output_is_refused (the rows are displayed) and
        # test_display_rows_off_needs_no_output and
        # test_a_table_needs_no_output (each takes the display away).
        if (
            initkwargs.get("display_rows", cls.display_rows)
            and initkwargs.get("table_class", cls.table_class) is None
        ):
            check_display(
                label,
                spec,
                shown="its rows",
                remedy="set table_class, or display_rows = False for a template of the "
                "project's own",
            )
        check_shipped_templates(cls, initkwargs)
        return view

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """The page: the spec's rows, or the refusal of the query that asked for them."""
        spec = self.served_spec()
        served = self._served(request)
        principal, grant = admitted(spec, request, kwargs, served)
        listing = spec.kind is SelectorKind.LIST
        query, page, limit = self._paging(request.GET) if listing else (request.GET, None, None)
        # After the admission, so a principal the class-level check refuses
        # is never shown a choice the form lists: its choices may query.
        context: dict[str, Any] = {}
        form: Any = None
        if spec.filter_set is not None:
            context["filter"] = filters = spec.filter_set(data=query, request=request)
            form = filters.form
            # Displayed, never validated: dispatch is the one reader that
            # decides, and its refusal is placed on the form below. With no
            # error state of its own, a field the form would refuse in its own
            # words is not refused twice, or in words dispatch never said
            # (test_the_form_s_own_validation_is_never_run).
            without_errors(form)
        try:
            arguments = request_arguments(
                _reading(request, query),
                spec.parameters(),
                url_kwargs=kwargs,
                route_kwargs=served.route_kwargs,
                server_arguments=served.server,
            )
            result = dispatch(
                spec,
                principal=principal,
                arguments=with_binding(spec, arguments, served.server),
                grant=grant,
                pool_seeds=served.seeds,
                unknown_arguments=self.unknown_arguments,
                call_context=served.call_context,
            )
        except (DispatchError, ServiceError) as refused:
            # Every row's answer is held by test_every_refusal_is_answered_from_the_one_map.
            if refusal_outcome(refused) is Outcome.DENIED:
                raise denial(refused) from refused
            tree = refusal_tree(refused)
            if form is not None:
                add_argument_errors(form, tree)
            context["errors"] = [
                f"{'.'.join(where)}: {message}" if where else message
                for where, message in refusal_messages(tree)
            ]
            context.update(refusal_code(refused))
            return self._rendered(self.get_context_data(**context), status=refusal_status(refused))
        if result.kind == "not_found":
            raise Http404
        if listing:
            served_page = paginate_output(
                result.value, page=page, limit=limit, max_page_size=self.max_page_size
            )
            # One query for the page's rows, here, and never again: the page
            # handed to the template holds the same list, so a template
            # looping ``page.items`` reads what ``rows`` holds
            # (test_page_items_are_the_rows).
            rows = answered_rows(spec, list(served_page.items))
            context["page"] = dataclasses.replace(served_page, items=rows)
            context.update(_links(request.GET, self.page_kwarg, served_page))
        else:
            rows = [] if result.value is None else answered_rows(spec, [result.value])
            if rows:
                context["row"] = rows[0]
        context["rows"] = rows
        context.update(self._shown(spec, rows, result))
        return self._rendered(self.get_context_data(**context), status=200)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Django's context - ``view``, and ``extra_context`` - with the ``spec`` served."""
        kwargs.setdefault("spec", self.served_spec())
        return super().get_context_data(**kwargs)

    def served_spec(self) -> SelectorSpec:
        """``spec``, compiled: ``as_view`` refused to build a view whose spec does not compile to a ``SelectorSpec``."""
        return cast("SelectorSpec", kernel_spec(cast("SelectorSpec | SpecSource", self.spec)))

    def _paging(self, sent: QueryDict) -> tuple[QueryDict, int | None, int | None]:
        """The query without its paging keys, and the page and the page size they asked for.

        Taken off a copy, so the request's own query is left as it came, for
        the links that keep it. Each value is the last sent under its key, as
        ``QueryDict`` reads a repeated one, and one that is not a whole
        number is no value: page 1, and ``paginate_by``
        (test_a_page_that_is_not_a_number_is_page_one).
        """
        query = sent.copy()
        page = _whole(query.pop(self.page_kwarg, []))
        limit = self.paginate_by
        if self.page_size_kwarg is not None:
            asked = _whole(query.pop(self.page_size_kwarg, []))
            if asked is not None:
                limit = asked
        return query, page, limit

    def _shown(self, spec: SelectorSpec, rows: list[Any], result: DispatchResult) -> dict[str, Any]:
        """``table`` and ``row_displays``, each where the view asks for it.

        The table is built with ``order_by=()``, which django-tables2 reads as
        "remove any ordering": with nothing orderable, its sort is by no key
        and leaves the spec's order, even where the table's ``Meta`` declares
        an ``order_by``: test_a_table_s_own_order_by_never_reorders_the_rows for
        a table with nothing orderable, and
        test_a_table_s_own_order_by_never_reorders_a_sorted_page for one whose
        headers sort, where the ``Meta.order_by`` would otherwise apply.
        No ``request`` is handed to it, since a table built with one runs
        django-tables2's ``RequestConfig``, which pages and orders the data
        from the query string.

        The displays are presented by the spec's presenter with the context
        dispatch built for the call, as ``present()`` would, and projected as
        the form page projects its target.
        """
        if self.table_class is not None:
            keywords: dict[str, Any] = {"order_by": ()}
            if self.table_order_by_field is None:
                keywords["orderable"] = False
            else:
                keywords["order_by_field"] = self.table_order_by_field
            return {"table": self.table_class(rows, **keywords)}
        if not self.display_rows:
            return {}
        presenter, projection = display_of(spec)
        presented = presenter.presented_rows(tuple(rows), result.context)
        return {
            "row_displays": [
                {"row": row, **displayed(each, projection)}
                for row, each in zip(rows, presented, strict=True)
            ]
        }


def _attributes(cls: type[SpecSelectorView]) -> dict[str, Any]:
    """The paging attributes as the class declares them, for ``initkwargs`` to override."""
    return {
        "page_kwarg": cls.page_kwarg,
        "page_size_kwarg": cls.page_size_kwarg,
        "paginate_by": cls.paginate_by,
        "max_page_size": cls.max_page_size,
    }


def _selector(label: str, spec: Any) -> SelectorSpec:
    """``spec`` compiled to the ``SelectorSpec`` it must be, or refused naming what it is."""
    try:
        compiled = kernel_spec(spec)
    except TypeError as error:
        raise ImproperlyConfigured(f"{label}.as_view() needs a SelectorSpec: {error}") from error
    if not isinstance(compiled, SelectorSpec):
        raise ImproperlyConfigured(
            f"{label}.as_view() needs a SelectorSpec, the read a page lists or shows; got "
            f"{type(compiled).__name__}. A write is served by SpecFormView or SpecConfirmView."
        )
    return compiled


def _check_paging(label: str, spec: SelectorSpec, configured: Mapping[str, Any]) -> None:
    """Refuse paging attributes that could not page, and a list spec that declares a paging key.

    Each key is a non-empty string, the two distinct; each size a positive
    ``int`` or ``None``; and a client's page size has a ceiling. Then, on a
    list, no parameter of the spec's may be named like a paging key: the key
    is taken off the query before the spec reads it, so the parameter could
    never be sent. A ``RETRIEVE`` is not paged, so its parameters are its own.
    """
    page_kwarg = configured["page_kwarg"]
    size_kwarg = configured["page_size_kwarg"]
    if not _is_key(page_kwarg):
        raise ImproperlyConfigured(
            f"{label}.page_kwarg must be the query key naming the page; got {page_kwarg!r}."
        )
    # One branch to coverage, so each condition is held by its own test:
    # every view with no size key holds the ``None`` test, and the cases of
    # test_a_page_size_kwarg_that_is_no_key_is_refused the rest: ``empty`` and
    # ``a-number`` the first, ``the-page-key`` the second.
    if size_kwarg is not None and (not _is_key(size_kwarg) or size_kwarg == page_kwarg):
        raise ImproperlyConfigured(
            f"{label}.page_size_kwarg must be None or a query key other than page_kwarg; "
            f"got {size_kwarg!r}."
        )
    for name in ("paginate_by", "max_page_size"):
        size = configured[name]
        # One branch to coverage, so each condition is held by its own test:
        # every view leaving a size unset holds the ``None`` test, and the
        # cases of test_a_size_that_is_no_positive_int_is_refused the rest:
        # ``a-string`` the first, ``true`` the second (a bool is an int),
        # ``zero`` the third.
        if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 1):
            raise ImproperlyConfigured(
                f"{label}.{name} must be None or a positive int; got {size!r}."
            )
    # One branch to coverage: every view with no size key holds the first
    # condition, and test_it_is_read_and_bounded the second.
    if size_kwarg is not None and configured["max_page_size"] is None:
        raise ImproperlyConfigured(
            f"{label}.page_size_kwarg lets a client choose the page size, and max_page_size "
            "sets no ceiling on it: set max_page_size."
        )
    if spec.kind is not SelectorKind.LIST:
        return
    # An unset size key is ``None``, which names no parameter.
    taken = sorted(name for name in (page_kwarg, size_kwarg) if name in spec.parameters().names())
    if taken:
        raise ImproperlyConfigured(
            f"{label} pages by {', '.join(taken)}, which the spec declares as a parameter: the "
            "page takes its paging keys off the query before the spec reads it, so the "
            "parameter could never be sent. Name another key with page_kwarg or "
            "page_size_kwarg."
        )


def _check_table(label: str, spec: SelectorSpec, table_class: Any, order_by_field: Any) -> None:
    """Refuse a ``table_order_by_field`` the spec does not declare, which every header link would send.

    The key a header link sends is the table's ``Meta.prefix`` followed by the
    field, as django-tables2 builds ``prefixed_order_by_field``, so the
    prefixed name is the one the spec must declare
    (test_a_prefixed_table_s_order_field_is_read_with_its_prefix). Read off
    the class's options, since the page builds the table with no prefix of
    its own.
    """
    if order_by_field is None:
        return
    key = f"{getattr(getattr(table_class, '_meta', None), 'prefix', '') or ''}{order_by_field}"
    if key in spec.parameters().names():
        return
    raise ImproperlyConfigured(
        f"{label}.table_order_by_field is {order_by_field!r}, so every header link sends "
        f"{key!r}, which the spec does not declare: dispatch would refuse it. Name the spec's "
        "own ordering parameter, such as its filter set's OrderingFilter, less any prefix "
        "the table's Meta sets."
    )


def _is_key(name: Any) -> bool:
    """Whether ``name`` can be a query key: a non-empty string."""
    return isinstance(name, str) and bool(name)


def _whole(sent: list[str]) -> int | None:
    """The last of ``sent`` as a whole number, or ``None`` where there is none or it is not one."""
    if not sent:
        return None
    try:
        return int(sent[-1])
    except ValueError:
        return None


def _reading(request: HttpRequest, query: QueryDict) -> HttpRequest:
    """``request`` as ``request_arguments`` reads it: its query string ``query``.

    A shallow copy, so the request's own query is left as it came, for the
    filter's binding and the links; the reader takes a request, and a query
    without the paging keys is what the spec is sent
    (test_the_page_key_never_reaches_the_spec).
    """
    # ``Any``, as the stubs declare ``GET`` an immutable ``QueryDict``, which a
    # copy taken to drop keys is not; the reader only reads it.
    reading: Any = copy.copy(request)
    reading.GET = query
    return reading


def _links(sent: QueryDict, page_kwarg: str, page: OutputPage) -> dict[str, str]:
    """``previous_page_query`` and ``next_page_query``, where those pages exist.

    The request's own query with the page key replaced and everything else
    kept: the filters, and a client's page size. Built off the page served,
    which is not always the page asked for, so a link from a clamped page
    leads where its label says.
    """
    links: dict[str, str] = {}
    if page.page > 1:
        links["previous_page_query"] = _with_page(sent, page_kwarg, page.page - 1)
    if page.has_next:
        links["next_page_query"] = _with_page(sent, page_kwarg, page.page + 1)
    return links


def _with_page(sent: QueryDict, page_kwarg: str, number: int) -> str:
    """``sent``, urlencoded, its page key set to ``number``."""
    query = sent.copy()
    query[page_kwarg] = str(number)
    return query.urlencode()
