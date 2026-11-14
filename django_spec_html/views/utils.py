from __future__ import annotations

import dataclasses
import datetime
import json
import math
from abc import ABCMeta, abstractmethod
from collections.abc import Callable, Mapping
from contextlib import suppress
from decimal import Decimal
from types import MappingProxyType
from typing import Any, ClassVar, Final, Literal, cast
from urllib.parse import unquote

from django import forms
from django.contrib import messages
from django.core.exceptions import (
    BadRequest,
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.forms.utils import ErrorDict
from django.forms.widgets import FILE_INPUT_CONTRADICTION
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBase,
    HttpResponseRedirect,
)
from django.http.response import HttpResponseRedirectBase
from django.shortcuts import resolve_url
from django.template import TemplateDoesNotExist
from django.template.loader import get_template, select_template
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.decorators import classonlymethod
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic.base import ContextMixin, TemplateResponseMixin
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    NON_FIELD_ERRORS,
    UNSET,
    ActionUnavailable,
    AdditionalInputRequired,
    Affordance,
    AudienceProjection,
    CallSeeds,
    DispatchError,
    DispatchResult,
    FieldAudience,
    Grant,
    InvalidArguments,
    NotPermitted,
    Parameters,
    PoolSeeds,
    PresentationContext,
    Presenter,
    PrincipalUnavailable,
    SelectorSpec,
    ServerArguments,
    ServiceError,
    ServiceNotFound,
    ServiceSpec,
    ServiceValidationError,
    UnknownArguments,
    apply_server_arguments,
    audience_projection_for_spec,
    authorize,
    authorize_scope,
    check_arguments,
    check_requested_input,
    coerce_flat,
    dispatch,
    enforce_affordances,
    project_payload,
    resolve_seeds,
    resolve_target,
    row_affordance_answers,
    seeded_pool,
    unmet_operation_affordance,
)

from django_spec_html.http.request_principal import request_principal
from django_spec_html.http.route_arguments import route_arguments
from django_spec_html.http.utils import (
    check_request_hooks,
    checked_binding,
    checked_seeds,
    field_map,
    html_call_context,
    refusal_outcome,
    refusal_status,
    refuse_server_owned,
    server_names,
)
from django_spec_html.types.outcome import Outcome

_MODE_HEADERS: Final = ("HX-Request", "HX-Boosted", "HX-History-Restore-Request")
"""The headers ``wants_fragment`` reads, and so the ones every answer varies on.

One URL answers two bodies, so a cache keyed on fewer would hand a fragment
to a page load, or to the history restore that needs the whole page back."""

_SPEC_FORM: Final = "spec-form"
"""The fragment a page's form is, addressed as ``<template_name>#spec-form`` by default."""

_SPEC_RESULT: Final = "spec-result"
"""The fragment a success is answered with in place, where the fragment template defines one."""

SPEC_RESULTS: Final = "spec-results"
"""The fragment a selector page's results are, at ``<template_name>#spec-results`` by default."""

_SHIPPED_FRAGMENTS: Final = MappingProxyType(
    {
        "django_spec_html/spec_form.html": "django_spec_html/spec_form_fragment.html",
        "django_spec_html/spec_form_set.html": "django_spec_html/spec_form_set_fragment.html",
        "django_spec_html/spec_confirm.html": "django_spec_html/spec_confirm_fragment.html",
        "django_spec_html/spec_selector.html": "django_spec_html/spec_selector_fragment.html",
    }
)
"""Each page this package ships, a view's ``template_name`` by default, and its fragment's file.

A file of its own rather than a ``spec-form`` ``{% partialdef %}`` inside
the page, because below Django 6.0 a page using that tag fails to parse
unless the project configures django-template-partials, and a default has to
render where nothing was configured for it. The page includes the
fragment, so the two never differ."""

_SUCCEEDED: Final = "spec-succeeded"
"""The event ``HX-Trigger`` names on a success answered in place, for the rest of the page."""

_TRIGGER_HEADERS: Final = MappingProxyType(
    {
        "receive": "HX-Trigger",
        "swap": "HX-Trigger-After-Swap",
        "settle": "HX-Trigger-After-Settle",
    }
)
"""The header each ``success_trigger_timing`` sends its trigger in."""

_TRIGGER_NAME: Final = "HX-Trigger-Name"
"""The ``name`` of the element an htmx 2 request was issued from: a form, or a control of its own.

A header htmx 2 sends, and not every client that posts the form does, which
is why ``commits`` reads it only to stop a commit the post's data would
otherwise make, and a refresh ``SpecFormView`` validates only to narrow the
errors it shows to one field's."""


_TYPED: Final = (forms.IntegerField, forms.DateField, forms.DateTimeField)
"""The fields whose input formats are wider than the wire form the shape check reads.

``DecimalField`` and ``FloatField`` subclass ``IntegerField``, so the first
row covers all three numbers. Each row is held by a test:
test_localized_numbers_are_read_as_the_form_reads_them (the numbers) and
test_dates_in_the_forms_input_formats_are_read_as_the_form_reads_them (both
date rows)."""


class SpecRequestBase(TemplateResponseMixin, ContextMixin, View):
    """What every page reads off a request: the hooks, the call context and the wrapper."""

    fragment_template_name: str | None = None
    fragment_name: ClassVar[str] = _SPEC_FORM
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None = None
    route_refusal_404: bool = True
    server_arguments: ServerArguments | None = None

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """Django's ``dispatch``, every answer varying on the headers the wrapper is chosen by."""
        response = super().dispatch(request, *args, **kwargs)
        patch_vary_headers(response, _MODE_HEADERS)
        return response

    def get_pool_seeds(self, request: HttpRequest) -> PoolSeeds:
        """The seeds this request's call is resolved with: ``pool_seeds`` unless overridden."""
        return self.pool_seeds

    def get_server_arguments(self, request: HttpRequest) -> ServerArguments | None:
        """What the server answers for on this call: ``server_arguments``, unless overridden."""
        return self.server_arguments

    def wants_fragment(self, request: HttpRequest) -> bool:
        """Whether ``request`` is answered with a fragment rather than the page."""
        details = getattr(request, "htmx", None)
        if details is not None:
            return bool(details) and not details.boosted and not details.history_restore_request
        headers = request.headers
        return (
            headers.get("HX-Request") == "true"
            and headers.get("HX-Boosted") != "true"
            and headers.get("HX-History-Restore-Request") != "true"
        )

    def get_fragment_template_names(self) -> list[str]:
        """The templates a fragment renders: ``fragment_template_name``, or ``#<fragment_name>``."""
        if self.fragment_template_name is not None:
            return [self.fragment_template_name]
        return [
            _SHIPPED_FRAGMENTS.get(name) or _address(name, self.fragment_name)
            for name in self.get_template_names()
        ]

    def _served(self, request: HttpRequest) -> Served:
        """What both hooks answer for ``request``, checked as ``SpecView`` checks them, once."""
        label = type(self).__name__
        server = checked_binding(label, self.get_server_arguments(request), self.server_arguments)
        seeds = checked_seeds(label, self.get_pool_seeds(request), self.pool_seeds, server)
        return Served(
            route_kwargs=self.route_kwargs,
            server=server,
            seeds=seeds,
            call_context=html_call_context(request, self),
        )

    def _rendered(self, context: dict[str, Any], *, status: int) -> HttpResponse:
        """``context`` as the page at ``status``, or as the form's fragment at 200."""
        if self.wants_fragment(self.request):
            return self.response_class(
                request=self.request,
                template=self.get_fragment_template_names(),
                context=context,
                using=self.template_engine,
                content_type=self.content_type,
            )
        return self.render_to_response(context, status=status)


class SpecPageBase(SpecRequestBase, metaclass=ABCMeta):
    """Everything a spec served as a page shares, whatever the page renders for its post."""

    spec: ServiceSpec | None = None
    actions: Mapping[str, ServiceSpec] | None = None
    action_name: str | None = None
    success_url: Any = None
    display_target: bool = False
    target_display_limit: int = 10
    commit_button: str | None = None
    success_message: str = ""
    redirect_field_name: str | None = None
    success_trigger: str | Mapping[str, Any] | None = _SUCCEEDED
    success_trigger_timing: Literal["receive", "swap", "settle"] = "receive"
    success_refresh: bool = False
    _takes_actions: ClassVar[bool] = False

    @classonlymethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., HttpResponseBase]:
        """Django's ``as_view``, then the spec, redirect and hooks checked, once per page."""
        view = super().as_view(**initkwargs)
        if cls.__abstractmethods__:
            missing = ", ".join(sorted(cls.__abstractmethods__))
            raise ImproperlyConfigured(
                f"{cls.__qualname__} does not supply the page's hooks: {missing}."
            )
        specs = _page_specs(cls, initkwargs)
        _check_configuration(cls, initkwargs, specs)
        for label, spec in specs:
            check_request_hooks(cls, initkwargs, spec)
            cls._check_page(label, spec, initkwargs)
        actions = initkwargs.get("actions", cls.actions)
        if actions is not None:
            cls._check_actions(cls.__name__, actions)
        check_shipped_templates(cls, initkwargs)
        return view

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """The page, with an unbound form showing the target row, for a principal admitted to it."""
        return self._page(request, kwargs)

    def _page(
        self, request: HttpRequest, url_kwargs: Mapping[str, Any], **bound: Any
    ) -> HttpResponse:
        """The page GET renders, its form bound by ``bound`` for a post that does not commit."""
        spec = self.served_spec()
        served = self._served(request)
        principal, grant, seeds = admitted(spec, request, url_kwargs, served)
        try:
            target, _ = _found(
                spec,
                principal,
                grant,
                seeds,
                url_kwargs,
                served,
                not_found=self.route_refusal_404,
                ask_scope=True,
            )
        except InvalidArguments as refused:
            raise BadRequest(str(refused.detail)) from refused
        pool = call_pool(principal, served)
        unavailable = _unavailable(spec, target, served, pool)
        context: dict[str, Any] = {} if unavailable is None else {"unavailable": unavailable}
        form = self._form(spec, principal, target, served, **bound)
        if bound:
            context["refreshed"] = True
            self._refreshed(request, form, spec, principal, target, served, url_kwargs)
        context.update(self._target_context(spec, principal, target, served, pool))
        return self._render_page(form, bool(bound), **context)

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Dispatch the posted form: a redirect on success, the form re-rendered on a refusal."""
        if not self.commits(request):
            return self._page(request, kwargs, data=request.POST, files=request.FILES)
        if self.actions is not None:
            self.action_name = _chosen(self.actions, request)
        spec = self.served_spec()
        served = self._served(request)
        principal, grant, seeds = admitted(spec, request, kwargs, served)
        target: Any = UNSET
        with suppress(InvalidArguments):
            target, grant = _found(
                spec, principal, grant, seeds, kwargs, served, not_found=self.route_refusal_404
            )
        form = self._form(spec, principal, target, served, data=request.POST, files=request.FILES)
        try:
            read = self._arguments(form, spec, kwargs, served)
            result = dispatch(
                spec,
                principal=principal,
                arguments=with_binding(spec, read, served),
                grant=grant,
                seeds=seeds,
                pool_seeds=served.seeds,
                unknown_arguments=self.unknown_arguments,
                target=target,
                call_context=served.call_context,
            )
        except (DispatchError, ServiceError) as refused:
            if spec.many and isinstance(refused, InvalidArguments):
                with suppress(InvalidArguments):
                    _found(
                        spec,
                        principal,
                        grant,
                        seeds,
                        kwargs,
                        served,
                        not_found=False,
                        ask_scope=True,
                    )
            outcome = refusal_outcome(refused)
            if outcome is Outcome.DENIED:
                raise denial(refused) from refused
            requested: AdditionalInputRequired | None = None
            if outcome is Outcome.NEEDS_INPUT:
                requested = cast("AdditionalInputRequired", refused)
                check_requested_input(
                    spec,
                    requested.schema,
                    pool_seeds=served.seeds,
                    call_context=served.call_context,
                )
            elsewhere = self._refused_elsewhere(refused)
            if elsewhere is not None:
                return elsewhere
            context = {
                **refusal_code(refused),
                **self._target_context(spec, principal, target, served, None),
            }
            if requested is not None:
                context["requested_input"] = {
                    "message": requested.message,
                    "schema": requested.schema,
                }
            return self._render_refused(
                form,
                refused,
                target,
                status=refusal_status(refused),
                requested=requested,
                **context,
            )
        messages.success(request, self.get_success_message(result), fail_silently=True)
        if self.wants_fragment(request):
            return self.fragment_success(result)
        return HttpResponseRedirect(self.get_redirect_url() or self.get_success_url(result))

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Django's context, with the ``spec`` served and the way back."""
        kwargs.setdefault("spec", self.served_spec())
        way_back = self.get_redirect_url()
        if way_back:
            kwargs.setdefault("redirect_field_name", self.redirect_field_name)
            kwargs.setdefault("next", way_back)
        return super().get_context_data(**kwargs)

    def get_redirect_url(self) -> str:
        """The safe URL the request asks to return to, else ``""``."""
        name = self.redirect_field_name
        if name is None:
            return ""
        request = self.request
        url = request.POST.get(name, request.GET.get(name))
        safe = url_has_allowed_host_and_scheme(
            url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        )
        return cast("str", url).strip() if safe else ""

    def get_success_url(self, result: DispatchResult) -> str:
        """Where a successful post redirects: ``success_url``, as ``redirect()`` reads it."""
        return resolve_url(self.success_url)

    def get_success_message(self, result: DispatchResult) -> str:
        """The message a successful post sends: ``success_message`` with the cleaned data."""
        return self.success_message % result.data

    def commits(self, request: HttpRequest) -> bool:
        """Whether a post runs the operation, or re-renders its form with the post's values."""
        button = self.commit_button
        if button is None:
            return True
        trigger = trigger_name(request)
        another = trigger != button and trigger in request.POST
        return button in request.POST and not another

    def get_success_trigger(self, result: DispatchResult) -> str | Mapping[str, Any] | None:
        """The event a success answered in place triggers: ``success_trigger`` by default."""
        return self.success_trigger

    def fragment_success(self, result: DispatchResult) -> HttpResponse:
        """A success, answered to the htmx request that posted it."""
        if self.success_refresh:
            response = HttpResponse()
            response["HX-Refresh"] = "true"
            return self._triggered(response, result)
        try:
            template = select_template(
                [_address(name, _SPEC_RESULT) for name in self.get_fragment_template_names()],
                using=self.template_engine,
            )
        except TemplateDoesNotExist:
            template = None
        if template is None:
            return ClientRedirect(self.get_redirect_url() or self.get_success_url(result))
        response = self.response_class(
            request=self.request,
            template=template,
            context=self.get_context_data(result=result),
            using=self.template_engine,
            content_type=self.content_type,
        )
        return self._triggered(response, result)

    def _triggered(self, response: HttpResponse, result: DispatchResult) -> HttpResponse:
        """``response`` carrying the success's trigger, in the header its timing names."""
        trigger = self.get_success_trigger(result)
        if trigger is None:
            return response
        if not isinstance(trigger, str):
            try:
                trigger = json.dumps(trigger, cls=DjangoJSONEncoder)
            except TypeError as unsent:
                raise ImproperlyConfigured(
                    f"{type(self).__name__}'s success trigger cannot be sent as JSON: {unsent}"
                ) from unsent
        response[_TRIGGER_HEADERS[self.success_trigger_timing]] = trigger
        return response

    def _target_context(
        self,
        spec: ServiceSpec,
        principal: Any,
        target: Any,
        served: Served,
        pool: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """What the page shows of the row or set the operation acts on."""
        if target is UNSET or target is None:
            return {}
        shown = display_of(spec) if self.display_target else None
        presented: PresentationContext | None = None
        if shown is not None:
            ambient = call_pool(principal, served) if pool is None else pool
            presented = PresentationContext(
                principal,
                target,
                MappingProxyType(ambient),
                call_context=served.call_context,
            )
        if spec.collection_selector_spec is None:
            context: dict[str, Any] = {"target": target}
            if shown is not None:
                presenter, projection = shown
                context["target_display"] = displayed(
                    presenter.presented(target, presented), projection
                )
            return context
        limit = self.target_display_limit
        if isinstance(target, models.QuerySet):
            count = target.all().count()
            rows: list[Any] = list(target.all()[:limit]) if shown is not None else []
        else:
            every = list(target)
            count = len(every)
            rows = every[:limit]
        context = {
            "target": target,
            "target_count": count,
            "target_counted_at": timezone.now(),
        }
        if shown is not None:
            presenter, projection = shown
            context["target_display"] = [
                displayed(each, projection)
                for each in presenter.presented_rows(tuple(rows), presented)
            ]
        return context

    def _refused_elsewhere(self, refused: DispatchError | ServiceError) -> HttpResponse | None:
        """An answer to a refused post other than the page again, or ``None`` for the page."""
        return None

    def _refreshed(
        self,
        request: HttpRequest,
        form: Any,
        spec: ServiceSpec,
        principal: Any,
        target: Any,
        served: Served,
        url_kwargs: Mapping[str, Any],
        /,
    ) -> None:
        """Set the error state of the form a post that does not commit renders."""

    def served_spec(self) -> ServiceSpec:
        """``spec``, or the action ``action_name`` names, the first before a post chose one."""
        actions = self.actions
        if actions is None:
            return cast("ServiceSpec", self.spec)
        return actions[self.action_name or next(iter(actions))]

    @classmethod
    def _check_actions(cls, label: str, actions: Mapping[str, ServiceSpec]) -> None:
        """Refuse, at ``as_view()``, actions this page cannot serve together; none by default."""

    # The hooks: each a step that differs with what the page renders, so a
    # subclass supplies every one.

    @classmethod
    @abstractmethod
    def _check_spec(cls, label: str, spec: ServiceSpec) -> None:
        """Refuse, at ``as_view()``, a spec this page cannot serve: its Validator, or its shape."""

    @classmethod
    @abstractmethod
    def _check_page(cls, label: str, spec: ServiceSpec, initkwargs: Mapping[str, Any]) -> None:
        """Refuse, at ``as_view()``, the subclass's own attributes read against what it renders."""

    @abstractmethod
    def _form(
        self, spec: ServiceSpec, principal: Any, target: Any, served: Served, **bound: Any
    ) -> Any:
        """What the page renders to post: unbound on GET, bound by ``bound`` otherwise."""

    @abstractmethod
    def _arguments(
        self, form: Any, spec: ServiceSpec, url_kwargs: Mapping[str, Any], served: Served
    ) -> Mapping[str, Any]:
        """The arguments a committing post carries, read off ``form``."""

    @abstractmethod
    def _render_page(self, form: Any, refreshed: bool, /, **context: Any) -> HttpResponse:
        """The page GET renders at 200, ``form`` in it beside ``context``."""

    @abstractmethod
    def _render_refused(
        self,
        form: Any,
        refused: DispatchError | ServiceError,
        target: Any,
        /,
        *,
        status: int,
        requested: AdditionalInputRequired | None,
        **context: Any,
    ) -> HttpResponse:
        """The page again for a refused post, at ``status``, ``form`` carrying ``refused``."""


@dataclasses.dataclass(frozen=True)
class Served:
    """What a request's hooks answered, read once, handed to each step using the route or a seed."""

    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None
    server: ServerArguments | None
    seeds: PoolSeeds
    call_context: dict[str, Any]
    """The request's one call context, handed as this very dict to every kernel call the page makes.

    It starts with ``request`` and ``view``, this request and this page, which
    the kernel hands to every stage naming either
    (test_every_stage_is_handed_the_request_and_the_page, in each page's
    tests). The class-level check, the target's lookup and its checks, the affordances,
    the form's keywords and dispatch, so a stage reads what an earlier one put
    there (test_the_checks_the_affordances_the_form_and_the_service_share_one_dict).
    """


def _page_specs(cls: type[SpecPageBase], initkwargs: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """Each spec the page serves, as the label its refusals name: ``spec``, or every action."""
    label = cls.__name__
    spec = initkwargs.get("spec", cls.spec)
    actions = initkwargs.get("actions", cls.actions)
    if actions is None:
        return [(label, spec)]
    if not cls._takes_actions:
        raise ImproperlyConfigured(
            f"{label} takes no actions: only SpecFormView and SpecConfirmView dispatch one "
            "spec per submit button."
        )
    if spec is not None:
        raise ImproperlyConfigured(
            f"{label} sets both spec and actions: a page serves one spec, or one per action, "
            "never both."
        )
    if not isinstance(actions, Mapping) or not actions:
        raise ImproperlyConfigured(
            f"{label}.actions must be a non-empty mapping of submit button names to the "
            f"ServiceSpec each runs; got {actions!r}."
        )
    for key in actions:
        if not isinstance(key, str) or not key:
            raise ImproperlyConfigured(
                f"{label}.actions keys are the names of the submit buttons that run them, "
                f"non-empty strings; got {key!r}."
            )
    return [(f"{label}.actions[{key!r}]", each) for key, each in actions.items()]


def _chosen(actions: Mapping[str, ServiceSpec], request: HttpRequest) -> str:
    """The one action whose button ``request`` posted, else Django's ``BadRequest``."""
    named = [key for key in actions if key in request.POST]
    if not named:
        raise BadRequest(f"The post names none of the page's actions: {', '.join(actions)}.")
    if len(named) > 1:
        raise BadRequest(f"The post names more than one action: {', '.join(named)}.")
    return named[0]


def _check_configuration(
    cls: type[SpecPageBase], initkwargs: Mapping[str, Any], specs: list[tuple[str, Any]]
) -> None:
    """Refuse a view that could not serve its first request, naming what is missing."""
    label = cls.__name__
    shown = initkwargs.get("display_target", cls.display_target)
    for named, spec in specs:
        if not isinstance(spec, ServiceSpec):
            raise ImproperlyConfigured(
                f"{named}.as_view() needs a ServiceSpec, the write a form posts; "
                f"got {type(spec).__name__}."
            )
        cls._check_spec(named, spec)
        if shown:
            check_display(named, spec)
    redirects = initkwargs.get("get_success_url", cls.get_success_url)
    if (
        initkwargs.get("success_url", cls.success_url) is None
        and redirects is SpecPageBase.get_success_url
    ):
        raise ImproperlyConfigured(
            f"{label}.as_view() needs a success_url, where a successful post redirects, "
            "or a get_success_url that returns one."
        )
    limit = initkwargs.get("target_display_limit", cls.target_display_limit)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ImproperlyConfigured(
            f"{label}.target_display_limit must be a positive int; got {limit!r}."
        )
    button = initkwargs.get("commit_button", cls.commit_button)
    _check_commit_button(label, button)
    redirect = initkwargs.get("redirect_field_name", cls.redirect_field_name)
    _check_redirect_field_name(label, redirect, button)
    actions = initkwargs.get("actions", cls.actions) or {}
    if button in actions:
        raise ImproperlyConfigured(
            f"{label}.commit_button names {button!r}, one of its actions: the button that "
            "commits is one press, and an action's is another."
        )
    if redirect in actions:
        raise ImproperlyConfigured(
            f"{label}.redirect_field_name names {redirect!r}, one of its actions: the URL to "
            "return to is posted by every submit, so every post would name that action."
        )
    _check_success_trigger(label, cls, initkwargs)


def _check_success_trigger(
    label: str, cls: type[SpecPageBase], initkwargs: Mapping[str, Any]
) -> None:
    """Refuse a success trigger, timing or refresh the page could not send."""
    trigger = initkwargs.get("success_trigger", cls.success_trigger)
    if not _sendable(trigger):
        raise ImproperlyConfigured(
            f"{label}.success_trigger must be None, an event's name, or a mapping of event "
            f"names to their JSON payloads; got {trigger!r}."
        )
    timing = initkwargs.get("success_trigger_timing", cls.success_trigger_timing)
    if timing not in _TRIGGER_HEADERS:
        raise ImproperlyConfigured(
            f"{label}.success_trigger_timing must be one of "
            f"{', '.join(map(repr, _TRIGGER_HEADERS))}; got {timing!r}."
        )
    refresh = initkwargs.get("success_refresh", cls.success_refresh)
    if not isinstance(refresh, bool):
        raise ImproperlyConfigured(f"{label}.success_refresh must be a bool; got {refresh!r}.")
    if refresh and trigger is not None and timing != "receive":
        raise ImproperlyConfigured(
            f"{label} refreshes on success, so a trigger timed to {timing!r} never fires: "
            "htmx reloads the page before any swap. Time it to 'receive'."
        )


def _sendable(trigger: Any) -> bool:
    """Whether ``trigger`` is None, a name, or a mapping keyed by names."""
    if trigger is None:
        return True
    if isinstance(trigger, str):
        return bool(trigger)
    if not isinstance(trigger, Mapping) or not trigger:
        return False
    return all(isinstance(name, str) and name for name in trigger)


def check_shipped_templates(cls: type[SpecRequestBase], initkwargs: Mapping[str, Any]) -> None:
    """Refuse a page rendering a shipped template the view's engine does not find."""
    page = initkwargs.get("template_name", cls.template_name)
    names = initkwargs.get("get_template_names", cls.get_template_names)
    if page not in _SHIPPED_FRAGMENTS or names is not TemplateResponseMixin.get_template_names:
        return
    engine = initkwargs.get("template_engine", cls.template_engine)
    fragment = initkwargs.get("fragment_template_name", cls.fragment_template_name)
    fragments = initkwargs.get("get_fragment_template_names", cls.get_fragment_template_names)
    own = fragment is not None or fragments is not SpecRequestBase.get_fragment_template_names
    finder = (
        "no template engine finds it: add 'django_spec_html' to INSTALLED_APPS with APP_DIRS on"
        if engine is None
        else f"the {engine!r} engine does not find it: turn APP_DIRS on in that engine, with "
        "'django_spec_html' in INSTALLED_APPS"
    )
    for name in (page,) if own else (page, _SHIPPED_FRAGMENTS[page]):
        try:
            get_template(name, using=engine)
        except TemplateDoesNotExist as missing:
            raise ImproperlyConfigured(
                f"{cls.__name__} renders {name}, a template django-spec-html ships, and "
                f"{finder}, or set template_name to a template of the project's own."
            ) from missing


def _check_commit_button(label: str, button: Any) -> None:
    """Refuse a ``commit_button`` that is not a button's name."""
    if button is None:
        return
    if not isinstance(button, str) or not button:
        raise ImproperlyConfigured(
            f"{label}.commit_button must be None or the name of the submit button whose post "
            f"commits; got {button!r}."
        )


def _check_redirect_field_name(label: str, name: Any, button: str | None) -> None:
    """Refuse a ``redirect_field_name`` that is not a key's name, or the ``commit_button``."""
    if name is None:
        return
    if not isinstance(name, str) or not name:
        raise ImproperlyConfigured(
            f"{label}.redirect_field_name must be None or the name of the key a post or its "
            f"query carries the URL to return to in; got {name!r}."
        )
    if name == button:
        raise ImproperlyConfigured(
            f"{label}.redirect_field_name and commit_button both name {name!r}: the button's "
            "value would be read as the URL to return to."
        )


class ClientRedirect(HttpResponseRedirectBase):
    """A redirect htmx follows as a navigation: ``HX-Redirect`` at 200, not 302."""

    status_code = 200

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self["HX-Redirect"] = self["Location"]
        del self["Location"]

    @property
    def url(self) -> str:
        return self["HX-Redirect"]


def trigger_name(request: HttpRequest) -> str | None:
    """``HX-Trigger-Name``, decoded where htmx encoded it, or ``None`` where it was not sent."""
    name = request.headers.get(_TRIGGER_NAME)
    if name is None or request.headers.get(f"{_TRIGGER_NAME}-URI-AutoEncoded") != "true":
        return name
    return unquote(name)


def _address(template_name: str, fragment: str) -> str:
    """``fragment`` inside the file ``template_name`` names, whatever fragment it names already."""
    return f"{template_name.partition('#')[0]}#{fragment}"


def admitted(
    spec: ServiceSpec | SelectorSpec,
    request: HttpRequest,
    url_kwargs: Mapping[str, Any],
    served: Served,
) -> tuple[Any, Grant, CallSeeds]:
    """The principal, its grant and the request's seeds, once the checks pass."""
    route_arguments(
        spec.parameters(call_context=served.call_context),
        url_kwargs,
        route_kwargs=served.route_kwargs,
    )
    refuse_server_owned(url_kwargs, served.route_kwargs, server_names(served.server))
    try:
        principal = request_principal(request)
        seeds = resolve_seeds(
            spec, principal=principal, pool_seeds=served.seeds, call_context=served.call_context
        )
        grant = authorize(
            spec,
            principal,
            seeds=seeds,
            pool_seeds=served.seeds,
            call_context=served.call_context,
        )
    except (NotPermitted, PrincipalUnavailable) as refused:
        raise denial(refused) from refused
    return principal, grant, seeds


def _found(
    spec: ServiceSpec,
    principal: Any,
    grant: Grant,
    seeds: CallSeeds,
    url_kwargs: Mapping[str, Any],
    served: Served,
    *,
    not_found: bool,
    ask_scope: bool = False,
) -> tuple[Any, Grant]:
    """The row, set or ``None`` the post acts on, and the grant vouching for it."""
    parameters = spec.parameters(call_context=served.call_context)
    route = coerced_route(parameters, url_kwargs, served, not_found=not_found)
    arguments = with_binding(spec, route, served)
    try:
        if spec.many:
            if ask_scope:
                authorize_scope(
                    spec,
                    principal,
                    None,
                    grant=grant,
                    arguments=arguments,
                    seeds=seeds,
                    pool_seeds=served.seeds,
                    call_context=served.call_context,
                )
            return UNSET, grant
        resolved = resolve_target(
            spec,
            principal=principal,
            arguments=arguments,
            grant=grant,
            seeds=seeds,
            pool_seeds=served.seeds,
            call_context=served.call_context,
        )
    except (NotPermitted, PrincipalUnavailable, ServiceNotFound) as refused:
        raise denial(refused) from refused
    if isinstance(resolved, DispatchResult):
        raise Http404
    target, found = resolved
    return target, found


def coerced_route(
    parameters: Parameters, url_kwargs: Mapping[str, Any], served: Served, *, not_found: bool
) -> dict[str, Any]:
    """The route's captures alone, coerced; checked too where ``not_found``, a refusal a 404."""
    route = route_arguments(parameters, url_kwargs, route_kwargs=served.route_kwargs)
    try:
        coerced = coerce_flat(parameters, route)
        if not_found:
            check_arguments(parameters, coerced, partial=True)
    except InvalidArguments as refused:
        if not_found:
            raise Http404 from refused
        raise
    return coerced


def with_binding(
    spec: ServiceSpec | SelectorSpec, arguments: Mapping[str, Any], served: Served
) -> Mapping[str, Any]:
    """``arguments`` under the request's binding, by ``apply_server_arguments``."""
    if served.server is None:
        return arguments
    return apply_server_arguments(spec, arguments, served.server, call_context=served.call_context)


def call_pool(principal: Any, served: Served) -> dict[str, Any]:
    """The pool the page's affordances and its display read, built from the call's own seeds."""
    return seeded_pool(user=principal, seeds=served.seeds, call_context=served.call_context)


def _unavailable(
    spec: ServiceSpec, target: Any, served: Served, pool: dict[str, Any]
) -> Affordance | None:
    """The first of the spec's affordances not met for ``target`` now, or ``None``."""
    seeds = served.seeds
    unmet = unmet_operation_affordance(
        spec, pool, reserved=seeds.reserved, call_context=served.call_context
    )
    if unmet is not None or not isinstance(target, models.Model):
        return unmet
    try:
        enforce_affordances(
            spec,
            pool,
            instance=target,
            reserved=seeds.reserved,
            call_context=served.call_context,
        )
    except ActionUnavailable as refused:
        declared = next(each for each in spec.affordances or () if each.code == refused.code)
        if isinstance(declared.reason, str):
            return declared
        return dataclasses.replace(declared, reason=str(declared.reason))
    except ServiceNotFound as refused:
        raise denial(refused) from refused
    return None


def display_of(spec: ServiceSpec | SelectorSpec) -> tuple[Presenter, AudienceProjection]:
    """The presenter and the markings a person's view of a row is read through."""
    presenter = spec.presenter if isinstance(spec, SelectorSpec) else spec.presenter_for_output()
    return cast("Presenter", presenter), audience_projection_for_spec(spec)


def check_display(
    label: str,
    spec: ServiceSpec | SelectorSpec,
    *,
    shown: str = "its target",
    remedy: str = "leave display_target off",
) -> None:
    """Refuse a page displaying ``shown`` whose spec's Output cannot display it, naming both."""
    if isinstance(spec, SelectorSpec):
        called = spec.selector
        named = f"the selector spec of {getattr(called, '__qualname__', type(called).__qualname__)}"
        source = "a presenter"
    else:
        called = spec.service
        named = f"the spec of {getattr(called, '__qualname__', type(called).__qualname__)}"
        source = "a presenter or an output_selector_spec"
    if spec.output() is None:
        raise ImproperlyConfigured(
            f"{label} displays {shown} through its spec's Output, and {named} declares "
            f"none: give it {source}, or {remedy}."
        )
    try:
        projection = audience_projection_for_spec(spec)
    except ImproperlyConfigured as refused:
        raise ImproperlyConfigured(
            f"{label} cannot display {shown.replace('its ', 'the ', 1)} of {named}: {refused}"
        ) from refused
    if not projection.fields:
        raise ImproperlyConfigured(
            f"{label} displays {shown} by its Output's markings, and {named}'s Output "
            "marks none of its own fields with a FieldMarking: mark the field naming the "
            f"row FieldMarking.label(), or {remedy}."
        )


def displayed(presented: Any, projection: AudienceProjection) -> dict[str, Any]:
    """One presented row as a person reads it: its ``fields``, ``pairs`` and ``label``."""
    fields = _without_handles(project_payload(presented, projection), projection)
    shown: dict[str, Any] = {"fields": fields, "pairs": tuple(fields.items())}
    if projection.label in fields:
        return {"label": fields[projection.label], **shown}
    return shown


def _without_handles(payload: Any, projection: AudienceProjection) -> Any:
    """``payload`` with every field marked ``HANDLE`` dropped, into each nested object and array."""
    if isinstance(payload, list):
        return [_without_handles(each, projection) for each in payload]
    if not isinstance(payload, Mapping):
        return payload
    return {
        key: _without_handles(value, projection.nested[key]) if key in projection.nested else value
        for key, value in payload.items()
        if projection.audience(key) is not FieldAudience.HANDLE
    }


def refusal_code(refused: DispatchError | ServiceError) -> dict[str, str]:
    """The refusal's ``code`` under that name, as ``error_response`` puts it in the body."""
    if isinstance(refused, ActionUnavailable):
        return {"code": refused.code}
    return {}


def denial(refused: DispatchError | ServiceError) -> PermissionDenied | Http404:
    """A refusal answered 403 or 404 as Django's own exception, for the host's 403 and 404 pages."""
    if refusal_status(refused) == 403:
        return PermissionDenied(refused.message)
    return Http404(refused.message)


def without_errors(form: forms.BaseForm) -> None:
    """Set a bound form's error state to none, so rendering it validates nothing."""
    bound: Any = form
    bound._errors = ErrorDict(renderer=form.renderer)
    bound.cleaned_data = {}


def form_values(form: forms.BaseForm) -> dict[str, Any]:
    """The values a bound form's post carries, typed for the shape check."""
    read: dict[str, Any] = {}
    for name, field in form.fields.items():
        if field.disabled:
            continue
        value = form[name].data
        widget = field.widget
        if isinstance(widget, forms.ClearableFileInput) and (
            value is False or value is FILE_INPUT_CONTRADICTION
        ):
            read[widget.clear_checkbox_name(name)] = True
            value = form.files.get(form[name].html_name)
        if value is None or value == "":
            continue
        read[name] = _wire(field, value)
    return read


def _wire(field: forms.Field, value: Any) -> Any:
    """``value`` as the field reads it, in the wire form the shape check reads."""
    if not isinstance(field, _TYPED):
        return value
    try:
        typed: Any = field.to_python(value)
    except ValidationError:
        return value
    if isinstance(typed, Decimal):
        return str(typed)
    if isinstance(typed, datetime.date):
        return typed.isoformat()
    if isinstance(typed, float) and not math.isfinite(typed):
        return value
    return typed


_ANSWERS: Final = "_django_spec_html_answers"
"""The attribute a served row carries its affordance answers under, for the ``available`` filter.

Underscored, so a template cannot read it past the filter, and named for this
package, so it takes no name a model or an annotation could hold."""


class _AnsweredRow(dict):
    """A mapping row's copy, which can carry the answers a ``dict`` cannot hold as an attribute."""


def answered_rows(spec: SelectorSpec, rows: list[Any]) -> list[Any]:
    """``rows``, each carrying its affordance answers for the ``available`` filter."""
    answered: list[Any] = []
    for row in rows:
        answers = row_affordance_answers(spec, row)
        carrier = _AnsweredRow(row) if isinstance(row, Mapping) else row
        with suppress(AttributeError):
            setattr(carrier, _ANSWERS, answers)
        answered.append(carrier)
    return answered


def row_answers(row: Any) -> dict[str, dict[str, Any]] | None:
    """The answers ``answered_rows`` put on ``row``, or ``None`` for a row it never served."""
    return getattr(row, _ANSWERS, None)


def refusal_tree(refused: DispatchError | ServiceError) -> Mapping[Any, Any]:
    """The refusal a re-rendered form carries: an argument refusal in place, others as messages."""
    if isinstance(refused, InvalidArguments):
        return refused.detail
    if isinstance(refused, ServiceValidationError):
        return field_map(refused.detail)
    return {NON_FIELD_ERRORS: [refused.message]}
