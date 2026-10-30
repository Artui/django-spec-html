"""What the page views share: ``SpecPageBase``, and the readers its steps call.

A page admits its principal, finds its target, asks the affordances, posts to
dispatch and answers the outcome as a page or a fragment, and none of that
depends on what the page renders for a person to fill in. So it is written
once here, and a page view supplies only what does: the spec it serves, the
form it builds and how a post's arguments are read off it, and how the page
and a refused post render it. The readers - the admission, the target, the
display, the refusal's mapping - are functions here rather than methods,
since each takes what it reads as arguments and keeps nothing on the view.
"""

from __future__ import annotations

import dataclasses
import datetime
import math
from abc import ABCMeta, abstractmethod
from collections.abc import Callable, Mapping
from contextlib import suppress
from decimal import Decimal
from types import MappingProxyType
from typing import Any, ClassVar, Final, cast
from urllib.parse import unquote

from django import forms
from django.contrib import messages
from django.core.exceptions import (
    BadRequest,
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
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
    DispatchError,
    DispatchResult,
    FieldAudience,
    Grant,
    InvalidArguments,
    NotPermitted,
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
    base_pool,
    check_requested_input,
    coerce_flat,
    dispatch,
    enforce_affordances,
    project_payload,
    resolve_target,
    row_affordance_answers,
    unmet_operation_affordance,
)

from django_spec_html.http.request_principal import request_principal
from django_spec_html.http.route_arguments import route_arguments
from django_spec_html.http.utils import (
    check_request_hooks,
    checked_binding,
    checked_seeds,
    field_map,
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
"""The fragment a selector page's results are, addressed as ``<template_name>#spec-results`` by default."""

_SHIPPED_FRAGMENTS: Final = MappingProxyType(
    {
        "django_spec_html/spec_form.html": "django_spec_html/spec_form_fragment.html",
        "django_spec_html/spec_form_set.html": "django_spec_html/spec_form_set_fragment.html",
        "django_spec_html/spec_confirm.html": "django_spec_html/spec_confirm_fragment.html",
        "django_spec_html/spec_selector.html": "django_spec_html/spec_selector_fragment.html",
    }
)
"""Each page this package ships, a view's ``template_name`` by default, and the file its fragment is.

A file of its own rather than a ``spec-form`` ``{% partialdef %}`` inside
the page, because below Django 6.0 a page using that tag fails to parse
unless the project configures django-template-partials, and a default has to
render where nothing was configured for it. The page includes the
fragment, so the two never differ."""

_SUCCEEDED: Final = "spec-succeeded"
"""The event ``HX-Trigger`` names on a success answered in place, for the rest of the page."""

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
    """What every page reads off a request, whatever it serves: the hooks, the call context and the wrapper.

    The request's two hooks and the checks of what they answer, the call
    context handed to the kernel, the fragment predicate with the ``Vary``
    every answer carries, the fragment's templates, and the render choosing
    the page or the fragment. ``SpecPageBase``, the pages a post commits on,
    and ``SpecSelectorView``, a read served as a page, each build on it, so a
    page and a list read one request by one set of rules.
    ``fragment_name`` is the ``{% partialdef %}`` an htmx request is answered
    with by default, inside the page's own template.
    """

    fragment_template_name: str | None = None
    fragment_name: ClassVar[str] = _SPEC_FORM
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None = None
    server_arguments: ServerArguments | None = None

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """Django's ``dispatch``, every answer varying on the headers the wrapper is chosen by.

        Here rather than in either wrapper, so a 405 and a success redirect
        vary too. A denial is raised, so the host's 403 or 404 page sets its
        own headers.
        """
        response = super().dispatch(request, *args, **kwargs)
        patch_vary_headers(response, _MODE_HEADERS)
        return response

    def get_pool_seeds(self, request: HttpRequest) -> PoolSeeds:
        """The seeds this request's call is resolved with: ``pool_seeds`` unless overridden.

        Read once per request and handed to every step that resolves a spec
        callable: the class-level check, the target's lookup and its
        object-level or scope check, the form's ``form_kwargs``, the
        affordances the page asks about, and dispatch; on a selector page,
        the class-level check and dispatch. Seed what the
        request carries rather than the request itself, as on ``SpecView``:
        a spec callable naming ``request`` is handed this request here, and
        ``None`` on every reader that has none. A seed's resolver may name
        ``request`` or ``view`` and is handed this request and this page
        (test_a_seed_resolver_reading_the_request_binds).
        """
        return self.pool_seeds

    def get_server_arguments(self, request: HttpRequest) -> ServerArguments | None:
        """The values the server answers for on this request's call: ``server_arguments`` unless overridden.

        The names it binds and withholds are the declared
        ``server_arguments``' own, which ``as_view`` checked; only the values
        may vary per request, and the kernel's ``apply_server_arguments``
        checks each on every call.
        """
        return self.server_arguments

    def wants_fragment(self, request: HttpRequest) -> bool:
        """Whether ``request`` is answered with a fragment rather than the page.

        An htmx request is, unless it is boosted or a history restore: a
        boosted link or form swaps the whole body, and a history restore after
        a cache miss needs the whole page back, so both get the page. Read from
        django-htmx's ``request.htmx`` where its middleware set it, and from
        the three headers where it did not, so a host with django-htmx and one
        without get the same answer. Override it for another rule; the headers
        every answer varies on stay these three.

        Each condition, on each path, is held by its own row:
        ``test_read_from_the_headers`` (``htmx-false``, ``boosted``,
        ``history-restore``) and ``test_read_from_django_htmx_where_its_middleware_ran``
        (``not-htmx``, ``boosted``, ``history-restore``), whose headers say
        the opposite of the details, so reading the wrong one fails it.
        """
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
        """The templates a fragment renders: ``fragment_template_name``, or each page's ``#<fragment_name>``.

        The default addresses a ``{% partialdef %}`` inside the page's
        template, named by ``fragment_name``: ``spec-form`` on a page that
        posts, ``spec-results`` on a selector page. So turning htmx on is a
        template change. The tag
        and the ``#name`` address are Django 6.0's. Below it they are
        django-template-partials', configured in ``TEMPLATES`` with its tag
        as a builtin, ``"builtins": ["template_partials.templatetags.partials"]``,
        and its loader around the project's: installed as an app alone, it
        wraps the loader and the page fails to parse at ``{% partialdef %}``.
        Without either, the default is a ``TemplateDoesNotExist`` naming it.

        A page this package ships is the exception: its fragment is a file of
        its own, which the page includes, so the fragment is that file on
        every Django (test_the_shipped_page_s_fragment_is_its_own_file). A
        project overriding a shipped page's file keeps the shipped
        fragment, and overrides the fragment's file to change it.
        """
        if self.fragment_template_name is not None:
            return [self.fragment_template_name]
        return [
            _SHIPPED_FRAGMENTS.get(name) or _address(name, self.fragment_name)
            for name in self.get_template_names()
        ]

    def _served(self, request: HttpRequest) -> Served:
        """What both hooks answer for ``request``, checked as ``SpecView`` checks them, once.

        The per-call binding must bind and withhold the declared names, and a
        seed added per call may not take one of them; the checks are the
        JSON views', in ``http/utils.py``. The call context starts with
        ``request`` and ``view``, this request and this page, which the kernel
        hands to every stage naming either.
        """
        label = type(self).__name__
        server = checked_binding(label, self.get_server_arguments(request), self.server_arguments)
        seeds = checked_seeds(label, self.get_pool_seeds(request), self.pool_seeds, server)
        return Served(
            route_kwargs=self.route_kwargs,
            server=server,
            seeds=seeds,
            call_context={"request": request, "view": self},
        )

    def _rendered(self, context: dict[str, Any], *, status: int) -> HttpResponse:
        """``context`` as the page at ``status``, or as the form's fragment at 200.

        A fragment answers every outcome it renders at 200, since htmx swaps
        no 4xx by default and a refusal answered at one would land nowhere;
        the outcome is in the body, and in the one map.
        """
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
    """Everything a spec served as a page shares, whatever the page renders for its post.

    Admission, the target and its display, the affordances, the post's
    dispatch, the refusal's status and outcome, the success - a redirect,
    ``HX-Redirect`` or the result fragment, and its message - the fragment
    wrapper and the post that does not commit are all here, as
    ``SpecFormView`` documents them. A subclass supplies the hooks, each a
    step that differs with what the page renders: the spec it serves and the
    attributes read against it, checked at ``as_view()`` (``_check_spec``,
    ``_check_page``), the form it builds (``_form``), how a post's arguments
    are read from it (``_arguments``), and how the page and a refused post
    render it (``_render_page``, ``_render_refused``).
    """

    spec: ServiceSpec | None = None
    success_url: Any = None
    display_target: bool = False
    target_display_limit: int = 10
    commit_button: str | None = None
    success_message: str = ""
    redirect_field_name: str | None = None

    @classonlymethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., HttpResponseBase]:
        """Django's ``as_view``, then the spec, the redirect and the request hooks checked, once, for every request.

        The spec and the redirect first, so a view with no spec is refused as
        one whatever else it sets. Then the checks ``SpecView`` makes of the
        same attributes, by the same code: both hooks plain ``def``,
        ``route_kwargs`` naming only parameters the spec declares and no bare
        string, ``server_arguments`` through the kernel's
        ``check_server_arguments`` against the declared ``pool_seeds``, and
        no route kwarg filling a name the binding answers for. The page's own
        checks last, so a page reads ``route_kwargs`` and ``server_arguments``
        already known to be well formed.
        """
        # Django's own check of the keywords first, so a misspelt ``sepc=`` is
        # refused as the typo it is rather than read as a view with no spec.
        view = super().as_view(**initkwargs)
        # A page class missing a hook would build, and then fail on every
        # request: Django instantiates the view per request, where ``ABCMeta``
        # refuses it as a TypeError. Refused here instead, at URLconf import
        # (test_a_page_missing_a_hook_is_refused_at_as_view).
        if cls.__abstractmethods__:
            missing = ", ".join(sorted(cls.__abstractmethods__))
            raise ImproperlyConfigured(
                f"{cls.__qualname__} does not supply the page's hooks: {missing}."
            )
        _check_configuration(cls, initkwargs)
        spec = initkwargs.get("spec", cls.spec)
        check_request_hooks(cls, initkwargs, spec)
        cls._check_page(cls.__name__, spec, initkwargs)
        check_shipped_templates(cls, initkwargs)
        return view

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """The page, with an unbound form showing the target row, for a principal admitted to it."""
        return self._page(request, kwargs)

    def _page(
        self, request: HttpRequest, url_kwargs: Mapping[str, Any], **bound: Any
    ) -> HttpResponse:
        """The page GET renders, its form bound by ``bound`` for a post that does not commit.

        One path for both, so a post that does not commit is admitted, finds
        its target, asks the affordances and holds back what GET
        holds back, and renders what GET renders with the post's values in it
        (test_a_refresh_is_the_page_get_renders_with_the_post_in_it). Bound,
        the form is built from the post through ``form_kwargs`` like every
        other, so a choice its constructor narrows by a posted value is
        narrowed again (test_a_dependent_choice_is_narrowed_by_the_posted_value);
        its error state is set to none, as ``_render_refused`` sets a refused
        form's, so nothing is validated and no field is called required
        (test_a_post_without_the_button_neither_dispatches_nor_validates),
        unless the page's ``_refreshed`` judges it, as ``SpecFormView`` does
        under ``validate_on_refresh``. The context says so as ``refreshed``,
        absent from every other render.
        """
        spec = self.served_spec()
        served = self._served(request)
        principal, grant = admitted(spec, request, url_kwargs, served)
        try:
            target, _ = _found(spec, principal, grant, url_kwargs, served, ask_scope=True)
        except InvalidArguments as refused:
            # No post to place it on beside the form's: the route names a
            # target its reads refuse, which a post answers at 400 too.
            raise BadRequest(str(refused.detail)) from refused
        # One pool for the affordances and the presenter's context, so the
        # target's display costs no seed resolution of its own on GET.
        pool = call_pool(principal, served)
        unavailable = _unavailable(spec, target, served, pool)
        # Absent when every condition is met, as ``code`` is on a refusal
        # that has none, so a template's ``{% if unavailable %}`` reads one
        # context on every answer: test_every_condition_met_leaves_it_out.
        context: dict[str, Any] = {} if unavailable is None else {"unavailable": unavailable}
        form = self._form(spec, principal, target, served, **bound)
        if bound:
            context["refreshed"] = True
            self._refreshed(request, form, spec, principal, target, served, url_kwargs)
        context.update(self._target_context(spec, principal, target, served, pool))
        return self._render_page(form, bool(bound), **context)

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Dispatch the posted form: a redirect on success, the form re-rendered on a refusal.

        A post ``commits`` refuses is the page again, its form bound to the
        post, and nothing is dispatched.
        """
        if not self.commits(request):
            return self._page(request, kwargs, data=request.POST, files=request.FILES)
        spec = self.served_spec()
        # Admitted as ``get`` admits, before the form is built from the post:
        # re-rendering a refused post offers the page, and dispatch's shape
        # check - and ``coerce_flat`` before it - runs before its permission
        # check, so a principal the spec refuses was otherwise answered a
        # malformed post with the page, every row a choice field lists
        # included. A form's constructor may query, for choices it builds, so
        # it waits too. The grant hands dispatch the check already made, so it
        # runs once.
        served = self._served(request)
        principal, grant = admitted(spec, request, kwargs, served)
        # Then the target, found as the page finds it, so the form a refused
        # post re-renders shows the row - its held file, and the clear
        # checkbox beside it - and the object-level and scope checks and a
        # missing row answer before the post is read. Dispatch takes the
        # target and the grant vouching for it, so it looks nothing up again
        # and asks no check twice. A route value the target's reads refuse is
        # left to dispatch, whose one refusal carries it beside the form's:
        # test_a_url_kwarg_the_shape_check_refuses_is_a_prefixed_non_field_error.
        target: Any = UNSET
        with suppress(InvalidArguments):
            target, grant = _found(spec, principal, grant, kwargs, served)
        form = self._form(spec, principal, target, served, data=request.POST, files=request.FILES)
        try:
            read = self._arguments(form, spec, kwargs, served)
            result = dispatch(
                spec,
                principal=principal,
                arguments=with_binding(spec, read, served.server),
                grant=grant,
                pool_seeds=served.seeds,
                unknown_arguments=self.unknown_arguments,
                target=target,
                call_context=served.call_context,
            )
        except (DispatchError, ServiceError) as refused:
            if spec.many and isinstance(refused, InvalidArguments):
                # A many call's scope check is dispatch's fourth step, and an
                # ``InvalidArguments`` is the one refusal raised before it:
                # by the page's own reading, or by dispatch's shape check -
                # a list past ``many_max_length`` or short of
                # ``many_min_length``. Either way it may be unasked, so it is
                # asked here before the formset is shown, and a principal it
                # refuses is denied rather than shown every row a choice
                # field lists
                # (test_a_post_the_page_s_reading_refuses_asks_the_scope_check_first,
                # test_a_post_the_shape_check_refuses_is_denied_to_a_principal_the_scope_check_refuses).
                # A row the Validator refuses was refused after it, so there
                # it is asked twice: there is no grant that vouches for a
                # scope check, a many call taking no ``target=``
                # (test_a_row_the_validator_refuses_asks_the_scope_check_again).
                # A route value its reads refuse leaves it unasked, and the
                # refusal raised is the reading's
                # (test_a_route_value_and_a_row_s_coercion_are_refused_in_one_post).
                # One branch to coverage: ``spec.many`` is held by
                # test_a_refused_post_asks_the_scope_check_once, the form
                # page's check having been asked before the post was read, and
                # the refusal's kind by
                # test_a_service_s_refusal_asks_the_scope_check_once.
                with suppress(InvalidArguments):
                    _found(spec, principal, grant, kwargs, served, ask_scope=True)
            # The outcome is decided here, once, from the one map, and the
            # wrapper is chosen only after it. A denial raised by the
            # operation, the target's checks having answered before dispatch,
            # is the host's 403 or 404 page in either mode. Every row's
            # answer is held by test_the_pages_outcome_table_is_the_one_shipped.
            outcome = refusal_outcome(refused)
            if outcome is Outcome.DENIED:
                raise denial(refused) from refused
            # A question is read off the map's row, as every outcome is, never
            # off the refusal's class: one that is a dispatch refusal as well
            # answers at its dispatch row, failed, and asks nothing
            # (test_the_map_s_order_decides_what_is_a_question). That row
            # names ``AdditionalInputRequired`` alone, hence the cast. Its
            # schema is checked before either wrapper is chosen, so a question
            # no route could hear answered is raised on the page and in the
            # fragment alike, and with the call's own seeds, since a key a seed
            # the request registers fills would be dropped from the answer
            # (test_the_call_s_seeds_are_the_ones_it_reads).
            requested: AdditionalInputRequired | None = None
            if outcome is Outcome.NEEDS_INPUT:
                requested = cast("AdditionalInputRequired", refused)
                check_requested_input(spec, requested.schema, pool_seeds=served.seeds)
            # A page answering the refusal with something other than itself
            # answers here, once the outcome and the question are decided and
            # before the target is shown again, so a set is not counted and
            # no presenter asked for a page nobody renders
            # (test_a_refusal_sent_back_reads_no_target).
            elsewhere = self._refused_elsewhere(refused)
            if elsewhere is not None:
                return elsewhere
            # The target is shown again, and a set counted again, here and
            # never on a success: a post that redirects renders nothing.
            context = {
                **refusal_code(refused),
                **self._target_context(spec, principal, target, served, None),
            }
            if requested is not None:
                # What a page shows of a question whatever it renders; the
                # hook adds what its form can answer it with.
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
        # Never ``not_found``: the target was found before dispatch, which
        # looks nothing up when handed it, and where the route's value was
        # refused the shape check refuses it again before any lookup.
        # The message before either wrapper is chosen, so the redirect, the
        # ``HX-Redirect`` and the result fragment all carry it, and a host's
        # own ``fragment_success`` does too
        # (test_each_success_stores_the_message). ``fail_silently``, since
        # without the messages middleware there is nowhere to send it, and a
        # write already committed must not answer ``MessageFailure``
        # (test_without_the_middleware_nothing_is_raised). An empty message
        # is dropped by Django's storage
        # (test_an_empty_message_sends_nothing).
        messages.success(request, self.get_success_message(result), fail_silently=True)
        if self.wants_fragment(request):
            return self.fragment_success(result)
        return HttpResponseRedirect(self.get_redirect_url() or self.get_success_url(result))

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Django's context - ``view``, and ``extra_context`` - with the ``spec`` served, and the way back.

        Where ``get_redirect_url()`` answers a URL, the context carries
        ``redirect_field_name``, the key it was read from, and ``next``, the
        URL, so a page posting back to itself can send it again: the shipped
        templates render both as a hidden input. Two fixed keys, where
        Django's ``LoginView`` puts the URL under the field's own name, so a
        name such as ``errors`` or ``target`` never overwrites what the page
        renders. Both are absent where there is no URL to hold, an unsafe one
        included, so an unsafe value is never written back into the page
        (test_without_a_safe_value_neither_key_is_there).
        """
        kwargs.setdefault("spec", self.served_spec())
        way_back = self.get_redirect_url()
        if way_back:
            kwargs.setdefault("redirect_field_name", self.redirect_field_name)
            kwargs.setdefault("next", way_back)
        return super().get_context_data(**kwargs)

    def get_redirect_url(self) -> str:
        """The URL the request asks to return to, under ``redirect_field_name``, where it is safe; otherwise ``""``.

        ``None``, the default, reads nothing, so a page follows a
        client-chosen URL only where its view names the key, as Django's
        ``FormView`` never does and its ``LoginView`` does. Named, it is read
        as ``LoginView`` reads it: the post's value, then the query's, so a
        value posted empty masks the query's
        (test_an_empty_posted_next_masks_the_query_s_as_django_s_login_view_reads_it).
        Then it is used only where Django's ``url_has_allowed_host_and_scheme``
        allows it, against the request's own host and requiring ``https`` on a
        secure request, so the page is no open redirect. Each argument is held
        by its own tests: the host by
        test_a_next_naming_the_request_s_own_host_is_followed, which fails
        with no host allowed, and test_an_unsafe_next_falls_back_to_the_success_url,
        which fails with another; the scheme by the ``http-refused`` case of
        test_a_secure_request_follows_only_an_https_next, which fails with
        ``https`` never required, and by
        test_a_next_naming_the_request_s_own_host_is_followed, which fails
        with it always required.

        A success redirects to it, or under htmx is sent to it with
        ``HX-Redirect``, in place of ``get_success_url(result)``, even where
        that is overridden (test_next_wins_over_an_overridden_get_success_url);
        a ``spec-result`` fragment still answers in place, since it navigates
        nowhere. The page's arguments are its form's fields and its route, so
        the value never reaches dispatch (test_next_never_reaches_dispatch).
        A host's own ``fragment_success`` reads it the same way:
        ``self.get_redirect_url() or self.get_success_url(result)``.
        """
        name = self.redirect_field_name
        if name is None:
            return ""
        request = self.request
        url = request.POST.get(name, request.GET.get(name))
        safe = url_has_allowed_host_and_scheme(
            url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        )
        # Django's check strips the value before reading it, so the stripped
        # value is the one checked, and the one followed: the raw one would
        # reach ``Location`` with its padding encoded into the path
        # (test_a_padded_next_is_followed_as_it_was_checked).
        return cast("str", url).strip() if safe else ""

    def get_success_url(self, result: DispatchResult) -> str:
        """Where a successful post redirects: ``success_url``, resolved as ``redirect()`` reads it.

        Override it to read ``result``, such as the row a create returned.
        """
        return resolve_url(self.success_url)

    def get_success_message(self, result: DispatchResult) -> str:
        """The message a successful post sends: ``success_message`` formatted with the form's cleaned data.

        As Django's ``SuccessMessageMixin`` formats it, ``%`` with a mapping:
        ``result.data``, the cleaned data the Validator's form handed the
        service, so ``"%(title)s was added."`` reads the post's own title
        (test_it_is_formatted_with_the_cleaned_data). On a partial page a
        field left blank is not in it. An empty message sends nothing.
        Override it to read the result, such as the row a create returned,
        under ``result.value``.
        """
        return self.success_message % result.data

    def commits(self, request: HttpRequest) -> bool:
        """Whether a post runs the operation, or re-renders its form with the post's values.

        Every post commits while ``commit_button`` is ``None``. Once it names
        a button, a post commits only when its data carries that name, which
        is how a browser says which submit button was pressed, with or
        without JavaScript; htmx 2 adds the clicked button's name and value
        to a form's post, and sends no button's name for a request a control
        of its own issued, such as a ``<select hx-post>``.

        ``HX-Trigger-Name``, which htmx 2 sends, can only stop a commit, never
        make one. It names the element the request was issued from, and
        that element's own value is in the post when it is a control, while
        a form's ``name`` never is. So a header naming a key the post carries,
        other than the button, is another control's request, and does not
        commit even where something - an inherited ``hx-vals``, a hidden
        input - put the button's name in the data. A form's own ``name`` in
        the header, or none at all, leaves the decision to the data. A value
        htmx had to encode is read decoded, as
        ``HX-Trigger-Name-URI-AutoEncoded`` says.

        One branch to coverage, so each condition is held by its own test:
        test_a_post_without_the_button_neither_dispatches_nor_validates (the
        data must carry the button),
        test_a_trigger_naming_another_posted_control_does_not_commit (the
        header vetoes), test_a_trigger_that_is_the_button_itself_commits (the
        button issuing its own request is no other control) and
        test_a_trigger_naming_the_form_commits (a name the post does not
        carry is no control). Override it for another rule, such as two
        buttons that both commit.
        """
        button = self.commit_button
        if button is None:
            return True
        trigger = trigger_name(request)
        another = trigger != button and trigger in request.POST
        return button in request.POST and not another

    def fragment_success(self, result: DispatchResult) -> HttpResponse:
        """A success, answered to the htmx request that posted it.

        Where the fragment template defines a ``spec-result`` fragment, that
        is rendered in place at 200, and ``HX-Trigger`` names
        ``spec-succeeded`` for the rest of the page to listen for. Its context
        is the view's - ``view``, ``spec`` and ``extra_context`` - with
        ``result`` in it, and no form, since the post's form is done with.
        Otherwise ``HX-Redirect`` to ``get_redirect_url()``, where the request
        names a safe URL to return to, or to ``get_success_url(result)``, at
        200 and never a 302: an XHR follows a 302 itself and would swap the
        next page into the form. The result fragment is rendered whatever
        ``get_redirect_url()`` answers: it is an answer in place, with nowhere
        to go, and its context carries ``next`` for a link back
        (test_a_result_fragment_answers_in_place_whatever_next_says). The URL is encoded and checked as the page's redirect
        is, so a path outside ASCII reaches the browser as the page's
        ``Location`` would, and an unsafe scheme raises
        ``DisallowedRedirect`` in both modes.

        The answer passes back through the host's middleware like any other,
        so a cookie it sets for the request - the session a sign-in rotates,
        a message - is set on it. Override it to answer another way, with
        ``HX-Location``, say, or a 204 carrying ``HX-Trigger``.
        """
        try:
            template = select_template(
                [_address(name, _SPEC_RESULT) for name in self.get_fragment_template_names()],
                using=self.template_engine,
            )
        except TemplateDoesNotExist:
            template = None
        if template is None:
            # Outside the ``except``, so an error of ``get_success_url``'s own
            # is not reported as raised while handling the lookup.
            return ClientRedirect(self.get_redirect_url() or self.get_success_url(result))
        response = self.response_class(
            request=self.request,
            template=template,
            context=self.get_context_data(result=result),
            using=self.template_engine,
            content_type=self.content_type,
        )
        response["HX-Trigger"] = _SUCCEEDED
        return response

    def _target_context(
        self,
        spec: ServiceSpec,
        principal: Any,
        target: Any,
        served: Served,
        pool: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """What the page shows of the row or set the operation acts on, under the names a template reads.

        Nothing for a create, for a missing row ``allow_none`` lets through
        (test_a_create_carries_no_target,
        test_a_missing_row_allow_none_lets_through_carries_no_target) and for a
        post whose route was refused before any row was found
        (test_a_post_whose_route_is_refused_carries_no_target). A row is
        ``target``; a set is ``target`` beside ``target_count`` and
        ``target_counted_at``, counted here, at the moment the page is
        rendered. ``target_display`` is the target projected for a person,
        on a page that sets ``display_target``, as ``display_of`` builds it;
        otherwise no presenter is asked and no pool is built for it
        (test_a_page_not_opted_in_never_presents_its_target).

        The presenter is handed the context dispatch hands it: the principal,
        the target, the request's one call context, and as ``ambient`` the
        call's base pool - ``pool``, the one GET built for the affordances, or
        on a refused post one built here, only where a display is presented
        (test_the_presenter_is_handed_the_context_dispatch_hands_it).
        """
        # One branch to coverage, so each condition is held by its own test:
        # test_a_post_whose_route_is_refused_carries_no_target (the marker of a
        # target never looked for is never handed to a template) and
        # test_a_create_carries_no_target (``None`` is no row to show).
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
            # On a fresh clone, never the set itself: a refused post's service
            # may have read it, and its result cache holds the rows of a pass
            # the refusal rolled back
            # (test_a_refused_post_counts_the_set_as_it_is_now_not_as_the_service_read_it).
            # A COUNT, and the bounded rows in one query of their own, so no
            # row past the bound is read (test_it_counts_without_loading_a_row,
            # test_each_row_of_a_set_is_projected_up_to_the_bound).
            count = target.all().count()
            rows: list[Any] = list(target.all()[:limit]) if shown is not None else []
        else:
            # A list a selector answered, its rows in memory already
            # (test_a_set_answered_as_a_list_is_counted_as_one).
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
        """An answer to a refused post other than the page again, or ``None`` for the page.

        Asked of a refusal the post renders - never a denial, which is
        raised - before the target's context is built, so an answer that
        renders nothing reads nothing for it. ``None`` by default: every
        page re-renders its refusal through ``_render_refused``.
        ``SpecConfirmView`` answers its ``refusal_redirect`` here.
        """
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
        """Set the error state of the form a post that does not commit renders.

        Asked once the form is built from the post and before
        ``_render_page``, with what admitting the post and finding its
        target answered, so a page that judges a refresh binds it as the
        commit would, on the same row and with the same seeds. Nothing by
        default: the confirmation and formset pages leave it to
        ``_render_page``. ``SpecFormView`` answers its
        ``validate_on_refresh`` here.
        """

    def served_spec(self) -> ServiceSpec:
        """``spec``, which ``as_view`` refused to build a view without."""
        return cast("ServiceSpec", self.spec)

    # The hooks: each a step that differs with what the page renders, so a
    # subclass supplies every one.

    @classmethod
    @abstractmethod
    def _check_spec(cls, label: str, spec: ServiceSpec) -> None:
        """Refuse, at ``as_view()``, a spec this page cannot serve: its Validator, or its shape.

        Asked straight after the spec is checked to be a ``ServiceSpec``, and
        before anything else is read against it, so a later check reading the
        spec's form reads one that is there.
        """

    @classmethod
    @abstractmethod
    def _check_page(cls, label: str, spec: ServiceSpec, initkwargs: Mapping[str, Any]) -> None:
        """Refuse, at ``as_view()``, the subclass's own attributes read against what it renders.

        Asked last, once ``commit_button`` is known to be ``None`` or a
        button's name, so a check of it against the form's fields compares a
        string.
        """

    @abstractmethod
    def _form(
        self, spec: ServiceSpec, principal: Any, target: Any, served: Served, **bound: Any
    ) -> Any:
        """What the page renders for a person to post: unbound on GET, bound by ``bound`` otherwise.

        Built once the principal is admitted and the target found, for GET, a
        post that does not commit and a post that does. ``target`` is
        ``UNSET`` on a post whose route was refused before any row was found.
        """

    @abstractmethod
    def _arguments(
        self, form: Any, spec: ServiceSpec, url_kwargs: Mapping[str, Any], served: Served
    ) -> Mapping[str, Any]:
        """The arguments a committing post carries, read off ``form``, the route's kwargs among them.

        The binding is laid over them after, as dispatch's arguments. Read
        inside the post's refusal handling, so a refusal the reading raises is
        answered as dispatch's own.
        """

    @abstractmethod
    def _render_page(self, form: Any, refreshed: bool, /, **context: Any) -> HttpResponse:
        """The page GET renders at 200, ``form`` in it beside ``context``.

        ``refreshed`` for a post that does not commit, whose form is bound to
        the post and was never validated by the page's own render;
        ``_refreshed`` has set its error state already where the page sets
        one there.
        """

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
        """The page again for a refused post, at ``status``, ``form`` carrying ``refused``.

        ``target`` is what the post found, or ``UNSET`` where its route was
        refused; ``requested`` is the question a refusal asks, its schema
        checked already, or ``None``. A question is in ``context`` already as
        ``requested_input``, its message and its schema.
        """


@dataclasses.dataclass(frozen=True)
class Served:
    """What a request's hooks answered, read once and handed to every step that reads the route or a seed."""

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


def _check_configuration(cls: type[SpecPageBase], initkwargs: Mapping[str, Any]) -> None:
    """Refuse a view that could not serve its first request, naming what is missing."""
    label = cls.__name__
    spec = initkwargs.get("spec", cls.spec)
    if not isinstance(spec, ServiceSpec):
        raise ImproperlyConfigured(
            f"{label}.as_view() needs a ServiceSpec, the write a form posts; "
            f"got {type(spec).__name__}."
        )
    cls._check_spec(label, spec)
    redirects = initkwargs.get("get_success_url", cls.get_success_url)
    # One branch to coverage, so each condition is held by its own test:
    # test_a_valid_post_redirects_to_the_success_url (the first: a view with a
    # success_url is built) and
    # test_a_subclass_overriding_get_success_url_needs_no_success_url (the
    # second), with test_get_success_url_may_be_passed_to_as_view for the
    # override passed to ``as_view`` rather than written on a subclass.
    if (
        initkwargs.get("success_url", cls.success_url) is None
        and redirects is SpecPageBase.get_success_url
    ):
        raise ImproperlyConfigured(
            f"{label}.as_view() needs a success_url, where a successful post redirects, "
            "or a get_success_url that returns one."
        )
    limit = initkwargs.get("target_display_limit", cls.target_display_limit)
    # One branch to coverage, so each condition is held by its own case of
    # test_anything_but_a_positive_int_is_refused: ``'3'``, ``2.5`` and
    # ``None`` the first, ``True`` the second (a bool is an int, and ``True``
    # is positive), ``0`` and ``-1`` the third.
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ImproperlyConfigured(
            f"{label}.target_display_limit must be a positive int; got {limit!r}."
        )
    if initkwargs.get("display_target", cls.display_target):
        check_display(label, spec)
    button = initkwargs.get("commit_button", cls.commit_button)
    _check_commit_button(label, button)
    _check_redirect_field_name(
        label, initkwargs.get("redirect_field_name", cls.redirect_field_name), button
    )


def check_shipped_templates(cls: type[SpecRequestBase], initkwargs: Mapping[str, Any]) -> None:
    """Refuse a page rendering a template this package ships that the view's template engine does not find.

    The shipped templates are found as any app's are, so a project lists
    ``django_spec_html`` in ``INSTALLED_APPS`` with ``APP_DIRS`` on, or puts
    files of the same names in a directory of its own. Without either, every
    request would answer ``TemplateDoesNotExist``; asked here, the URLconf's
    import says what is missing instead. The page and its fragment both,
    since a project overriding the page alone in a directory of its own
    still renders the shipped fragment under htmx; the page alone when
    ``fragment_template_name`` names a fragment of the project's own, which
    is the one an htmx request renders
    (test_a_fragment_of_the_project_s_own_is_not_the_shipped_one), or when
    ``get_fragment_template_names`` is overridden, which may read the request
    (test_an_overridden_get_fragment_template_names_is_not_asked). One branch
    to coverage, so each condition is held by its own test.

    Those two names and no more: a template either of them extends or
    includes is not followed, so a project copying a shipped page that
    extends ``base.html`` into a directory of its own, without the app,
    needs ``base.html`` found there too, and is told so by the first
    request rather than here.

    Asked only of a page that renders a shipped template: one whose own
    ``template_name`` is a consumer's is never loaded here
    (test_a_page_s_own_template_is_not_looked_for), and nor is one whose
    ``get_template_names`` is overridden, since that may read the request
    (test_an_overridden_get_template_names_is_not_asked). One branch to
    coverage, so each condition is held by its own test.
    """
    page = initkwargs.get("template_name", cls.template_name)
    names = initkwargs.get("get_template_names", cls.get_template_names)
    if page not in _SHIPPED_FRAGMENTS or names is not TemplateResponseMixin.get_template_names:
        return
    # The engine the view names, not the default one: it is the engine every
    # request renders with (test_the_view_s_own_engine_is_the_one_asked).
    engine = initkwargs.get("template_engine", cls.template_engine)
    fragment = initkwargs.get("fragment_template_name", cls.fragment_template_name)
    fragments = initkwargs.get("get_fragment_template_names", cls.get_fragment_template_names)
    own = fragment is not None or fragments is not SpecRequestBase.get_fragment_template_names
    # Named, the engine is the one the advice is about: the app may be
    # installed and found by every engine but this one.
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
    """Refuse a ``commit_button`` that is not a button's name.

    ``None`` commits every post. Otherwise a non-empty string, the ``name``
    a submit button posts: one branch to coverage, held by the ``a-tuple``
    and ``empty`` cases of test_anything_but_a_button_s_name_is_refused.
    Whether it is the name of one of the form's fields is the page's own
    check, since only a page with a form has fields to clash with.
    """
    if button is None:
        return
    if not isinstance(button, str) or not button:
        raise ImproperlyConfigured(
            f"{label}.commit_button must be None or the name of the submit button whose post "
            f"commits; got {button!r}."
        )


def _check_redirect_field_name(label: str, name: Any, button: str | None) -> None:
    """Refuse a ``redirect_field_name`` that is not a key's name, or that is the ``commit_button``.

    ``None`` reads nothing. Otherwise a non-empty string: one branch to
    coverage, held by the ``a-tuple`` and ``an-int`` cases of
    test_anything_but_a_name_is_refused (the type) and its ``empty`` case
    (the length). The button's name is refused since a button posts its
    value, which would be read as the URL; ``commit_button`` was checked
    first, so the two compare as strings or ``None``. Whether it is the name
    of a field is each page's own check, since only a page with a form has
    fields to clash with.
    """
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
    """A redirect htmx follows as a navigation: ``HX-Redirect`` at 200, where the page answers 302.

    Built as Django's own redirect is, so the URL is IRI-encoded and an unsafe
    scheme raises ``DisallowedRedirect``, then moved from ``Location`` to
    ``HX-Redirect``: the shape of django-htmx's ``HttpResponseClientRedirect``,
    with nothing imported from it. Held by
    ``test_hx_redirect_is_the_page_s_location`` and
    ``test_an_unsafe_scheme_is_refused_in_both_modes``.
    """

    status_code = 200

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self["HX-Redirect"] = self["Location"]
        del self["Location"]

    @property
    def url(self) -> str:
        return self["HX-Redirect"]


def trigger_name(request: HttpRequest) -> str | None:
    """``HX-Trigger-Name``, decoded where htmx encoded it, or ``None`` where it was not sent.

    htmx 2 sends a value the browser refuses as a header URI-encoded, and
    says so in ``HX-Trigger-Name-URI-AutoEncoded``, as django-htmx reads it
    (test_an_encoded_trigger_name_is_read_decoded). One branch to coverage,
    so each condition is held by its own test:
    test_an_encoded_flag_with_no_name_is_no_trigger (there is no name to
    decode) and test_an_unencoded_trigger_name_is_read_as_sent (a ``%``
    htmx did not put there is the name's own).
    """
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
) -> tuple[Any, Grant]:
    """The principal and its grant, once the route and the class-level check pass.

    The route first, as ``SpecView`` refuses it on any method: a route
    capturing a kwarg the spec does not declare is wrong for every request and
    every principal, and a page served anyway fails only once someone has
    filled it in. A route capturing a name the server answers for is the
    host's too, refused by the rule ``request_arguments`` refuses it by. Then
    the principal the spec's class-level check admits, so neither the page
    nor anything the post reaches is offered to one it refuses. The check is
    asked with the request's seeds, resolving those it names, as dispatch
    asks it (test_the_class_level_check_is_asked_with_the_seed_it_names).
    """
    route_arguments(spec.parameters(), url_kwargs, route_kwargs=served.route_kwargs)
    refuse_server_owned(url_kwargs, served.route_kwargs, server_names(served.server))
    try:
        principal = request_principal(request)
        return principal, authorize(
            spec, principal, pool_seeds=served.seeds, call_context=served.call_context
        )
    except (NotPermitted, PrincipalUnavailable) as refused:
        raise denial(refused) from refused


def _found(
    spec: ServiceSpec,
    principal: Any,
    grant: Grant,
    url_kwargs: Mapping[str, Any],
    served: Served,
    *,
    ask_scope: bool = False,
) -> tuple[Any, Grant]:
    """The row, set or ``None`` the post acts on, and the grant vouching for it.

    Found by ``resolve_target`` as dispatch would find it, and refused as
    dispatch would refuse it, with Django's exceptions for the host's 403 and
    404 pages. A spec with no target selector - a create - resolves ``None``,
    which the scope check is asked about. The route's kwargs are coerced as a
    post's are, since a route captures strings and the target's reads are
    typed; a value they refuse raises ``InvalidArguments``, for the caller to
    answer. The server's binding is laid over them as dispatch's arguments
    are, so a target selector reading a bound parameter finds the row by the
    server's value, the one dispatch is then handed.

    A ``many`` spec has no target: ``resolve_target`` refuses one with
    ``ImproperlyConfigured`` and dispatch refuses ``target=``, ``None``
    included, asking the scope check of ``None`` itself on every call. So it
    is ``UNSET`` once the route is read, and the scope check is asked of
    ``None`` here only when ``ask_scope`` says dispatch will not ask it: on
    the page, so a principal the post would refuse is never offered it, and
    on a post the page's own reading refused; any other post leaves it to
    dispatch, which would otherwise ask it twice
    (test_a_principal_the_scope_check_refuses_is_never_offered_the_page,
    test_the_scope_check_is_asked_once_a_post).
    """
    parameters = spec.parameters()
    route = route_arguments(parameters, url_kwargs, route_kwargs=served.route_kwargs)
    arguments = with_binding(spec, coerce_flat(parameters, route), served.server)
    try:
        if spec.many:
            if ask_scope:
                authorize_scope(
                    spec,
                    principal,
                    None,
                    grant=grant,
                    arguments=arguments,
                    pool_seeds=served.seeds,
                    call_context=served.call_context,
                )
            return UNSET, grant
        resolved = resolve_target(
            spec,
            principal=principal,
            arguments=arguments,
            grant=grant,
            pool_seeds=served.seeds,
            call_context=served.call_context,
        )
    except (NotPermitted, PrincipalUnavailable, ServiceNotFound) as refused:
        raise denial(refused) from refused
    if isinstance(resolved, DispatchResult):
        raise Http404
    return resolved


def with_binding(
    spec: ServiceSpec | SelectorSpec, arguments: Mapping[str, Any], server: ServerArguments | None
) -> Mapping[str, Any]:
    """``arguments`` under ``server``, by the kernel's ``apply_server_arguments``, or as they are with no binding."""
    if server is None:
        return arguments
    return apply_server_arguments(spec, arguments, server)


def call_pool(principal: Any, served: Served) -> dict[str, Any]:
    """The pool the page's affordances and its display read, built from the call's own seeds.

    Built as dispatch builds its own: the seeds resolve against a pool
    carrying the call's context, so a resolver naming ``request`` or ``view``
    is handed this request and this page rather than raising ``TypeError``
    (test_a_seed_resolver_reading_the_request_binds, on GET and on a refused
    post that shows its target again). The context then leaves the pool, as
    it leaves dispatch's: it is no seed, and a presenter's ambient is this
    pool (test_the_displays_ambient_holds_the_seeds_and_never_the_call_context).
    The key is the kernel's reserved name, which its root does not export.
    """
    pool = base_pool(user=principal, seeds=served.seeds, call_context=served.call_context)
    del pool["call_context"]
    return pool


def _unavailable(
    spec: ServiceSpec, target: Any, served: Served, pool: dict[str, Any]
) -> Affordance | None:
    """The first of the spec's affordances not met for ``target`` now, or ``None``.

    Asked as the kernel says a transport offering an operation asks: the
    callable conditions through ``unmet_operation_affordance``, on ``pool``,
    the one ``base_pool`` built for the page from the call's own seeds, with ``reserved`` from
    the same ones, so a condition reading a seed ``get_pool_seeds`` added
    sees it. Then, with none of those unmet and a row to ask about, the
    conditions on the row, by the kernel's ``enforce_affordances``, which
    dispatch runs. Its ``ActionUnavailable`` carries the unmet affordance's
    ``code``, and a spec's codes are unique, which ``ServiceSpec`` refuses
    otherwise, so the code finds the very ``Affordance``.

    The callables are asked first, so where a row condition declared before
    an unmet callable is unmet too, this names the callable while the post
    is refused with the row's, in declaration order. The answer is advice:
    dispatch asks every condition again, and its refusal is the authority.

    A row the kernel finds gone when its condition is reached is ``Http404``,
    as the post's ``ServiceNotFound`` is.
    """
    seeds = served.seeds
    # ``reserved`` from the call's own seeds, or a condition reading a seed
    # added per call is never handed it:
    # test_a_condition_reading_a_per_call_seed_is_asked_with_it.
    unmet = unmet_operation_affordance(
        spec, pool, reserved=seeds.reserved, call_context=served.call_context
    )
    # One branch to coverage, so each condition is held by its own test:
    # test_a_callable_condition_is_named_before_a_row_condition_declared_first
    # (an unmet callable answers before the row is asked) and
    # test_no_row_asks_no_row_condition (``None`` is no row, and the kernel
    # refuses to ask a row condition about it).
    if unmet is not None or not isinstance(target, models.Model):
        return unmet
    try:
        # ``reserved`` here too, since this asks the callables again:
        # test_the_row_s_conditions_are_asked_with_the_call_s_seeds.
        enforce_affordances(
            spec,
            pool,
            instance=target,
            reserved=seeds.reserved,
            call_context=served.call_context,
        )
    except ActionUnavailable as refused:
        # Never ``None`` here, since a spec with no affordances refuses nothing.
        return next(each for each in spec.affordances or () if each.code == refused.code)
    except ServiceNotFound as refused:
        raise denial(refused) from refused
    return None


def display_of(spec: ServiceSpec | SelectorSpec) -> tuple[Presenter, AudienceProjection]:
    """The presenter and the markings a person's view of a row is read through.

    Asked only of a page that displays its rows - a page that set
    ``display_target``, or a selector page rendering its rows by the
    shipped template - whose spec ``as_view`` checked has an Output marking
    one of its own fields, so there is a presenter to present through: a
    service spec's ``presenter_for_output()``, a selector spec's own. The
    projection is built per request rather than kept from that check: a
    choice's display is read in the language active when it is built.
    """
    presenter = spec.presenter if isinstance(spec, SelectorSpec) else spec.presenter_for_output()
    return cast("Presenter", presenter), audience_projection_for_spec(spec)


def check_display(
    label: str,
    spec: ServiceSpec | SelectorSpec,
    *,
    shown: str = "its target",
    remedy: str = "leave display_target off",
) -> None:
    """Refuse a page displaying ``shown`` whose spec's Output cannot display it, naming both.

    Three refusals, each its own test in ``TestDisplayingTheTargetIsOptIn``:
    a spec with no Output (test_a_spec_with_no_output_is_refused); one whose
    projection the kernel refuses, such as two fields marked ``LABEL``
    (test_an_output_the_projection_refuses_is_refused_naming_the_view); and
    one marking none of its own fields, its choices alone or only a nested
    object's (test_an_output_marking_none_of_its_own_fields_is_refused), since
    a person's display is driven by the row's own markings. Whether the Output
    describes the target's rows is not known until one is presented. A
    selector page asks the same of its rows, with its own ``shown`` and
    ``remedy``, and names the selector, where a service spec names its service
    (``TestTheRowsDisplay`` in ``test_spec_selector_view``).
    """
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
    """One presented row as a person reads it: its ``fields``, the same as ``pairs``, and its ``label``.

    ``pairs`` is the fields as name and value, in order, for a template to
    loop over: ``fields.items`` in a template looks the key ``items`` up
    first, so an Output with a field of that name - an order's, a basket's -
    would loop over that field's value instead
    (test_a_field_named_items_is_shown_as_a_field).

    Projected by the kernel's ``project_payload`` - a ``HIDDEN`` field
    dropped, a choice spoken as its display - and then
    with every ``HANDLE`` dropped, at any depth: a handle is kept for an agent,
    which passes it to another tool, and is never read out to a person
    (test_a_row_is_named_by_its_label_with_no_handle_and_nothing_hidden,
    test_a_nested_handle_is_never_shown). ``label`` is the value of the field
    marked ``LABEL``, and absent where none is, or where the row left it out
    (test_a_row_with_no_label_has_none_in_its_display).
    """
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
    """The refusal's ``code`` under that name, for a refusal carrying one, as ``error_response`` puts it in the body."""
    if isinstance(refused, ActionUnavailable):
        return {"code": refused.code}
    return {}


def denial(refused: DispatchError | ServiceError) -> PermissionDenied | Http404:
    """A refusal answered 403 or 404 as Django's own exception, for the host's 403 and 404 pages."""
    if refusal_status(refused) == 403:
        return PermissionDenied(refused.message)
    return Http404(refused.message)


def without_errors(form: forms.BaseForm) -> None:
    """Set a bound form's error state to none, so rendering it validates nothing.

    Django's own two attributes, as ``full_clean()`` starts them, with the
    form's renderer as Django 5.0 and later pass it; ``Any``, as the stubs
    declare neither as assignable. A refused post's form then carries the
    refusal alone, and a post that does not commit shows no error at all.
    """
    bound: Any = form
    bound._errors = ErrorDict(renderer=form.renderer)
    bound.cleaned_data = {}


def form_values(form: forms.BaseForm) -> dict[str, Any]:
    """The values a bound form's post carries, read as the form reads it and typed for the shape check.

    A field naming a parameter the server answers for is never read: the
    form page disables it, and a disabled field is skipped, so a value the
    client was never offered is never coerced or refused as the client's;
    the caller lays the binding over the result. Every value comes back
    typed as the shape check reads it, or a string for ``coerce_flat`` to
    type by its declaration, which passes a value that is not a string
    through untouched: the caller coerces it, with whatever the route adds,
    so one refusal carries every problem. The form page and each row of the
    formset page read their forms through it.
    """
    read: dict[str, Any] = {}
    for name, field in form.fields.items():
        if field.disabled:
            continue
        value = form[name].data
        widget = field.widget
        # A ticked clear checkbox is read by the file's widget, as ``False``,
        # or as a marker where a file was uploaded beside it. Neither is a
        # file the shape check takes, and the Validator declares the checkbox
        # as an argument of its own, named as the widget names it: so the
        # checkbox is passed as that, and the file as what was uploaded, if
        # anything was. With nothing uploaded the file is absent, and the
        # Validator's form reads the checkbox as a cleared file; beside an
        # upload, that form refuses the pair in its own words. One
        # branch to coverage, so each condition is held by its own test:
        # test_a_checked_box_is_true_and_an_unchecked_one_false (the widget:
        # an unticked ``BooleanField`` reads ``False`` too),
        # test_the_rendered_checkbox_ticked_clears_the_file (``False``) and
        # test_an_upload_and_the_ticked_box_are_refused_in_the_form_s_words
        # (the marker).
        if isinstance(widget, forms.ClearableFileInput) and (
            value is False or value is FILE_INPUT_CONTRADICTION
        ):
            read[widget.clear_checkbox_name(name)] = True
            value = form.files.get(form[name].html_name)
        # A form reads a missing field and a blank one alike, so both are
        # absent here and the shape check never sees a blank it would refuse
        # where the form takes it. One branch to coverage, so each condition is
        # held by its own test: test_a_required_field_never_posted_is_refused_once
        # (``None``) and test_a_blank_optional_field_is_absent (``""``).
        if value is None or value == "":
            continue
        read[name] = _wire(field, value)
    return read


def _wire(field: forms.Field, value: Any) -> Any:
    """``value`` as the field reads it, in the wire form the shape check reads, where they differ.

    A value the field refuses, or reads as a number no JSON caller can send,
    is passed on as it came, so the refusal is the kernel's own - in the words
    the field would have used.
    """
    if not isinstance(field, _TYPED):
        return value
    try:
        # ``Any``, because the stubs give each class its base's return: a
        # FloatField is an IntegerField to them, and returns a float.
        typed: Any = field.to_python(value)
    except ValidationError:
        return value
    if isinstance(typed, Decimal):
        return str(typed)
    if isinstance(typed, datetime.date):
        # A datetime is a date too, and each writes its own ISO form: the
        # date-time with its offset, since the field made it aware.
        return typed.isoformat()
    # Only a float can be no finite number, and asking an int, which an
    # ``IntegerField`` reads any number of digits as, converts it to one,
    # raising OverflowError past a float's range. One branch to coverage, so
    # each condition is held by its own test:
    # test_an_integer_past_a_float_s_range_reaches_the_validator (the float)
    # and test_localized_numbers_are_read_as_the_form_reads_them (its
    # finiteness: without it, every float is sent on as it was typed).
    if isinstance(typed, float) and not math.isfinite(typed):
        return value
    return typed


_ANSWERS: Final = "_django_spec_html_answers"
"""The attribute a served row carries its affordance answers under, for the ``available`` filter.

Underscored, so a template cannot read it past the filter, and named for this
package, so it takes no name a model or an annotation could hold."""


class _AnsweredRow(dict):
    """A mapping row's copy, which can carry the answers a ``dict`` cannot hold as an attribute.

    A copy rather than the row, as the kernel's ``rows_with_affordances`` adds
    a mapping's answers to a new mapping and leaves the selector's own object
    alone: a list a selector returns may be a module's constant, which a page
    must not write to (test_a_mapping_row_is_copied_and_the_selector_s_own_left_alone).
    A template and a table read its keys as the row's.
    """


def answered_rows(spec: SelectorSpec, rows: list[Any]) -> list[Any]:
    """``rows``, each carrying its affordance answers for the ``available`` filter, read once here.

    The answers are the kernel's ``row_affordance_answers``, which reads the
    flags dispatch annotated in bulk, so asking costs no query whatever the
    number of rows or of affordances. A model instance carries them as an
    attribute; a mapping row is copied into one that can. A row that takes no
    attribute - a tuple from ``values_list()``, a frozen dataclass - is
    served as it is, carrying none, and the filter says so when asked about
    one (test_a_row_that_takes_no_attribute_is_served_without_answers). The
    kernel refuses a declared condition on any row but an instance or a
    mapping before this is reached, so such a row's spec declares no
    affordances and its answers would have been empty.
    """
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
    """The refusal a re-rendered form carries: an argument refusal in place, any other as its message."""
    if isinstance(refused, InvalidArguments):
        return refused.detail
    if isinstance(refused, ServiceValidationError):
        # A string, a list or a lazy string is about the whole form.
        return field_map(refused.detail)
    return {NON_FIELD_ERRORS: [refused.message]}
