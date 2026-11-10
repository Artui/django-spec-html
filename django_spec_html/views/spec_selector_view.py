from __future__ import annotations

import copy
import dataclasses
import inspect
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
    UnknownArguments,
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
    """A ``SelectorSpec`` served as a page: a list's rows a page at a time, or one row."""

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
    # Only here does an undeclared key a stranger wrote reach dispatch, unlike REJECT.
    unknown_arguments: UnknownArguments = UnknownArguments.IGNORE

    @classonlymethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., HttpResponseBase]:
        """Django's ``as_view``, then the spec, hooks, paging, table and templates checked, once."""
        view = super().as_view(**initkwargs)
        label = cls.__name__
        spec = _selector(label, initkwargs.get("spec", cls.spec))
        check_request_hooks(cls, initkwargs, spec)
        if inspect.iscoroutinefunction(initkwargs.get("get_query", cls.get_query)):
            raise ImproperlyConfigured(
                f"{label}.get_query is async, and the view calls it without awaiting it, so it "
                "would answer a coroutine: define it with def."
            )
        _check_paging(label, spec, {**_attributes(cls), **initkwargs})
        _check_table(
            label,
            spec,
            initkwargs.get("table_class", cls.table_class),
            initkwargs.get("table_order_by_field", cls.table_order_by_field),
        )
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
        principal, grant, seeds = admitted(spec, request, kwargs, served)
        listing = spec.kind is SelectorKind.LIST
        read = self._query(request)
        query, page, limit = self._paging(read) if listing else (read, None, None)
        context: dict[str, Any] = {"request": _reading(request, read)}
        form: Any = None
        if spec.filter_set is not None:
            context["filter"] = filters = spec.filter_set(data=query, request=request)
            form = filters.form
            without_errors(form)
        try:
            arguments = request_arguments(
                _reading(request, query),
                spec.parameters(call_context=served.call_context),
                url_kwargs=kwargs,
                route_kwargs=served.route_kwargs,
                server_arguments=served.server,
            )
            result = dispatch(
                spec,
                principal=principal,
                arguments=with_binding(spec, arguments, served),
                grant=grant,
                seeds=seeds,
                pool_seeds=served.seeds,
                unknown_arguments=self.unknown_arguments,
                call_context=served.call_context,
            )
        except (DispatchError, ServiceError) as refused:
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
            rows = answered_rows(spec, list(served_page.items))
            context["page"] = dataclasses.replace(served_page, items=rows)
            context.update(_links(read, self.page_kwarg, served_page))
        else:
            rows = [] if result.value is None else answered_rows(spec, [result.value])
            if rows:
                context["row"] = rows[0]
        context["rows"] = rows
        context.update(self._shown(spec, rows, result))
        return self._rendered(self.get_context_data(**context), status=200)

    def get_query(self, request: HttpRequest) -> QueryDict:
        """The query string the page reads: ``request.GET`` unless overridden."""
        return request.GET

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Django's context - ``view``, and ``extra_context`` - with the ``spec`` served."""
        kwargs.setdefault("spec", self.served_spec())
        return super().get_context_data(**kwargs)

    def served_spec(self) -> SelectorSpec:
        """``spec``, compiled: ``as_view`` refused a view whose spec is not a ``SelectorSpec``."""
        return cast("SelectorSpec", kernel_spec(cast("SelectorSpec | SpecSource", self.spec)))

    def _query(self, request: HttpRequest) -> QueryDict:
        """``get_query(request)``, refused unless a ``QueryDict``; the paging copies and pops it."""
        query = self.get_query(request)
        if not isinstance(query, QueryDict):
            raise TypeError(
                f"{type(self).__name__}.get_query() must answer a QueryDict, such as "
                f"request.GET or QueryDict(saved); got {type(query).__name__}."
            )
        return query

    def _paging(self, sent: QueryDict) -> tuple[QueryDict, int | None, int | None]:
        """The query without its paging keys, and the page and the page size they asked for."""
        query = sent.copy()
        page = _whole(query.pop(self.page_kwarg, []))
        limit = self.paginate_by
        if self.page_size_kwarg is not None:
            asked = _whole(query.pop(self.page_size_kwarg, []))
            if asked is not None:
                limit = asked
        return query, page, limit

    def _shown(self, spec: SelectorSpec, rows: list[Any], result: DispatchResult) -> dict[str, Any]:
        """``table`` and ``row_displays``, each where the view asks for it."""
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
    """Refuse paging attributes that could not page, and a list spec that declares a paging key."""
    page_kwarg = configured["page_kwarg"]
    size_kwarg = configured["page_size_kwarg"]
    if not _is_key(page_kwarg):
        raise ImproperlyConfigured(
            f"{label}.page_kwarg must be the query key naming the page; got {page_kwarg!r}."
        )
    if size_kwarg is not None and (not _is_key(size_kwarg) or size_kwarg == page_kwarg):
        raise ImproperlyConfigured(
            f"{label}.page_size_kwarg must be None or a query key other than page_kwarg; "
            f"got {size_kwarg!r}."
        )
    for name in ("paginate_by", "max_page_size"):
        size = configured[name]
        if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 1):
            raise ImproperlyConfigured(
                f"{label}.{name} must be None or a positive int; got {size!r}."
            )
    if size_kwarg is not None and configured["max_page_size"] is None:
        raise ImproperlyConfigured(
            f"{label}.page_size_kwarg lets a client choose the page size, and max_page_size "
            "sets no ceiling on it: set max_page_size."
        )
    if spec.kind is not SelectorKind.LIST:
        return
    taken = sorted(name for name in (page_kwarg, size_kwarg) if name in spec.parameters().names())
    if taken:
        raise ImproperlyConfigured(
            f"{label} pages by {', '.join(taken)}, which the spec declares as a parameter: the "
            "page takes its paging keys off the query before the spec reads it, so the "
            "parameter could never be sent. Name another key with page_kwarg or "
            "page_size_kwarg."
        )


def _check_table(label: str, spec: SelectorSpec, table_class: Any, order_by_field: Any) -> None:
    """Refuse a ``table_order_by_field`` the spec does not declare."""
    if order_by_field is None:
        return
    key = f"{getattr(getattr(table_class, '_meta', None), 'prefix', '') or ''}{order_by_field}"
    if key in spec.parameters().names():
        return
    raise ImproperlyConfigured(
        f"{label}.table_order_by_field is {order_by_field!r}, so every header link sends "
        f"{key!r}, which the spec does not declare: dispatch would ignore it, or refuse it "
        "under REJECT, and no header would sort the rows. Name the spec's own ordering "
        "parameter, such as its filter set's OrderingFilter, less any prefix "
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
    """``request`` with its query string ``query``, as the template reads it."""
    reading: Any = copy.copy(request)
    reading.GET = query
    return reading


def _links(sent: QueryDict, page_kwarg: str, page: OutputPage) -> dict[str, str]:
    """``previous_page_query`` and ``next_page_query``, where those pages exist."""
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
