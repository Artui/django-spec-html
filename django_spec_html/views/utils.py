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
from abc import ABCMeta, abstractmethod
from collections.abc import Callable, Mapping
from contextlib import suppress
from types import MappingProxyType
from typing import Any, Final, cast
from urllib.parse import unquote

from django.contrib import messages
from django.core.exceptions import (
    BadRequest,
    ImproperlyConfigured,
    PermissionDenied,
)
from django.db import models
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
from django.template.loader import select_template
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.decorators import classonlymethod
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
    ServerArguments,
    ServiceError,
    ServiceNotFound,
    ServiceSpec,
    ServiceValidationError,
    UnknownArguments,
    apply_server_arguments,
    audience_projection_for_spec,
    authorize,
    base_pool,
    check_requested_input,
    coerce_flat,
    dispatch,
    enforce_affordances,
    project_payload,
    resolve_target,
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

_SUCCEEDED: Final = "spec-succeeded"
"""The event ``HX-Trigger`` names on a success answered in place, for the rest of the page."""

_TRIGGER_NAME: Final = "HX-Trigger-Name"
"""The ``name`` of the element an htmx 2 request was issued from: a form, or a control of its own.

A header htmx 2 sends, and not every client that posts the form does, which
is why ``commits`` reads it only to stop a commit the post's data would
otherwise make."""


class SpecPageBase(TemplateResponseMixin, ContextMixin, View, metaclass=ABCMeta):
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
    fragment_template_name: str | None = None
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None = None
    server_arguments: ServerArguments | None = None
    display_target: bool = False
    target_display_limit: int = 10
    commit_button: str | None = None
    success_message: str = ""

    @classonlymethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., HttpResponseBase]:
        """Django's ``as_view``, then the spec, the redirect and the request hooks checked, once, for every request.

        The spec and the redirect first, so a view with no spec is refused as
        one whatever else it sets. Then the checks ``SpecView`` makes of the
        same attributes, by the same code: both hooks plain ``def``,
        ``route_kwargs`` naming only parameters the spec declares and no bare
        string, ``server_arguments`` through the kernel's
        ``check_server_arguments`` against the declared ``pool_seeds``, and
        no route kwarg filling a name the binding answers for.
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
        check_request_hooks(cls, initkwargs, initkwargs.get("spec", cls.spec))
        return view

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """Django's ``dispatch``, every answer varying on the headers the wrapper is chosen by.

        Here rather than in either wrapper, so a 405 and a success redirect
        vary too. A denial is raised, so the host's 403 or 404 page sets its
        own headers.
        """
        response = super().dispatch(request, *args, **kwargs)
        patch_vary_headers(response, _MODE_HEADERS)
        return response

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
        (test_a_post_without_the_button_neither_dispatches_nor_validates). The
        context says so as ``refreshed``, absent from every other render.
        """
        spec = self.served_spec()
        served = self._served(request)
        principal, grant = _admitted(spec, request, url_kwargs, served)
        try:
            target, _ = _found(spec, principal, grant, url_kwargs, served)
        except InvalidArguments as refused:
            # No post to place it on beside the form's: the route names a
            # target its reads refuse, which a post answers at 400 too.
            raise BadRequest(str(refused.detail)) from refused
        # One pool for the affordances and the presenter's context, so the
        # target's display costs no seed resolution of its own on GET.
        pool = base_pool(user=principal, seeds=served.seeds)
        unavailable = _unavailable(spec, target, served, pool)
        # Absent when every condition is met, as ``code`` is on a refusal
        # that has none, so a template's ``{% if unavailable %}`` reads one
        # context on every answer: test_every_condition_met_leaves_it_out.
        context: dict[str, Any] = {} if unavailable is None else {"unavailable": unavailable}
        form = self._form(spec, principal, target, served, **bound)
        if bound:
            context["refreshed"] = True
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
        principal, grant = _admitted(spec, request, kwargs, served)
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
                arguments=_bound(spec, read, served.server),
                grant=grant,
                pool_seeds=served.seeds,
                unknown_arguments=self.unknown_arguments,
                target=target,
                call_context=served.call_context,
            )
        except (DispatchError, ServiceError) as refused:
            # The outcome is decided here, once, from the one map, and the
            # wrapper is chosen only after it. A denial raised by the
            # operation, the target's checks having answered before dispatch,
            # is the host's 403 or 404 page in either mode. Every row's
            # answer is held by test_the_pages_outcome_table_is_the_one_shipped.
            outcome = refusal_outcome(refused)
            if outcome is Outcome.DENIED:
                raise _denial(refused) from refused
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
            # The target is shown again, and a set counted again, here and
            # never on a success: a post that redirects renders nothing.
            context = {
                **_refusal_code(refused),
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
        return HttpResponseRedirect(self.get_success_url(result))

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Django's context - ``view``, and ``extra_context`` - with the ``spec`` served."""
        kwargs.setdefault("spec", self.served_spec())
        return super().get_context_data(**kwargs)

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

    def get_pool_seeds(self, request: HttpRequest) -> PoolSeeds:
        """The seeds this request's call is resolved with: ``pool_seeds`` unless overridden.

        Read once per request and handed to every step that resolves a spec
        callable: the class-level check, the target's lookup and its
        object-level or scope check, the form's ``form_kwargs``, the
        affordances the page asks about, and dispatch. Seed what the
        request carries rather than the request itself, as on ``SpecView``:
        a spec callable requiring a ``request`` raises ``TypeError`` on every
        reader that has none.
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
        trigger = _trigger_name(request)
        another = trigger != button and trigger in request.POST
        return button in request.POST and not another

    def get_fragment_template_names(self) -> list[str]:
        """The templates a fragment's form renders: ``fragment_template_name``, or each page's ``#spec-form``.

        The default addresses a ``{% partialdef spec-form %}`` inside the
        page's template, so turning htmx on is a template change. The tag
        and the ``#name`` address are Django 6.0's. Below it they are
        django-template-partials', configured in ``TEMPLATES`` with its tag
        as a builtin, ``"builtins": ["template_partials.templatetags.partials"]``,
        and its loader around the project's: installed as an app alone, it
        wraps the loader and the page fails to parse at ``{% partialdef %}``.
        Without either, the default is a ``TemplateDoesNotExist`` naming it.
        """
        if self.fragment_template_name is not None:
            return [self.fragment_template_name]
        return [_address(name, _SPEC_FORM) for name in self.get_template_names()]

    def fragment_success(self, result: DispatchResult) -> HttpResponse:
        """A success, answered to the htmx request that posted it.

        Where the fragment template defines a ``spec-result`` fragment, that
        is rendered in place at 200, and ``HX-Trigger`` names
        ``spec-succeeded`` for the rest of the page to listen for. Its context
        is the view's - ``view``, ``spec`` and ``extra_context`` - with
        ``result`` in it, and no form, since the post's form is done with.
        Otherwise ``HX-Redirect`` to ``get_success_url(result)``, at 200 and
        never a 302: an XHR follows a 302 itself and would swap the next page
        into the form. The URL is encoded and checked as the page's redirect
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
            return _ClientRedirect(self.get_success_url(result))
        response = self.response_class(
            request=self.request,
            template=template,
            context=self.get_context_data(result=result),
            using=self.template_engine,
            content_type=self.content_type,
        )
        response["HX-Trigger"] = _SUCCEEDED
        return response

    def _served(self, request: HttpRequest) -> Served:
        """What both hooks answer for ``request``, checked as ``SpecView`` checks them, once.

        The per-call binding must bind and withhold the declared names, and a
        seed added per call may not take one of them; the checks are the
        JSON views', in ``http/utils.py``.
        """
        label = type(self).__name__
        server = checked_binding(label, self.get_server_arguments(request), self.server_arguments)
        seeds = checked_seeds(label, self.get_pool_seeds(request), self.pool_seeds, server)
        return Served(route_kwargs=self.route_kwargs, server=server, seeds=seeds, call_context={})

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
        on a page that sets ``display_target``, as ``_display`` builds it;
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
        shown = _display(spec) if self.display_target else None
        presented: PresentationContext | None = None
        if shown is not None:
            ambient = base_pool(user=principal, seeds=served.seeds) if pool is None else pool
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
                context["target_display"] = _displayed(
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
                _displayed(each, projection)
                for each in presenter.presented_rows(tuple(rows), presented)
            ]
        return context

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
        the post and was never validated.
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

    The class-level check, the target's lookup and its checks, the affordances,
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
        _check_display(label, spec)
    _check_commit_button(label, initkwargs.get("commit_button", cls.commit_button))
    cls._check_page(label, spec, initkwargs)


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


class _ClientRedirect(HttpResponseRedirectBase):
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


def _trigger_name(request: HttpRequest) -> str | None:
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


def _admitted(
    spec: ServiceSpec, request: HttpRequest, url_kwargs: Mapping[str, Any], served: Served
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
        raise _denial(refused) from refused


def _found(
    spec: ServiceSpec,
    principal: Any,
    grant: Grant,
    url_kwargs: Mapping[str, Any],
    served: Served,
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
    """
    parameters = spec.parameters()
    route = route_arguments(parameters, url_kwargs, route_kwargs=served.route_kwargs)
    try:
        resolved = resolve_target(
            spec,
            principal=principal,
            arguments=_bound(spec, coerce_flat(parameters, route), served.server),
            grant=grant,
            pool_seeds=served.seeds,
            call_context=served.call_context,
        )
    except (NotPermitted, PrincipalUnavailable, ServiceNotFound) as refused:
        raise _denial(refused) from refused
    if isinstance(resolved, DispatchResult):
        raise Http404
    return resolved


def _bound(
    spec: ServiceSpec, arguments: Mapping[str, Any], server: ServerArguments | None
) -> Mapping[str, Any]:
    """``arguments`` under ``server``, by the kernel's ``apply_server_arguments``, or as they are with no binding."""
    if server is None:
        return arguments
    return apply_server_arguments(spec, arguments, server)


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
        raise _denial(refused) from refused
    return None


def _display(spec: ServiceSpec) -> tuple[Presenter, AudienceProjection]:
    """The presenter and the markings a person's view of the target is read through.

    Asked only of a page that set ``display_target``, whose spec ``as_view``
    checked has an Output marking one of its own fields, so there is a
    presenter to present through. The projection is built per request rather
    than kept from that check: a choice's display is read in the language
    active when it is built.
    """
    return cast("Presenter", spec.presenter_for_output()), audience_projection_for_spec(spec)


def _check_display(label: str, spec: ServiceSpec) -> None:
    """Refuse a page displaying its target whose spec's Output cannot display one, naming both.

    Three refusals, each its own test in ``TestDisplayingTheTargetIsOptIn``:
    a spec with no Output (test_a_spec_with_no_output_is_refused); one whose
    projection the kernel refuses, such as two fields marked ``LABEL``
    (test_an_output_the_projection_refuses_is_refused_naming_the_view); and
    one marking none of its own fields, its choices alone or only a nested
    object's (test_an_output_marking_none_of_its_own_fields_is_refused), since
    a person's display is driven by the row's own markings. Whether the Output
    describes the target's rows is not known until one is presented.
    """
    service = spec.service
    named = f"the spec of {getattr(service, '__qualname__', type(service).__qualname__)}"
    if spec.output() is None:
        raise ImproperlyConfigured(
            f"{label} displays its target through its spec's Output, and {named} declares "
            "none: give it a presenter or an output_selector_spec, or leave display_target off."
        )
    try:
        projection = audience_projection_for_spec(spec)
    except ImproperlyConfigured as refused:
        raise ImproperlyConfigured(
            f"{label} cannot display the target of {named}: {refused}"
        ) from refused
    if not projection.fields:
        raise ImproperlyConfigured(
            f"{label} displays its target by its Output's markings, and {named}'s Output "
            "marks none of its own fields with a FieldMarking: mark the field naming the "
            "row FieldMarking.label(), or leave display_target off."
        )


def _displayed(presented: Any, projection: AudienceProjection) -> dict[str, Any]:
    """One presented row as a person reads it: its ``fields``, and the ``label`` naming it.

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
    if projection.label in fields:
        return {"label": fields[projection.label], "fields": fields}
    return {"fields": fields}


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


def _refusal_code(refused: DispatchError | ServiceError) -> dict[str, str]:
    """The refusal's ``code`` under that name, for a refusal carrying one, as ``error_response`` puts it in the body."""
    if isinstance(refused, ActionUnavailable):
        return {"code": refused.code}
    return {}


def _denial(refused: DispatchError | ServiceError) -> PermissionDenied | Http404:
    """A refusal answered 403 or 404 as Django's own exception, for the host's 403 and 404 pages."""
    if refusal_status(refused) == 403:
        return PermissionDenied(refused.message)
    return Http404(refused.message)


def refusal_tree(refused: DispatchError | ServiceError) -> Mapping[Any, Any]:
    """The refusal a re-rendered form carries: an argument refusal in place, any other as its message."""
    if isinstance(refused, InvalidArguments):
        return refused.detail
    if isinstance(refused, ServiceValidationError):
        # A string, a list or a lazy string is about the whole form.
        return field_map(refused.detail)
    return {NON_FIELD_ERRORS: [refused.message]}
