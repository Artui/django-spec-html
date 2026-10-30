"""``SpecConfirmView`` - a spec with no Validator, served as the page that confirms it."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.contrib import messages
from django.contrib.messages import MessageFailure
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import resolve_url
from django_service_specs import (
    AdditionalInputRequired,
    DispatchError,
    DispatchResult,
    FormValidator,
    InvalidArguments,
    PoolSeeds,
    ServerArguments,
    ServiceError,
    ServiceSpec,
    bind_arguments,
    coerce_flat,
    refusal_messages,
    server_owned_keys,
)

from django_spec_html.http.route_arguments import route_arguments
from django_spec_html.http.utils import route_targets
from django_spec_html.views.utils import ClientRedirect, Served, SpecPageBase, refusal_tree


class SpecConfirmView(SpecPageBase):
    """A ``ServiceSpec`` with no Validator, served as a page showing what it acts on and a button.

    For an operation a person confirms rather than fills in - withdrawing a
    row, archiving a set - whose every argument the server answers: the
    route, a pool seed, a ``kwargs`` provider or ``server_arguments``. The
    spec declares ``validator=None``:

        urlpatterns = [
            path(
                "books/<int:pk>/withdraw/",
                SpecConfirmView.as_view(
                    spec=withdraw_book_spec,
                    success_url=reverse_lazy("books"),
                    route_kwargs=("pk",),
                ),
            ),
        ]

    **What it shares with** ``SpecFormView`` is everything but the form: the
    principal admitted by the class-level check before anything is shown,
    the target found as dispatch finds it - ``target``, a set's
    ``target_count`` and ``target_counted_at``, and ``target_display`` on a
    page that sets ``display_target`` - the affordances asked on GET as
    ``unavailable``, ``commit_button``, the fragment wrapper under htmx, the
    request hooks, ``success_url``, ``redirect_field_name`` and
    ``success_message``, each as ``SpecFormView`` documents it.

    **GET** renders ``template_name`` with that context, beside ``view`` and
    ``spec``, and **no** ``form``: there is nothing to fill in, so the
    template renders its own ``<form method="post">``, the CSRF token and
    the submit button. A key with nothing to hold is absent, as on every
    page, so ``{{ form }}`` is never ``None``.

    **The page's template is optional**, as on ``SpecFormView``:
    ``django_spec_html/spec_confirm.html`` by default, which this package
    ships, with its button in ``django_spec_html/spec_confirm_fragment.html``.
    It renders the target pane, an unmet affordance's reason with the button
    disabled, a refused post's ``errors`` or a question's message alone, and
    the button, posting ``commit_button``'s name where one is set, and the
    URL to return to as a hidden input where the view reads one. Found,
    refused at ``as_view()`` and overridden as ``SpecFormView``'s is.

    **POST** dispatches with the route's kwargs, read by ``route_kwargs`` and
    coerced as a form page's are, under the binding and with the request's
    seeds. **Nothing the post carries becomes an argument**: not the CSRF
    token, not the button, not ``redirect_field_name``'s key and not a key
    named like a parameter, so the closed argument set holds with nothing to
    check. A success redirects to ``get_redirect_url()`` where the request
    names a safe URL to return to, else to ``get_success_url(result)``, or
    under htmx answers ``HX-Redirect`` to the same URL or the ``spec-result``
    fragment, with ``success_message`` sent through ``django.contrib.messages``
    where its middleware runs.

    **A refused post is the confirmation page again**, at the status of the
    one map - 400 for an argument refusal, 409 for a conflict or an
    unavailable action, 422 for any other service refusal - or as the
    fragment at 200 under htmx, and needs no messages app. Its context
    carries ``errors``, every message the refusal holds as a list of
    strings, a message about a parameter prefixed with its path
    (``"pk: Enter a whole number."``), and ``code`` for a refusal carrying
    one. A denial is raised for the host's 403 or 404 page, as on any page.

    **A refused post may return where it came from instead.** A button in a
    list's row posts to this page, and a refusal rendered here leaves the
    person on a page they never opened. With ``refusal_redirect``, a refused
    post that is invalid, failed or a question sends each message of
    ``errors`` through ``messages.error``, with the refusal's ``code`` as
    ``extra_tags`` where it has one, and redirects to
    ``get_refusal_url(refused)``: the safe URL ``redirect_field_name`` reads,
    else ``success_url``. Under htmx it answers ``HX-Redirect`` to the same
    URL. A denial is raised as ever. Without the messages middleware there is
    nowhere to keep the messages, so the page is rendered as without
    ``refusal_redirect``.

    **A question is shown, and cannot be answered here.** A service raising
    ``AdditionalInputRequired`` re-renders the page at 422, its schema first
    checked by the kernel's ``check_requested_input``, with
    ``requested_input`` - the ``message`` and the ``schema`` - and the
    message in ``errors``. The page builds no control from a schema, and no
    post is read for an answer, so a confirming post is asked again. An
    operation that asks a person something mid-flight declares a form that
    holds the answer, and is served by ``SpecFormView`` with its
    ``asked_fields``.

    ``as_view()`` refuses, when the URLconf is imported, a spec with a
    Validator - a ``FormValidator``'s is ``SpecFormView``'s to serve - and
    a spec demanding a parameter that nothing but a post could supply,
    besides everything the page base refuses. Which parameters a call
    demands is the kernel's answer, through ``bind_arguments`` on a call
    carrying nothing, and each must be supplied by the route - a parameter
    ``route_kwargs`` names a kwarg into, since the URL pattern is not known
    when ``as_view()`` runs - by a seed the declared ``pool_seeds`` registers,
    as ``server_owned_keys`` reads them, or by a value ``server_arguments``
    binds. It refuses ``refusal_redirect`` on a page with no ``success_url``
    that does not override ``get_refusal_url``, since a refused post naming
    no URL to return to would have nowhere to go.

    Attributes:
        spec: The operation served, declaring no Validator. Required.
        template_name: The page's template, as for any Django template view:
            ``django_spec_html/spec_confirm.html``, the one this package
            ships, by default.
        success_url: Where a successful post redirects, read by
            ``get_success_url``.
        route_kwargs: Which URL kwargs fill which parameters, as
            ``route_arguments`` reads it. The parameters it names a kwarg
            into are the ones ``as_view()`` counts the route as supplying.
        pool_seeds: The seeds the spec's callables are resolved with, unless
            ``get_pool_seeds`` answers others for a request.
        server_arguments: The kernel's ``ServerArguments`` the server answers
            for, checked at ``as_view()``.
        fragment_template_name: The template an htmx request is answered
            with; ``None`` addresses ``<template_name>#spec-form``, or a
            shipped page's own fragment file.
        display_target: Whether the context carries ``target_display``.
        target_display_limit: How many rows of a set ``target_display``
            projects.
        commit_button: The ``name`` of the submit button whose post commits;
            ``None``, the default, commits every post.
        redirect_field_name: The key a post or its query carries the URL to
            return to under, as on ``SpecFormView``: ``None``, the default,
            reads none.
        refusal_redirect: Whether a refused post that is not a denial
            redirects to ``get_refusal_url``, its messages sent through
            ``django.contrib.messages``, rather than rendering the page:
            ``False`` by default.
        success_message: The message a successful post sends, formatted by
            ``get_success_message`` with the target.
    """

    template_name = "django_spec_html/spec_confirm.html"
    refusal_redirect: bool = False

    @classmethod
    def _check_spec(cls, label: str, spec: ServiceSpec) -> None:
        """Refuse a spec with a Validator, pointing a ``FormValidator``'s at ``SpecFormView``.

        Nothing else to refuse of the spec's shape: the kernel refuses a
        ``partial`` or a ``many`` spec with no Validator when it is built.
        """
        validator = spec.validator
        if validator is None:
            return
        if isinstance(validator, FormValidator):
            raise ImproperlyConfigured(
                f"{label} serves a spec with no Validator, and this one validates with "
                f"{validator.form_class.__name__}: serve it with SpecFormView, whose page is "
                "that form."
            )
        raise ImproperlyConfigured(
            f"{label} serves a spec whose every argument the server answers, so it must declare "
            f"validator=None; got {type(validator).__name__}."
        )

    @classmethod
    def _check_page(cls, label: str, spec: ServiceSpec, initkwargs: Mapping[str, Any]) -> None:
        """That the call can be made without a post: every parameter it demands is supplied.

        Asked once the base has checked ``route_kwargs`` and
        ``server_arguments``, so a bare-string ``route_kwargs`` is still
        refused as one (test_a_bare_string_route_kwargs_is_refused_as_a_string)
        and a withheld name in the kernel's words
        (test_withholding_it_is_refused_by_the_kernel_s_own_check). There is
        no form, so neither ``commit_button`` nor ``redirect_field_name``
        clashes with a field. Then a ``refusal_redirect`` with nowhere to go.
        """
        _check_supplied(
            label,
            spec,
            initkwargs.get("route_kwargs", cls.route_kwargs),
            initkwargs.get("server_arguments", cls.server_arguments),
            initkwargs.get("pool_seeds", cls.pool_seeds),
        )
        # A refused post with no ``next`` returns to ``success_url``, so a page
        # redirecting its refusals needs one, or a ``get_refusal_url`` of its
        # own. One branch to coverage, so each condition is held by its own
        # test: test_a_page_not_redirecting_needs_none (the page redirects),
        # test_a_success_url_is_somewhere_to_go (it has no success_url) and
        # test_an_overridden_get_refusal_url_is_somewhere_to_go (nor its own
        # get_refusal_url), with test_get_refusal_url_may_be_passed_to_as_view
        # for the override passed to ``as_view``.
        if (
            initkwargs.get("refusal_redirect", cls.refusal_redirect)
            and initkwargs.get("success_url", cls.success_url) is None
            and initkwargs.get("get_refusal_url", cls.get_refusal_url)
            is SpecConfirmView.get_refusal_url
        ):
            raise ImproperlyConfigured(
                f"{label}.refusal_redirect needs a success_url, where a refused post returns "
                "without a next, or a get_refusal_url that returns one."
            )

    def _form(
        self, spec: ServiceSpec, principal: Any, target: Any, served: Served, **bound: Any
    ) -> None:
        """No form: the page renders the target and a button, and a post carries nothing."""

    def _arguments(
        self, form: None, spec: ServiceSpec, url_kwargs: Mapping[str, Any], served: Served
    ) -> Mapping[str, Any]:
        """The route's kwargs, as arguments coerced by their declarations, and nothing posted.

        ``route_arguments`` refuses a kwarg the spec does not declare, as on
        every page, and the base lays the binding over the result.
        """
        parameters = spec.parameters()
        route = route_arguments(parameters, url_kwargs, route_kwargs=served.route_kwargs)
        return coerce_flat(parameters, route)

    def _render_page(self, form: None, refreshed: bool, /, **context: Any) -> HttpResponse:
        """The page GET renders, and a post that does not commit, at 200: the base's context, and no form."""
        return self._rendered(self.get_context_data(**context), status=200)

    def _render_refused(
        self,
        form: None,
        refused: DispatchError | ServiceError,
        target: Any,
        /,
        *,
        status: int,
        requested: AdditionalInputRequired | None,
        **context: Any,
    ) -> HttpResponse:
        """The confirmation page again at ``status``, its context carrying the refusal as ``errors``.

        Every message of the refusal's tree, as a form page would place it -
        an argument refusal at its parameters, a service's string or list
        about the whole call - in the tree's order. With no field to put one
        on, a message at a parameter is prefixed with its path, as
        ``add_argument_errors`` prefixes a key no field declares
        (test_a_service_s_tree_is_flattened_with_each_path). A question's
        ``requested_input`` is the base's, its message and its schema: there
        is no field to answer it with.

        With ``refusal_redirect``, the page is not rendered: each of those
        messages is sent through ``messages.error``, carrying the refusal's
        ``code`` as ``extra_tags`` where it has one, and the post redirects
        to ``get_refusal_url(refused)``, or under htmx is sent there with
        ``HX-Redirect``
        (test_an_unmet_affordance_returns_to_next_with_its_reason,
        test_htmx_is_sent_back_with_hx_redirect). Where the messages
        middleware does not run, Django raises ``MessageFailure`` on the first
        message, before any is stored, and the page is rendered as without
        it, so the refusal is never lost to a redirect
        (test_without_the_messages_middleware_it_is_the_page_again). A denial
        never reaches here: it is raised before.
        """
        errors = [
            f"{'.'.join(where)}: {message}" if where else message
            for where, message in refusal_messages(refusal_tree(refused))
        ]
        if self.refusal_redirect:
            try:
                for error in errors:
                    messages.error(self.request, error, extra_tags=context.get("code", ""))
            except MessageFailure:
                ...
            else:
                url = self.get_refusal_url(refused)
                if self.wants_fragment(self.request):
                    return ClientRedirect(url)
                return HttpResponseRedirect(url)
        context["errors"] = errors
        return self._rendered(self.get_context_data(**context), status=status)

    def get_refusal_url(self, refused: DispatchError | ServiceError) -> str:
        """Where a refused post returns, with ``refusal_redirect`` on: the way back, or ``success_url``.

        ``get_redirect_url()``, the safe URL the request names under
        ``redirect_field_name``, and otherwise ``success_url``, resolved as
        ``redirect()`` reads it: a page posted from a list returns to the
        list, the refusal's messages waiting there. Override it to read
        ``refused``, such as to send an unavailable action's ``code`` to a
        page of its own.
        """
        return self.get_redirect_url() or resolve_url(self.success_url)

    def get_success_message(self, result: DispatchResult) -> str:
        """``success_message`` formatted with the target: ``"%(target)s was withdrawn."``.

        ``%`` with a mapping, as Django's ``SuccessMessageMixin`` formats it,
        whose one key is ``target``, the row or set the operation acted on,
        ``result.instance`` (test_it_is_formatted_with_the_target). Not the
        page base's ``result.data``: with no Validator it is ``{}``, so every
        placeholder would raise ``KeyError``. A set is the queryset, read when
        the message is formatted, so it holds the rows that match once the
        operation has run (test_a_set_is_read_as_the_operation_left_it).
        Override it to read the result, under ``result.value``.
        """
        return self.success_message % {"target": result.instance}


def _check_supplied(
    label: str,
    spec: ServiceSpec,
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None,
    server: ServerArguments | None,
    seeds: PoolSeeds,
) -> None:
    """Refuse a spec demanding a parameter of the caller that only a post could supply.

    No post to this page carries an argument, so every parameter the call
    demands must come from the server's side. Which ones it demands is the
    kernel's answer, never restated here: the parameters its shape check
    refuses to a call carrying nothing, through ``bind_arguments``, which
    relaxes what dispatch relaxes - a parameter that is optional or declares
    a default (test_a_read_the_shape_check_does_not_demand_needs_no_source), a
    key a typed ``kwargs`` provider fills or may decline
    (test_a_key_a_typed_provider_may_decline_is_not_demanded), and every read
    an untyped one reaches
    (test_an_untyped_provider_supplies_every_read_it_reaches). It is handed
    no seeds, so what they fill is taken off after, beside the other two
    sources:

    - **the seeds**, every name the kernel's ``server_owned_keys`` reads off
      the declared ``pool_seeds``: a ``reflected`` read a registered seed
      fills (test_a_registered_seed_supplies_a_reflected_read). A seed only
      ``get_pool_seeds`` adds per request is not seen, since nothing here
      has a request (test_a_seed_only_get_pool_seeds_adds_is_not_seen);
    - **the binding**, every name ``server_arguments`` binds a value to
      (test_a_bound_server_argument_supplies_it). One it withholds was
      refused by the kernel's ``check_server_arguments`` already;
    - **the route**, every parameter ``route_kwargs`` names a kwarg into: a
      tuple's names, a mapping's targets
      (test_the_route_supplies_what_route_kwargs_names). Under the strict
      rule, ``None``, the route is not known when the URLconf is imported,
      since ``as_view()`` is called before the pattern is built, so it
      supplies nothing until ``route_kwargs`` names what it fills
      (test_a_route_naming_no_kwarg_into_it_supplies_nothing).
    """
    try:
        bind_arguments(spec, {}, principal=None)
    except InvalidArguments as refused:
        demanded = frozenset(refused.detail)
    else:
        return
    supplied = (
        server_owned_keys(spec, pool_seeds=seeds)
        | frozenset({} if server is None else server.values)
        | route_targets(route_kwargs)
    )
    missing = sorted(demanded - supplied)
    if missing:
        raise ImproperlyConfigured(
            f"{label} requires {', '.join(missing)}, which no post to a confirmation carries: "
            "fill each from the route, naming it in route_kwargs, or from a pool seed, a "
            "kwargs provider or server_arguments."
        )
