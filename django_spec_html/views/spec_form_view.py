"""``SpecFormView`` - a spec validated by a Django form, served as the page that form is."""

from __future__ import annotations

import copy
import dataclasses
import datetime
import math
from collections.abc import Callable, Collection, Mapping
from contextlib import suppress
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Final, cast
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
from django.forms.models import model_to_dict
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
    FormValidator,
    Grant,
    InvalidArguments,
    NotPermitted,
    Parameters,
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
    clean_held_to_their_columns,
    coerce_flat,
    dispatch,
    enforce_affordances,
    form_keywords,
    project_payload,
    resolve_target,
    unmet_operation_affordance,
)

from django_spec_html.http.add_argument_errors import add_argument_errors
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

_TYPED: Final = (forms.IntegerField, forms.DateField, forms.DateTimeField)
"""The fields whose input formats are wider than the wire form the shape check reads.

``DecimalField`` and ``FloatField`` subclass ``IntegerField``, so the first
row covers all three numbers. Each row is held by its own test:
test_an_integer_the_form_reads_is_not_refused,
test_dates_in_the_forms_input_formats_are_read_as_the_form_reads_them (both
date rows) and test_localized_numbers_are_read_as_the_form_reads_them."""


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


class SpecFormView(TemplateResponseMixin, ContextMixin, View):
    """A ``ServiceSpec`` validated by a form, served as the page a person fills that form in on.

    The spec's Validator must be a
    [`FormValidator`][django_service_specs.adapters.forms.form_validator.FormValidator],
    and its form class is the page's form:

        urlpatterns = [
            path(
                "books/new/",
                SpecFormView.as_view(
                    spec=add_book_spec,
                    template_name="books/book_form.html",
                    success_url=reverse_lazy("books"),
                ),
            ),
        ]

    **GET** renders ``template_name`` with an unbound form under ``form``,
    beside ``view`` and ``spec``: the names Django's ``FormView`` uses, with
    ``extra_context`` merged in as Django's ``ContextMixin`` does. The spec's
    class-level permission check runs first, through
    [`authorize`][django_service_specs.dispatch.authorize.authorize], so
    a page is never offered to a principal the post would refuse.

    **An update's page shows the row it updates.** Every GET then finds its
    target as dispatch would, through
    [`resolve_target`][django_service_specs.dispatch.resolve_target.resolve_target]
    on the route's kwargs, coerced as a post's are: the row or set a target
    selector resolves, or ``None`` for a create. A check refusing it - the
    object-level check on a row, the scope check on a set or on ``None`` -
    raises ``PermissionDenied``, as the post would be refused; a missing row
    or a selector's ``ServiceNotFound`` raises ``Http404``; and a route value
    the spec's parameters refuse raises Django's ``BadRequest``. The form
    shows a row's values for its own fields: a ``ModelForm`` of the row's
    model is built on a copy of it, as the Validator binds one, and any other
    form takes the row's values for the enabled fields it names as its
    ``initial``. So a file the row holds is shown, with the clear checkbox an
    optional file's ``ClearableFileInput`` offers beside it. A create, a
    collection target and a missing row ``allow_none`` lets through show an
    empty form.

    **POST starts as GET does**: the route first, then that check, and only
    then is the form built from the post. A refused post is answered with the
    page again, and dispatch's shape check comes before its own permission
    check, so without it a principal the spec refuses would be shown the
    page - every row a choice field lists, and ``extra_context`` - by posting
    a malformed form, and a form whose constructor reads rows would read them
    for that principal. The
    [`Grant`][django_service_specs.dispatch.grant.Grant] it returns goes
    to dispatch, so the class-level check runs once.

    **Then the post finds its target, as GET does**, before the form is
    built or read, so the form a refused post re-renders is built on the row
    as the page's was, its held file and clear checkbox shown. The target
    answers before the shape check: the object-level and scope checks and a
    missing row refuse a malformed post as they would a well-formed one,
    rather than dispatch re-rendering the page for a principal the row
    refuses. Dispatch is handed the target and the grant ``resolve_target``
    returns, so it looks nothing up again and asks no check twice. A route
    value the target's reads refuse is left to dispatch, whose one refusal
    carries it beside the form's own.

    **POST** binds the form to the post and reads the arguments through its
    own widgets - a checkbox is ``True`` or ``False``, a multi-select a list -
    because that is how Django reads a form, and a flat reading refuses a
    checkbox's ``"on"``. Only the form's fields are read, so the CSRF token
    and a named submit button never become arguments, and a disabled field is
    not read at all, as the form ignores what is posted for it. A field left
    blank is absent, as it is to the form. A number, date or date-time the
    field reads in one of its input formats - ``10/25/2006``, a localized
    ``12,50`` - is sent on in the form the shape check reads, through the
    field's own ``to_python``, so the shape check never refuses what the form
    accepts. The URL kwargs are merged last, so the route wins a clash, as in
    [`request_arguments`][django_spec_html.http.request_arguments.request_arguments].
    Each is an argument, so the spec declares each one: a kwarg it does not is
    the host's misconfiguration, raised as ``ImproperlyConfigured`` under
    either ``unknown_arguments`` policy rather than answered as a refused post.
    Then [`dispatch`][django_service_specs.dispatch.dispatch.dispatch]:

    - **A success** redirects to ``get_success_url(result)``: ``success_url``
      through Django's ``resolve_url`` by default, so a ``reverse_lazy`` or a
      URL name works, and a subclass overrides it to read the result.
    - **A refusal of the arguments** - ``InvalidArguments``, or a
      ``ServiceValidationError`` - re-renders the bound form at 400 with the
      refusal placed on it by
      [`add_argument_errors`][django_spec_html.http.add_argument_errors.add_argument_errors].
      A service's string or list detail is about the whole form.
    - **Any other refusal** re-renders it with the message as a non-field
      error: ``ServiceConflict`` at 409, any other ``ServiceError`` at 422,
      ``UnsupportedMediaType`` at 415, any other ``DispatchError`` at 400. The
      statuses are ``error_response``'s, read from the one map, so
      a client reading a refused post - Turbo, which will not render a failed
      post answered 200 - reads it the same way.
    - **A question**, ``AdditionalInputRequired``, re-renders it at 422, the
      message as a non-field error, as the confirmation page below.
    - ``NotPermitted`` and ``PrincipalUnavailable`` raise Django's
      ``PermissionDenied``, and a not-found result or ``ServiceNotFound``
      raises ``Http404``, for the host's own 403 and 404 pages.

    **The confirmation page is an** ``AdditionalInputRequired`` **rendered.**
    Its ``schema`` is checked first by the kernel's
    [`check_requested_input`][django_service_specs.dispatch.check_requested_input.check_requested_input],
    with the call's seeds, so a question no route could hear answered - a key
    no parameter declares, one a seed fills, a shape a transport cannot
    render, a type the parameter refuses - raises ``ImproperlyConfigured`` on
    the page and in the fragment alike, and is never rendered. Then the bound
    form is re-rendered with every value posted, and the context carries
    ``requested_input``: the ``message``, the ``schema``, and the ``fields``,
    the form's bound fields the schema names. The page builds no widget from
    a schema. The answer is a field the spec's form declares, an optional
    ``BooleanField`` whose absent box cleans to ``False``, and
    ``asked_fields`` holds it back until a question names it: it is taken out
    of the form each GET and every other refusal renders, so it posts nothing
    of its own, and shown empty when asked, so a yes is never carried over to
    a question asked again. A key no field declares is asked by the message
    alone. The target is found and a set counted again for the post that
    asked, so the page says what it found then.

    **Two wrappers, one outcome.** The view decides what a request ends in
    once - a success, or the refusal's ``Outcome`` - and only then reads
    who asked, to choose how to answer it. Every request gets the page above
    unless ``wants_fragment`` says htmx asked for less, and then the same
    outcome is answered as a fragment, at the same URL:

    - **The form** renders ``get_fragment_template_names()`` where the page
      renders ``template_name``, from the same context: on GET, and on every
      refusal the page re-renders, at 200 rather than the page's 4xx, since
      htmx swaps no 4xx by default. The default address is
      ``<template_name>#spec-form``, a ``{% partialdef %}`` inside the page's
      own template, which Django 6.0 resolves; below it,
      django-template-partials does, with its tag named in ``TEMPLATES`` as
      a builtin (``"builtins": ["template_partials.templatetags.partials"]``)
      and its loader around the project's. A template with no fragment at the
      address is Django's ``TemplateDoesNotExist`` naming it, never the page,
      which swapped into a form's target would nest a second layout inside it.
    - **A success** is ``fragment_success(result)``: ``HX-Redirect`` to
      ``get_success_url(result)``, or the ``spec-result`` fragment in place.
    - **A denial** is raised in both modes alike, for the host's handlers.

    Both wrappers set ``Vary`` on the three headers the choice reads. No
    htmx header reaches dispatch: the arguments are the form's fields and the
    route's kwargs whichever wrapper answers, so a spec cannot tell the two
    apart. One header is read before dispatch, ``HX-Trigger-Name``, and only
    to stop a post committing, below.

    **A post that does not commit.** A form that refreshes a dependent
    choice, adds a row or searches posts without meaning to run the
    operation. ``commit_button`` names the submit button whose post commits;
    ``None``, the default, commits every post. Once set, ``commits(request)``
    decides: a post whose data does not carry that name, or an htmx request
    whose ``HX-Trigger-Name`` names another control the post carries, is the
    page GET renders, admitted and with its target, its affordances and
    ``asked_fields`` held back as GET's are, but with the form bound to the
    post and built through ``form_kwargs`` as every form is, so a choice its
    constructor narrows by a posted value is narrowed again. Nothing is
    dispatched and nothing validated: the form shows no error, and the
    context carries ``refreshed``, absent from every other render. It
    answers 200 on the page, and the form's fragment at 200 under htmx.

    **A success message.** ``success_message``, where it is set, is sent
    through ``django.contrib.messages`` on every success - the redirect, the
    ``HX-Redirect`` and the result fragment alike - formatted by
    ``get_success_message(result)``, ``%`` with the form's cleaned data as
    Django's ``SuccessMessageMixin`` formats it. Without the messages
    middleware it is skipped, and never raises ``MessageFailure``. A refusal,
    a confirmation page and a post that does not commit send none.

    **The request is read through the same attributes and hooks as**
    ``SpecView``**'s**, checked by the same code in ``http/utils.py``:
    ``route_kwargs`` says which URL kwargs fill which parameters;
    ``get_pool_seeds(request)``, asked once per request, answers the seeds
    the class-level check, the target's lookup and its object-level or scope
    check, the form's ``form_kwargs``, the affordances and dispatch are
    handed, each resolving only the seeds it names; and
    ``get_server_arguments(request)`` answers the binding, laid over the
    route's arguments before the target is found and over the post's before
    dispatch. A field of the page's form the binding binds renders disabled
    with the server's value, and one it withholds disabled and empty, so the
    post never reads it. **A named seed is resolved again at each step that
    names it**, since a ``Grant`` carries no values: a resolver answering a
    fresh object answers each step a different one.

    **The page's form is the Validator's**, built with what its
    ``form_kwargs`` answers, through the kernel's ``form_keywords``: on GET,
    on a refused post and for its own validation below, with the principal,
    the request's seeds, the row the post acts on and the request's one call
    context, the very dict dispatch is then handed. So a form whose
    ``__init__`` takes a keyword - a queryset scoped by a tenant - is served,
    and the page's form offers the choices the Validator's accepts.

    **One call context per request**: a plain dict the page creates for the
    request and hands, as the same object, to every kernel call it makes - the
    class-level check, the target's lookup and its checks, the affordances,
    the form's keywords and dispatch - so a check, a condition or the service
    declaring ``call_context`` reads what an earlier stage put there.

    **GET asks the spec's affordances** once the target is found: the
    callable conditions always, through the kernel's
    ``unmet_operation_affordance`` on a pool built from the call's seeds,
    and, with none of those unmet and a row to ask about, the conditions on
    the row, as dispatch asks them. The first unmet ``Affordance`` is in the
    context as ``unavailable``, for a template to read its ``code`` and
    ``reason``; it is absent when every condition is met. The POST is
    unchanged, and dispatch's 409 stays the authority. **A refused post's
    context carries** ``code``, the ``ActionUnavailable``'s, for a refusal
    carrying one, as ``error_response`` puts it in the JSON body; it is
    absent for every other.

    **The page shows its target**, on GET and on every refused post it
    re-renders, in the context beside the form: ``target`` is the row an
    instance selector found, or the set a ``collection_selector_spec``
    resolved, and it is absent for a create and for a missing row
    ``allow_none`` lets through. A set comes with ``target_count``, how many
    rows it held, and ``target_counted_at``, when it was counted
    (``django.utils.timezone.now()``), so a template says "117 rows matched at
    14:02" rather than promising a number the confirming post may not find. An
    empty set is no refusal: its scope check is asked, and the page counts 0.
    A queryset is counted by a ``COUNT`` on a fresh clone, never by reading its
    rows; the post re-runs the service on the set as it is then, and a refused
    one is counted again. A successful post counts nothing.

    **``target_display`` is the target as a person reads it**, on a page
    that sets ``display_target``. It is off by default: an Output describes
    what the operation returns, which need not be its target, so a page
    presents its target only where it says the Output describes it. On, the
    target is presented through the spec's presenter - its own, or its output
    selector's - with the context dispatch hands it, projected by the kernel's
    ``project_payload``, and with every ``HANDLE`` dropped as well as every
    ``HIDDEN`` one. A row's is a dict of its ``fields``, with ``label``, the
    value of the field marked ``LABEL``, beside them where there is one; a
    set's is a list of those, one per row, for at most
    ``target_display_limit`` rows, read in one query. ``as_view()`` refuses a
    page displaying its target whose spec has no Output, whose Output marks
    none of its own fields with a ``FieldMarking``, or whose markings the
    kernel's projection refuses, such as two labels. Whether the Output
    describes the target's rows is known only once one is presented: an
    Output that does not raises on the page's first request, which is the
    cost of turning the display on.

    **A refusal of the arguments shows every error in one post.** Beside an
    ``InvalidArguments``, the page's own form - built as the Validator's is,
    with the same keywords - is validated too, and the refusal is placed
    beside what it found, never twice. So after a shape-check refusal - a
    required field left blank, a date that does not parse - the form's further
    checks, a length, ``clean()`` or uniqueness, answer in the same post
    rather than the next. It is validated as the Validator validates its own,
    under the kernel's ``clean_held_to_their_columns``, so a value no column
    holds - a key past 64 bits - is refused in the field's words and never
    reaches the database driver. A post whose route the target's reads
    refuse never found its row, so its form is not judged as one and carries
    the refusal alone. Any other refusal is no verdict on the
    arguments - a conflict, a service's own rule, an unavailable action - and
    the page's form carries it alone, validated no further.

    **A partial spec's page** renders no HTML ``required`` attribute, so the
    browser sends a post that leaves fields blank, and a blank field is absent:
    on a partial call, one the operation leaves as it is. A post with every
    field blank dispatches with no arguments but what a checkbox or a
    multi-select always sends, and is not refused. Beside a shape-check refusal, a
    partial page's form carries the refusal alone: its Validator takes the
    fields a post leaves out off its own form, and the page's form, validated
    whole, would call each one required.

    ``as_view()`` refuses, when the URLconf is imported, a view with no
    ``ServiceSpec``, a spec whose Validator is not a ``FormValidator``, a
    ``many`` spec, whose list of rows no form posts, a view with no
    ``success_url`` that does not override ``get_success_url``, and a
    ``commit_button`` that is not a button's name or is one of the form's
    fields.

    **Sync only**, in this release. **CSRF is the host's middleware**, as for
    any Django view, and the template renders ``{% csrf_token %}``.

    Attributes:
        spec: The operation served. Required.
        template_name: The page's template, as for any Django template view.
        success_url: Where a successful post redirects, read by
            ``get_success_url``: a path, a ``reverse_lazy``, or a URL name.
        pool_seeds: The seeds the spec's callables are resolved with, unless
            ``get_pool_seeds`` answers others for a request.
        unknown_arguments: What becomes of an argument no parameter declares.
        route_kwargs: Which URL kwargs fill which parameters: ``None`` for
            the strict rule, a mapping from a captured name to the parameter
            it fills or to ``None`` to drop it, or a tuple of names to allow,
            as ``route_arguments`` reads it.
        server_arguments: The kernel's ``ServerArguments`` the server answers
            for, checked at ``as_view()``. Its values may vary per request,
            through ``get_server_arguments``, and its names may not.
        fragment_template_name: The template an htmx request's form is
            answered with, for a fragment in another file or under another
            name; ``None``, the default, addresses ``<template_name>#spec-form``.
        display_target: Whether the context carries ``target_display``,
            the target presented through the spec's Output and projected by
            its markings: ``False`` by default, checked at ``as_view()``.
        target_display_limit: How many rows of a set ``target_display``
            projects, the first that many in the set's own order: a positive
            int, 10 by default, checked at ``as_view()``. ``target_count``
            counts them all.
        asked_fields: The fields of the form held back until a post's
            ``AdditionalInputRequired`` asks for them by name: a tuple of
            optional, enabled fields the server does not answer for, checked at
            ``as_view()``. Empty by default, so every field is shown.
        commit_button: The ``name`` of the submit button whose post commits,
            read by ``commits``: ``None``, the default, commits every post;
            a non-empty string no field of the form declares, checked at
            ``as_view()``.
        success_message: The message a successful post sends through
            ``django.contrib.messages``, formatted by
            ``get_success_message`` with the form's cleaned data. Empty by
            default, which sends none.
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
    asked_fields: tuple[str, ...] = ()
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
        its target, asks the affordances and holds ``asked_fields`` back as
        GET does, and renders what GET renders with the post's values in it
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
        form = _page_form(spec, principal, target, served, **bound)
        if bound:
            # Before ``_hold_back``, which reads the form's errors and would
            # otherwise validate it.
            _without_errors(form)
            context["refreshed"] = True
        _hold_back(form, self.asked_fields, ())
        context.update(self._target_context(spec, principal, target, served, pool))
        return self._rendered(self.get_context_data(form=form, **context), status=200)

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
        form = _page_form(spec, principal, target, served, data=request.POST, files=request.FILES)
        try:
            read = _form_arguments(form, spec.parameters(), kwargs, served)
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
            # One branch to coverage, so each condition is held by its own
            # test: test_a_refusal_after_the_validator_took_the_form_validates_it_no_further
            # (an argument refusal: any other is no verdict on the post's
            # arguments, and a service's comes once the Validator's form, built
            # as the page's is, has taken them) and
            # test_a_shape_refusal_shows_no_word_about_a_field_left_blank (a
            # whole call: a partial one's Validator takes the fields a post
            # leaves blank off its form, and the page's form validated whole
            # would call each one required), and
            # test_an_update_page_s_own_unchanged_value_is_never_taken[a-refused-route]
            # (a found target: with the route refused, the page's form is built
            # as no row, and validated it would judge the post as a create,
            # the row's own unique value taken by another).
            merged = (
                isinstance(refused, InvalidArguments) and not spec.partial and target is not UNSET
            )
            # The target is shown again, and a set counted again, here and
            # never on a success: a post that redirects renders nothing.
            return self._render_refused(
                form,
                _refusal_tree(refused),
                status=refusal_status(refused),
                merged=merged,
                requested=requested,
                **_refusal_code(refused),
                **self._target_context(spec, principal, target, served, None),
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

    def _served(self, request: HttpRequest) -> _Served:
        """What both hooks answer for ``request``, checked as ``SpecView`` checks them, once.

        The per-call binding must bind and withhold the declared names, and a
        seed added per call may not take one of them; the checks are the
        JSON views', in ``http/utils.py``.
        """
        label = type(self).__name__
        server = checked_binding(label, self.get_server_arguments(request), self.server_arguments)
        seeds = checked_seeds(label, self.get_pool_seeds(request), self.pool_seeds, server)
        return _Served(route_kwargs=self.route_kwargs, server=server, seeds=seeds, call_context={})

    def _target_context(
        self,
        spec: ServiceSpec,
        principal: Any,
        target: Any,
        served: _Served,
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

    def _render_refused(
        self,
        form: forms.BaseForm,
        detail: Mapping[Any, Any],
        *,
        status: int,
        merged: bool,
        requested: AdditionalInputRequired | None,
        **context: Any,
    ) -> HttpResponse:
        """The page again, the bound form carrying ``detail``, and its own errors where ``merged``.

        Merged, the page's form is validated first, as the Validator
        validates its own, under the kernel's ``clean_held_to_their_columns``,
        and the refusal is placed beside what it found, never twice. So after
        a shape-check refusal the form's own checks - a length, ``clean()``,
        uniqueness - answer in the same post
        (test_a_blank_required_field_and_a_clean_violation_show_in_one_post).
        The guards are what validating it needs: without them it would hand a
        number no integer column holds to the database driver, and SQLite's
        raises ``OverflowError``
        (test_a_key_past_its_column_beside_a_shape_refusal_never_reaches_the_driver).

        Otherwise the form carries ``detail`` alone: its error state is set to
        none directly, which is what Django's ``add_error`` needs, and the
        refusal is placed on it.

        Then ``asked_fields`` are held back, but for those ``requested`` asks
        for, as ``_hold_back`` reads them, and a question is in the context as
        ``requested_input``: its message, its schema, and the form's bound
        fields it names, in the schema's order. A key no field of the form
        declares is asked by the message alone, since the page builds no
        widget from a schema
        (test_a_key_no_field_declares_is_asked_by_its_message_alone).
        """
        if merged:
            clean_held_to_their_columns(form)
        else:
            _without_errors(form)
        add_argument_errors(form, detail)
        schema: Mapping[str, Any] = {}
        # One branch to coverage, so each condition is held by its own test:
        # test_any_other_refusal_holds_it_back (no question was asked) and
        # test_a_question_with_no_schema_reveals_nothing (one that names no key).
        if requested is not None and requested.schema is not None:
            schema = requested.schema
        _hold_back(form, self.asked_fields, schema)
        if requested is not None:
            context["requested_input"] = {
                "message": requested.message,
                "schema": requested.schema,
                "fields": [form[name] for name in schema if name in form.fields],
            }
        return self._rendered(self.get_context_data(form=form, **context), status=status)

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


@dataclasses.dataclass(frozen=True)
class _Served:
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


def _check_configuration(cls: type[SpecFormView], initkwargs: Mapping[str, Any]) -> None:
    """Refuse a view that could not serve its first request, naming what is missing."""
    label = cls.__name__
    spec = initkwargs.get("spec", cls.spec)
    if not isinstance(spec, ServiceSpec):
        raise ImproperlyConfigured(
            f"{label}.as_view() needs a ServiceSpec, the write a form posts; "
            f"got {type(spec).__name__}."
        )
    if not isinstance(spec.validator, FormValidator):
        raise ImproperlyConfigured(
            f"{label} renders the form its spec validates with, so the spec's validator "
            f"must be a FormValidator; got {type(spec.validator).__name__}."
        )
    if spec.many:
        raise ImproperlyConfigured(
            f"{label} posts one row, the form's, and a many spec takes a list of rows "
            "under its many_argument: serve it from a view that sends the list."
        )
    redirects = initkwargs.get("get_success_url", cls.get_success_url)
    # One branch to coverage, so each condition is held by its own test:
    # test_a_valid_post_redirects_to_the_success_url (the first: a view with a
    # success_url is built) and
    # test_a_subclass_overriding_get_success_url_needs_no_success_url (the
    # second), with test_get_success_url_may_be_passed_to_as_view for the
    # override passed to ``as_view`` rather than written on a subclass.
    if (
        initkwargs.get("success_url", cls.success_url) is None
        and redirects is SpecFormView.get_success_url
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
    _check_asked_fields(
        label,
        spec,
        initkwargs.get("asked_fields", cls.asked_fields),
        initkwargs.get("server_arguments", cls.server_arguments),
    )
    _check_commit_button(label, spec, initkwargs.get("commit_button", cls.commit_button))


def _check_commit_button(label: str, spec: ServiceSpec, button: Any) -> None:
    """Refuse a ``commit_button`` that is not a button's name, or is the name of one of the form's fields.

    ``None`` commits every post. Otherwise a non-empty string, the ``name``
    a submit button posts: one branch to coverage, held by the ``a-tuple``
    and ``empty`` cases of test_anything_but_a_button_s_name_is_refused. A
    field's name is refused (test_a_field_s_name_is_refused), since a
    field posts its value on every submit, and the rule could never tell a
    post that commits from one that does not.
    """
    if button is None:
        return
    if not isinstance(button, str) or not button:
        raise ImproperlyConfigured(
            f"{label}.commit_button must be None or the name of the submit button whose post "
            f"commits; got {button!r}."
        )
    form_class = _form_class(spec)
    # ``base_fields`` is set by the metaclass, which the stubs do not declare.
    fields: dict[str, forms.Field] = form_class.base_fields  # ty: ignore[unresolved-attribute]
    if button in fields:
        raise ImproperlyConfigured(
            f"{label}.commit_button names {button!r}, a field {form_class.__name__} declares: a "
            "field is posted by every submit, so it cannot say which one commits."
        )


def _check_asked_fields(
    label: str, spec: ServiceSpec, asked: Any, server: ServerArguments | None
) -> None:
    """Refuse ``asked_fields`` naming anything but optional fields of the form that the client answers.

    A tuple, as ``route_kwargs``' names are, so a bare string is not read as
    its letters (test_anything_but_the_form_s_field_names_is_refused). Each
    name is a field the form class declares; one it does not disable, since
    a disabled field is never read from a post (test_a_disabled_field_is_refused);
    one that is optional, since a
    field held back is absent from every post no question asked for, and a
    required one would refuse them all (test_a_required_field_is_refused);
    and one the server does not answer for, whose field the page disables, so
    no person could answer it (test_a_field_the_server_answers_for_is_refused).
    """
    if not isinstance(asked, tuple):
        raise ImproperlyConfigured(
            f"{label}.asked_fields must be a tuple of the form's field names; got {asked!r}."
        )
    form_class = _form_class(spec)
    # ``base_fields`` is set by the metaclass, which the stubs do not declare.
    fields: dict[str, forms.Field] = form_class.base_fields  # ty: ignore[unresolved-attribute]
    owned = server_names(server)
    for name in asked:
        if name not in fields:
            raise ImproperlyConfigured(
                f"{label}.asked_fields names {name!r}, which {form_class.__name__} does not "
                "declare."
            )
        if fields[name].disabled:
            raise ImproperlyConfigured(
                f"{label}.asked_fields names {name!r}, which {form_class.__name__} disables: "
                "nobody posting the form could answer it."
            )
        if fields[name].required:
            raise ImproperlyConfigured(
                f"{label}.asked_fields names {name!r}, which {form_class.__name__} requires: a "
                "field held back until a question asks for it is absent from every other post, "
                "so it must be optional."
            )
        if name in owned:
            raise ImproperlyConfigured(
                f"{label}.asked_fields names {name!r}, which the server answers for: nobody "
                "posting the form could answer it."
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


def _hold_back(form: forms.BaseForm, held: tuple[str, ...], asked: Collection[str]) -> None:
    """Take the ``held`` fields ``asked`` does not name out of the form a page renders.

    Out of this form's own ``fields``, which Django copies per instance, so
    the class and every other request's form keep them, and at render only:
    the post has read its arguments by now, through every field, so a
    confirming post's answer is read like any other value
    (test_the_confirming_post_runs_with_every_value). A field taken out
    renders nothing, with ``{{ form }}`` or field by field, so it posts no
    value of its own and a box never shown answers ``False``
    (test_the_first_post_answers_no). A held field a refusal's error names is
    left in, or the error would sit on a field nobody can see
    (test_a_held_field_a_refusal_names_is_shown_with_it).

    A held field a question asks for is shown without what was posted for it,
    every other field keeping its value: asked again after a yes, since the
    set it counted moved, say, the box is shown empty, and one click never
    answers a question nobody read (test_a_re_ask_is_never_ticked_from_the_post).
    """
    for name in held:
        if name in asked:
            # A copy, since a request's ``POST`` is immutable, and the form
            # reads its data afresh each time a field renders. ``Any``, as
            # the stubs declare the data a ``Mapping``, and the page binds a
            # ``QueryDict``, whose copy keeps every value of a multi-select.
            posted: Any = form.data
            data = posted.copy()
            data.pop(form.add_prefix(name), None)
            form.data = data
        elif name not in form.errors:
            # ``None`` beside it, for a field a form's ``__init__`` removed.
            form.fields.pop(name, None)


def _without_errors(form: forms.BaseForm) -> None:
    """Set a bound form's error state to none, so rendering it validates nothing.

    Django's own two attributes, as ``full_clean()`` starts them, with the
    form's renderer as Django 5.0 and later pass it; ``Any``, as the stubs
    declare neither as assignable. A refused post's form then carries the
    refusal alone, and a post that does not commit shows no error at all.
    """
    bound: Any = form
    bound._errors = ErrorDict(renderer=form.renderer)
    bound.cleaned_data = {}


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


def _form_class(spec: ServiceSpec) -> type[forms.BaseForm]:
    """The form the spec validates with, which ``as_view`` checked is there."""
    return cast("FormValidator", spec.validator).form_class


def _admitted(
    spec: ServiceSpec, request: HttpRequest, url_kwargs: Mapping[str, Any], served: _Served
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
    served: _Served,
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
    spec: ServiceSpec, target: Any, served: _Served, pool: dict[str, Any]
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


def _refusal_tree(refused: DispatchError | ServiceError) -> Mapping[Any, Any]:
    """The refusal a re-rendered form carries: an argument refusal in place, any other as its message."""
    if isinstance(refused, InvalidArguments):
        return refused.detail
    if isinstance(refused, ServiceValidationError):
        # A string, a list or a lazy string is about the whole form.
        return field_map(refused.detail)
    return {NON_FIELD_ERRORS: [refused.message]}


def _page_form(
    spec: ServiceSpec, principal: Any, target: Any, served: _Served, **bound: Any
) -> forms.BaseForm:
    """The page's form, as ``_built_form`` builds it, with every field the binding answers for held.

    Built with what the Validator's ``form_kwargs`` answers, through the
    kernel's ``form_keywords``, so the page's form and the one the Validator
    binds are one form: the same principal, the request's seeds, the row
    itself (never the copy a ``ModelForm`` edits) and the request's one call
    context, the dict dispatch is then handed
    (test_the_page_and_the_validator_are_handed_the_same_keywords). A post
    whose target was never found, its route refused, is asked about no row:
    the marker of a target not yet looked for is the page's, and is never
    handed on (test_a_post_whose_route_is_refused_is_built_as_no_row).

    A field the binding binds is disabled and shows the server's value; one
    it withholds is disabled and shows none. A disabled field shows its
    initial value however the form is bound, so a refused post re-renders
    the server's value and never the one the client posted for it, and the
    post never reads it (``_form_arguments`` skips a disabled field), so the
    binding alone fills it. Only this form's own copies change: Django gives
    each form instance its own ``fields``, so the form class and every other
    request's form keep theirs (test_the_form_class_is_left_as_it_was). Held
    on GET and on a refused post by
    test_a_bound_field_renders_disabled_with_the_server_s_value,
    test_a_refused_post_shows_the_server_s_value_never_the_client_s and
    test_a_withheld_field_renders_disabled_with_no_value.
    """
    keywords = form_keywords(
        spec,
        principal,
        pool_seeds=served.seeds,
        target=None if target is UNSET else target,
        call_context=served.call_context,
    )
    form = _built_form(spec, target, keywords, **bound)
    if spec.partial:
        # A partial post may leave out any field, so the browser must not
        # refuse to send one blank. This form's own attribute, so the class
        # and every other page keep theirs
        # (test_a_partial_page_renders_no_required_attribute).
        form.use_required_attribute = False
    server = served.server
    owned = server_names(server)
    values: Mapping[str, Any] = {} if server is None else server.values
    for name in [name for name in form.fields if name in owned]:
        form.fields[name].disabled = True
        # ``None`` for a withheld name: the field shows no value.
        form.initial[name] = values.get(name)
    return form


def _built_form(
    spec: ServiceSpec, target: Any, keywords: Mapping[str, Any], **bound: Any
) -> forms.BaseForm:
    """The page's form, bound by ``bound`` if given, showing ``target``'s values where it is a row.

    ``keywords`` are what ``form_kwargs`` answered, passed beside ``bound`` as
    the Validator passes them beside its own. An ``initial`` among them, on a
    form taking the row's values as its own ``initial``, is merged under
    those values rather than passed twice, so a field the row does not fill
    keeps it
    (test_an_initial_form_kwargs_answers_sits_under_the_row_s_values).

    A ``ModelForm`` of the row's model is built on a copy of it, as
    ``FormValidator`` binds one, so its own ``__init__`` sees the row it
    edits; any other form takes the row's values for the fields it names, its
    own fields alone, as its ``initial``. A disabled field is left out: the
    Validator declares none, and the form cleans one from its own
    ``initial``, so the row's value is never what the post sends
    (test_a_disabled_field_shows_its_own_initial). One branch to coverage, so
    each condition is held by its own test:
    test_a_plain_form_shows_the_row_s_values_for_its_own_fields (a
    ``ModelForm``: a plain form takes no ``instance``) and
    test_a_model_form_of_another_model_is_built_as_a_create (the row's model,
    as the Validator binds it).
    """
    form_class = _form_class(spec)
    if not isinstance(target, models.Model):
        return form_class(**bound, **keywords)
    if issubclass(form_class, forms.BaseModelForm) and isinstance(target, form_class._meta.model):
        # A copy, as for the Validator: a post builds this form before
        # dispatch, on the very row it then hands dispatch as its target, so
        # anything the form's constructor writes onto its instance - a
        # default filled in - would reach the Validator and the service as
        # the row's stored values
        # (test_the_page_form_s_writes_never_reach_the_row_dispatch_is_handed).
        return form_class(**bound, **keywords, instance=copy.copy(target))
    # ``base_fields`` is set by the metaclass, which the stubs do not declare.
    fields: dict[str, forms.Field] = form_class.base_fields  # ty: ignore[unresolved-attribute]
    names = [name for name, field in fields.items() if not field.disabled]
    own = {name: value for name, value in keywords.items() if name != "initial"}
    initial = {**keywords.get("initial", {}), **model_to_dict(target, fields=names)}
    return form_class(**bound, **own, initial=initial)


def _form_arguments(
    form: forms.BaseForm, parameters: Parameters, url_kwargs: Mapping[str, Any], served: _Served
) -> dict[str, Any]:
    """The arguments a bound form's post carries, read as the form reads it, route merged last.

    A field naming a parameter the server answers for is never read:
    ``_page_form`` disables it, and a disabled field is skipped, so a value
    the client was never offered is never coerced or refused as the
    client's; the caller lays the binding over the result.
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
    # Every value is typed as the shape check reads it by now, or a string
    # for ``coerce_flat`` to type by its declaration, which passes a value
    # that is not a string through untouched. The route's kwargs are coerced
    # in the same call, so one refusal carries every problem, once
    # ``route_arguments`` - the check ``request_arguments`` makes too - has
    # refused any the spec does not declare.
    route = route_arguments(parameters, url_kwargs, route_kwargs=served.route_kwargs)
    return coerce_flat(parameters, {**read, **route})


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
