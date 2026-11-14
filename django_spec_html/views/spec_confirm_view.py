from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from django.contrib import messages
from django.contrib.messages import MessageFailure
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import resolve_url
from django.template import TemplateDoesNotExist
from django.template.response import TemplateResponse
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
    flat_arguments,
    refusal_messages,
    server_owned_keys,
)

from django_spec_html.http.route_arguments import route_arguments
from django_spec_html.http.utils import route_targets
from django_spec_html.views.utils import (
    ClientRedirect,
    Served,
    SpecPageBase,
    refusal_code,
    refusal_tree,
    renders_no_page,
)


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
    request hooks, ``success_url``, ``redirect_field_name``,
    ``success_message`` and a file's answer with ``as_attachment``, each as
    ``SpecFormView`` documents it.

    **GET** renders ``template_name`` with that context, beside ``view`` and
    ``spec``, and **no** ``form``: there is nothing to fill in, so the
    template renders its own ``<form method="post">``, the CSRF token and
    the submit button. A key with nothing to hold is absent, as on every
    page, so ``{{ form }}`` is never ``None``.

    **The page's template is optional**, as on ``SpecFormView``:
    ``django_spec_html/spec_confirm.html`` by default, which this package
    ships, with its button in ``django_spec_html/spec_confirm_fragment.html``.
    It renders the target pane, an unmet affordance's reason with the button
    disabled, each action's own on a page serving ``actions``, a refused
    post's ``errors`` or a question's message alone, and the button,
    posting ``commit_button``'s name where one is set, and the URL to return
    to as a hidden input where the view reads one. Found,
    refused at ``as_view()`` and overridden as ``SpecFormView``'s is.

    **POST** dispatches with the route's kwargs, read by ``route_kwargs`` and
    coerced as a form page's are, under the binding and with the request's
    seeds. **Nothing the post carries becomes an argument** unless
    ``posted_arguments`` names it: not the CSRF token, not the button, not
    ``redirect_field_name``'s key and not any other key named like a
    parameter, so the closed argument set holds with nothing to check.

    **A bulk action reads its selection from the post.** A list's button
    posting the ticked rows' checkboxes beside it, through ``hx-include``,
    names their key in ``posted_arguments``: ``("ids",)`` for a spec taking
    ``ids``. Each name is read from ``request.POST`` as a query string is
    read, every value of an array parameter and the last of anything else,
    a comma list split as the kernel's ``flat_arguments`` splits it and a
    blank absent, then merged with the route's and coerced by its
    declaration. A value refused, by coercion or by the shape check, is a
    refused post like any other: the page at 400, or under
    ``refusal_redirect`` its message and a redirect. Never a 404, which
    ``route_refusal_404`` keeps for the route.

    **One page, several confirmations.** ``actions`` maps each button's name
    to the spec it runs, in place of ``spec``, as on ``SpecFormView``: an
    "Archive" and a "Delete" button on one page, every action a spec with no
    Validator and checked as ``spec`` is. A post naming no action or several
    is a 400, GET renders the first action's spec, and the success hooks read
    ``self.action_name``. **Each action's button answers its own
    availability**, in ``unavailable_actions`` and ``action_buttons`` as on
    ``SpecFormView``: the shipped template disables an unavailable action's
    button and shows its reason, and the others stay usable; ``unavailable``
    is set only when no action is. An action after the first the user may not
    run is disabled with no reason, its key in ``refused_actions``, as on
    ``SpecFormView``. ``as_view()`` refuses ``actions`` beside a
    ``commit_button``: an actions page commits only through an action's button,
    and an action keyed like a ``posted_arguments`` name or ``csrfmiddlewaretoken``,
    which a post carries whichever button it uses.

    A success redirects to ``get_redirect_url()`` where the request names a
    safe URL to return to, else to ``get_success_url(result)``, or
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
    nowhere to keep the messages, and a refusal with no message has nothing
    to send, so either is the page rendered as without ``refusal_redirect``.

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
    binds, or named in ``posted_arguments``. A name there must be a
    parameter of the spec, and not one ``route_kwargs`` fills from the URL.
    It refuses ``refusal_redirect`` on a page with no ``success_url``
    that does not override ``get_refusal_url``, since a refused post naming
    no URL to return to would have nowhere to go.

    Attributes:
        spec: The operation served, declaring no Validator. Required, unless
            ``actions`` is set.
        actions: The operations served, by the name of the button that runs
            each, every one declaring no Validator: a non-empty mapping, in
            place of ``spec``. ``None`` by default. Each one's affordances are
            asked on GET, for its own button.
        action_name: The key of the action a committing post runs, for the
            success hooks to read; ``None`` without one.
        template_name: The page's template, as for any Django template view:
            ``django_spec_html/spec_confirm.html``, the one this package
            ships, by default.
        success_url: Where a successful post redirects, read by
            ``get_success_url``.
        route_kwargs: Which URL kwargs fill which parameters, as
            ``route_arguments`` reads it. The parameters it names a kwarg
            into are the ones ``as_view()`` counts the route as supplying.
        route_refusal_404: Whether a route capture the shape check refuses
            raises ``Http404``, on GET and POST alike, as a URL converter that
            did not match would; ``False`` answers it as 0.12.0 did.
        posted_arguments: The parameters a post supplies, read from
            ``request.POST`` by name: ``()``, the default, reads none.
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
            ``None``, the default, commits every post. Refused beside
            ``actions``, which commit only through their own buttons.
        redirect_field_name: The key a post or its query carries the URL to
            return to under, as on ``SpecFormView``: ``None``, the default,
            reads none.
        refusal_redirect: Whether a refused post that is not a denial
            redirects to ``get_refusal_url``, its messages sent through
            ``django.contrib.messages``, rather than rendering the page:
            ``False`` by default.
        success_message: The message a successful post sends, formatted by
            ``get_success_message`` with the target.
        success_trigger: The event a success answered in place triggers, read
            by ``get_success_trigger``: an event's name, a mapping of event
            names to payloads, or ``None`` for none; ``"spec-succeeded"`` by
            default, checked at ``as_view()``.
        success_trigger_timing: The header the trigger travels in: ``"receive"``,
            the default, ``"swap"`` or ``"settle"``, for ``HX-Trigger``,
            ``HX-Trigger-After-Swap`` and ``HX-Trigger-After-Settle``.
        success_refresh: Whether a success answered in place is ``HX-Refresh``
            and an empty body in place of the result fragment: ``False`` by
            default, checked at ``as_view()``.
        as_attachment: Whether a spec presenting a file answers its success
            as a download, the default, or ``inline``, for a browser to show
            it in place.
    """

    template_name = "django_spec_html/spec_confirm.html"
    refusal_redirect: bool = False
    posted_arguments: tuple[str, ...] = ()
    _takes_actions = True

    @classmethod
    def _check_spec(cls, label: str, spec: ServiceSpec) -> None:
        """Refuse a spec with a Validator, pointing a ``FormValidator``'s at ``SpecFormView``."""
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
        """That every parameter the call demands is supplied, a posted one among the spec's."""
        route_kwargs = initkwargs.get("route_kwargs", cls.route_kwargs)
        posted = initkwargs.get("posted_arguments", cls.posted_arguments)
        _check_posted(label, spec, posted, route_kwargs)
        _check_action_keys(cls.__name__, initkwargs.get("actions", cls.actions), posted)
        _check_supplied(
            label,
            spec,
            route_kwargs,
            initkwargs.get("server_arguments", cls.server_arguments),
            initkwargs.get("pool_seeds", cls.pool_seeds),
            frozenset(posted),
        )
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
        """The route's kwargs and the posted ``posted_arguments``, coerced by their declarations."""
        parameters = spec.parameters(call_context=served.call_context)
        route = route_arguments(parameters, url_kwargs, route_kwargs=served.route_kwargs)
        read = flat_arguments(parameters, self.request.POST)
        posted = {name: read[name] for name in self.posted_arguments if name in read}
        return coerce_flat(parameters, {**posted, **route})

    def _render_page(self, form: None, refreshed: bool, /, **context: Any) -> HttpResponse:
        """The page GET renders, and an uncommitted post, at 200: the base's context, no form."""
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
        """The confirmation page again at ``status``, empty when a redirect-only page has none."""
        context["errors"] = _errors(refused)
        response = self._rendered(self.get_context_data(**context), status=status)
        if not renders_no_page(self.http_method_names, self.refusal_redirect):
            return response
        try:
            return cast(TemplateResponse, response).render()
        except TemplateDoesNotExist:
            return HttpResponse(status=response.status_code)

    def _refused_elsewhere(self, refused: DispatchError | ServiceError) -> HttpResponse | None:
        """The refusal's messages sent and the post redirected, or ``None`` without a redirect."""
        sent = [error for error in _errors(refused) if error]
        if not (self.refusal_redirect and sent):
            return None
        code = refusal_code(refused).get("code", "")
        try:
            for error in sent:
                messages.error(self.request, error, extra_tags=code)
        except MessageFailure:
            return None
        url = self.get_refusal_url(refused)
        if self.wants_fragment(self.request):
            return ClientRedirect(url)
        return HttpResponseRedirect(url)

    def get_refusal_url(self, refused: DispatchError | ServiceError) -> str:
        """Where a refused post returns, with ``refusal_redirect`` on: back, or ``success_url``.

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


def _errors(refused: DispatchError | ServiceError) -> list[str]:
    """Every message of the refusal's tree, in order, one at a parameter prefixed with its path."""
    return [
        f"{'.'.join(where)}: {message}" if where else message
        for where, message in refusal_messages(refusal_tree(refused))
    ]


def _check_supplied(
    label: str,
    spec: ServiceSpec,
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None,
    server: ServerArguments | None,
    seeds: PoolSeeds,
    posted: frozenset[str],
) -> None:
    """Refuse a spec demanding a parameter of the caller that no post or source supplies."""
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
        | posted
    )
    missing = sorted(demanded - supplied)
    if missing:
        raise ImproperlyConfigured(
            f"{label} requires {', '.join(missing)}, which no post to a confirmation carries: "
            "fill each from the route, naming it in route_kwargs, or from a pool seed, a "
            "kwargs provider or server_arguments, or name it in posted_arguments."
        )


def _check_action_keys(
    label: str, actions: Mapping[str, ServiceSpec] | None, posted: tuple[str, ...]
) -> None:
    """Refuse an action named like a posted argument or the CSRF token: every post carries it."""
    for key in actions or ():
        if key in posted or key == "csrfmiddlewaretoken":
            raise ImproperlyConfigured(
                f"{label}.actions names {key!r}, which every post can carry as "
                "posted_arguments or the CSRF token: it cannot say which action runs."
            )


def _check_posted(
    label: str,
    spec: ServiceSpec,
    posted: Any,
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None,
) -> None:
    """Refuse a ``posted_arguments`` not the spec's parameter names, or naming a route's."""
    if not isinstance(posted, tuple) or not all(isinstance(name, str) for name in posted):
        raise ImproperlyConfigured(
            f"{label}.posted_arguments must be a tuple of parameter names; got {posted!r}."
        )
    unknown = sorted(frozenset(posted) - frozenset(spec.parameters().names()))
    if unknown:
        raise ImproperlyConfigured(
            f"{label}.posted_arguments names {', '.join(unknown)}, which the spec does not declare."
        )
    routed = sorted(frozenset(posted) & route_targets(route_kwargs))
    if routed:
        raise ImproperlyConfigured(
            f"{label}.posted_arguments names {', '.join(routed)}, which route_kwargs fills "
            "from the URL: a parameter is the route's or the post's, never both."
        )
