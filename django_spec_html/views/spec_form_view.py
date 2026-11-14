from __future__ import annotations

import copy
import inspect
from collections.abc import Collection, Mapping
from typing import Any, cast

from django import forms
from django.core.exceptions import (
    ImproperlyConfigured,
)
from django.db import models
from django.forms.models import model_to_dict
from django.http import (
    HttpRequest,
    HttpResponse,
)
from django_service_specs import (
    UNSET,
    AdditionalInputRequired,
    DispatchError,
    DispatchResult,
    FormValidator,
    InvalidArguments,
    ServerArguments,
    ServiceError,
    ServiceSpec,
    Validator,
    bind_arguments,
    clean_held_to_their_columns,
    coerce_flat,
    form_keywords,
    server_owned_keys,
)

from django_spec_html.http.add_argument_errors import add_argument_errors
from django_spec_html.http.route_arguments import route_arguments
from django_spec_html.http.utils import (
    server_names,
)
from django_spec_html.views.utils import (
    Served,
    SpecPageBase,
    form_values,
    refusal_tree,
    trigger_name,
    with_binding,
    without_errors,
)


class SpecFormView(SpecPageBase):
    """A ``ServiceSpec`` validated by a form, served as the page a person fills that form in on.

    The spec's Validator must be a
    [`FormValidator`][django_service_specs.adapters.forms.form_validator.FormValidator],
    and its form class is the page's form:

        urlpatterns = [
            path(
                "books/new/",
                SpecFormView.as_view(spec=add_book_spec, success_url=reverse_lazy("books")),
            ),
        ]

    **GET** renders ``template_name`` with an unbound form under ``form``,
    beside ``view`` and ``spec``: the names Django's ``FormView`` uses, with
    ``extra_context`` merged in as Django's ``ContextMixin`` does. The spec's
    class-level permission check runs first, through
    [`authorize`][django_service_specs.dispatch.authorize.authorize], so
    a page is never offered to a principal the post would refuse.

    **The page's template is optional.** ``template_name`` is
    ``django_spec_html/spec_form.html`` by default, a template this package
    ships: a plain document with no stylesheet, script or htmx attribute,
    holding the target pane, an unmet affordance's reason with the form
    disabled, and the form - its errors, a question's message and the field
    it asks for, and a submit button posting ``commit_button``'s name where
    one is set - inside a POST form with its token. It is found as any app's
    templates are, so a project lists ``django_spec_html`` in
    ``INSTALLED_APPS`` with ``APP_DIRS`` on, and ``as_view()`` refuses a page
    rendering it that no template engine finds. Its form is a file of its
    own, ``django_spec_html/spec_form_fragment.html``, which the page
    includes and an htmx request is answered with, on every Django. Override
    either file the Django way, with one of the same name in a template
    directory found first, or ``django_spec_html/base.html``, the document
    every shipped page extends; or set ``template_name`` to a page of the
    project's own.

    **An update's page shows the row it updates.** Every GET then finds its
    target as dispatch would, through
    [`resolve_target`][django_service_specs.dispatch.resolve_target.resolve_target]
    on the route's kwargs, coerced as a post's are: the row or set a target
    selector resolves, or ``None`` for a create. A check refusing it - the
    object-level check on a row, the scope check on a set or on ``None`` -
    raises ``PermissionDenied``, as the post would be refused; a missing row
    or a selector's ``ServiceNotFound`` raises ``Http404``; and a route value
    the spec's parameters refuse raises ``Http404`` too, or Django's
    ``BadRequest`` with ``route_refusal_404`` off. The form
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
    value the target's reads refuse is a 404 before the post is read; with
    ``route_refusal_404`` off it is left to dispatch, whose one refusal
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
      URL name works, and a subclass overrides it to read the result. A
      view naming ``redirect_field_name`` - ``"next"``, as Django's
      ``LoginView`` names it - redirects instead to the URL the post or its
      query carries under that key, where ``get_redirect_url()`` finds it
      safe, so a page linked to from a list returns to the list.
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
      htmx swaps no 4xx by default. A shipped page's is its fragment file.
      For a page of the project's own the default address is
      ``<template_name>#spec-form``, a ``{% partialdef %}`` inside the page's
      own template, which Django 6.0 resolves; below it,
      django-template-partials does, with its tag named in ``TEMPLATES`` as
      a builtin (``"builtins": ["template_partials.templatetags.partials"]``)
      and its loader around the project's. A template with no fragment at the
      address is Django's ``TemplateDoesNotExist`` naming it, never the page,
      which swapped into a form's target would nest a second layout inside it.
    - **A success** is ``fragment_success(result)``: ``HX-Redirect`` to
      ``get_redirect_url()`` or ``get_success_url(result)``, or the
      ``spec-result`` fragment in place.
    - **A denial** is raised in both modes alike, for the host's handlers.

    Both wrappers set ``Vary`` on the three headers the choice reads. No
    htmx header reaches dispatch: the arguments are the form's fields and the
    route's kwargs whichever wrapper answers, so a spec cannot tell the two
    apart. One header is read before dispatch, ``HX-Trigger-Name``, and only
    to stop a post committing and, on a post that does not commit, to say
    which errors a refresh validated under ``validate_on_refresh`` shows,
    both below.

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

    **Validating a post that does not commit.** ``validate_on_refresh``
    shows on such a post what its commit would refuse, so a field is checked
    as a person leaves it. The post is read as a commit reads it, the binding
    laid over it, and bound through the kernel's
    [`bind_arguments`][django_service_specs.dispatch.bind_arguments.bind_arguments]
    with the principal, the target the page found, the request's seeds and
    its call context: the shape check and the Validator, and nothing
    dispatched, no ``success_message`` sent. Its refusal is placed on the
    form as a refused commit's is, beside the page's own form validated on a
    whole call, so a field the shape check refuses does not hide what the
    form's own checks - ``clean_<field>()``, a length, ``clean()`` - say of
    the rest, at 200 still, with ``refreshed`` in the context. Where
    ``HX-Trigger-Name`` names one of the form's fields, as its
    ``html_name``, the name it posts under, only that field's errors are
    shown, the shape check's and the form's own alike, and no non-field
    error; on every other refresh, every error. **It runs no provider**, as
    ``bind_arguments`` runs neither ``input_data`` nor ``kwargs``: a field the
    spec's ``input_data`` fills is judged as posted, so a required one left
    blank is refused by the refresh and accepted by the commit, which the
    provider fills.

    **A success message.** ``success_message``, where it is set, is sent
    through ``django.contrib.messages`` on every success - the redirect, the
    ``HX-Redirect`` and the result fragment alike - formatted by
    ``get_success_message(result)``, ``%`` with the Validator's data, the form's
    cleaned data here, as Django's ``SuccessMessageMixin`` formats it. Without the messages
    middleware it is skipped, and never raises ``MessageFailure``. A refusal,
    a confirmation page and a post that does not commit send none.

    **The request is read through the same attributes and hooks as**
    ``SpecView``**'s**, checked by the same code in ``http/utils.py``:
    ``route_kwargs`` says which URL kwargs fill which parameters;
    ``get_pool_seeds(request)``, asked once per request, answers the seeds
    the class-level check, the target's lookup and its object-level or scope
    check, the form's ``form_kwargs``, the affordances and dispatch are
    handed; and
    ``get_server_arguments(request)`` answers the binding, laid over the
    route's arguments before the target is found and over the post's before
    dispatch. A field of the page's form the binding binds renders disabled
    with the server's value, and one it withholds disabled and empty, so the
    post never reads it. **The seeds the spec's checks name are resolved
    once per request**, by the kernel's ``resolve_seeds`` ahead of the
    class-level check, and its ``CallSeeds`` is handed to that check, the
    target's lookup and its checks, and dispatch, so each sees one value of
    each. The form's ``form_kwargs``, the affordances' pool and a validated
    refresh's ``bind_arguments`` take no carrier, and resolve the seeds
    again: a resolver answering a fresh object answers each of those a
    different one.

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
    value of the field marked ``LABEL``, beside them where there is one, and
    ``pairs``, the same fields as name and value in order, for a template to
    loop over, since ``fields.items`` reads a field named ``items``; a
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
    reaches the database driver. With ``route_refusal_404`` off, a post whose
    route the target's reads refuse never found its row, so its form is not
    judged as one and carries the refusal alone. Any other refusal is no verdict on the
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

    **One form, several operations.** ``actions`` maps each submit button's
    name to the spec it runs, in place of ``spec``. A committing post runs the
    action whose key ``request.POST`` carries, and one naming none or several
    is Django's ``BadRequest``; GET, and a post that does not commit, render
    the first action's. ``success_url`` and ``success_message`` are
    page-wide, and ``get_success_url`` and ``get_success_message`` read the
    action that ran as ``self.action_name``. The shipped template renders one
    button per action, named by its key. Every action is checked as ``spec``
    is, and validated by a ``FormValidator`` of one form class.

    **A form over any Validator.** ``form_class`` names the form a page
    renders for a spec whose Validator is not a ``FormValidator``. It is
    built as above, with ``get_form_kwargs()`` in place of ``form_kwargs``,
    and read as above, and never validated: the Validator's refusal is the
    only error its form carries, so its ``clean()``, field validators and
    uniqueness queries never run. ``success_message`` formats over the
    Validator's data, a mapping's keys or an object's attributes. ``as_view()``
    refuses a form the post cannot be read off, a field or ``<name>-clear``
    checkbox the spec does not declare, a required parameter neither the form,
    the server nor a tuple ``route_kwargs`` fills, a field whose type, element
    type, format where the spec names one, or ``required`` differs from its
    parameter's (``any`` takes any field), a Validator with ``unsendable``
    fields, ``form_class`` over a ``FormValidator``, and a form defining its
    own ``clean()`` or ``clean_<field>()``. Every action shares it.

    ``as_view()`` refuses, when the URLconf is imported, a view with no
    ``ServiceSpec``, a spec whose Validator is not a ``FormValidator`` on a
    page with no ``form_class``, a
    ``many`` spec, whose list of rows no form posts, a view with no
    ``success_url`` that does not override ``get_success_url``, a
    ``commit_button`` that is not a button's name or is one of the form's
    fields, and a ``redirect_field_name`` that is not a key's name, is one of
    the form's fields or is the ``commit_button``; and ``actions`` beside a
    ``spec``, empty, keyed by anything but a non-empty string or by a field of
    the form, posting different form classes, or keyed by the
    ``commit_button`` or the ``redirect_field_name``.

    **Sync only**, in this release. **CSRF is the host's middleware**, as for
    any Django view, and the template renders ``{% csrf_token %}``.

    Attributes:
        spec: The operation served. Required, unless ``actions`` is set.
        form_class: The form the page renders over a Validator that is not a
            ``FormValidator``, never validated, checked at ``as_view()``;
            ``None``, the default, renders the ``FormValidator``'s.
        actions: The operations served, by the name of the submit button that
            runs each: a non-empty mapping, in place of ``spec``. ``None`` by
            default.
        action_name: The key of the action a committing post runs, set before
            it is dispatched, for the success hooks to read; ``None`` on GET,
            on a post that does not commit, and on a page without ``actions``.
        template_name: The page's template, as for any Django template view:
            ``django_spec_html/spec_form.html``, the one this package ships,
            by default.
        success_url: Where a successful post redirects, read by
            ``get_success_url``: a path, a ``reverse_lazy``, or a URL name.
        pool_seeds: The seeds the spec's callables are resolved with, unless
            ``get_pool_seeds`` answers others for a request.
        unknown_arguments: What becomes of an argument no parameter declares.
        route_kwargs: Which URL kwargs fill which parameters: ``None`` for
            the strict rule, a mapping from a captured name to the parameter
            it fills or to ``None`` to drop it, or a tuple of names to allow,
            as ``route_arguments`` reads it.
        route_refusal_404: Whether a route value the spec's parameters
            refuse is a 404, the default, as Django's own converters answer
            one, or, off, the 400 or form refusal of 0.12.0.
        server_arguments: The kernel's ``ServerArguments`` the server answers
            for, checked at ``as_view()``. Its values may vary per request,
            through ``get_server_arguments``, and its names may not.
        fragment_template_name: The template an htmx request's form is
            answered with, for a fragment in another file or under another
            name; ``None``, the default, addresses ``<template_name>#spec-form``,
            or a shipped page's own fragment file.
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
        redirect_field_name: The key a post or its query carries the URL a
            success returns to under, read by ``get_redirect_url``:
            ``None``, the default, reads none; a non-empty string no field of
            the form declares and the ``commit_button`` is not, checked at
            ``as_view()``. The page's context carries it, with the URL as
            ``next``, for a refused post to send again.
        validate_on_refresh: Whether a post that does not commit shows what
            its commit would refuse, bound through the kernel's
            ``bind_arguments`` and never dispatched. ``False`` by default,
            which shows no error. A page with no ``commit_button`` commits
            every post, so it has nothing to refresh.
        success_message: The message a successful post sends through
            ``django.contrib.messages``, formatted by
            ``get_success_message`` with the form's cleaned data. Empty by
            default, which sends none.
    """

    template_name = "django_spec_html/spec_form.html"
    form_class: type[forms.BaseForm] | None = None
    asked_fields: tuple[str, ...] = ()
    validate_on_refresh: bool = False
    _takes_actions = True

    @classmethod
    def _check_actions(cls, label: str, actions: Mapping[str, ServiceSpec]) -> None:
        """Refuse actions posting different forms, or one named like a field of the form."""
        first, *rest = actions.items()
        if not isinstance(first[1].validator, FormValidator):
            return
        form_class = _form_class(first[1])
        for key, spec in rest:
            other = _form_class(spec)
            if other is not form_class:
                raise ImproperlyConfigured(
                    f"{label}.actions[{key!r}] validates with {other.__name__}, and the page's "
                    f"form is {form_class.__name__}, the first action's: every action posts the "
                    "page's one form."
                )
        _check_action_keys(label, actions, form_class)

    @classmethod
    def _check_spec(cls, label: str, spec: ServiceSpec) -> None:
        """Refuse a ``many`` spec."""
        if spec.many:
            raise ImproperlyConfigured(
                f"{label} posts one row, the form's, and a many spec takes a list of rows "
                "under its many_argument: serve it from a view that sends the list."
            )

    @classmethod
    def _check_page(cls, label: str, spec: ServiceSpec, initkwargs: Mapping[str, Any]) -> None:
        """Refuse the form against the spec, ``asked_fields``, or a button or redirect field."""
        form_class = initkwargs.get("form_class", cls.form_class)
        server = initkwargs.get("server_arguments", cls.server_arguments)
        if form_class is None:
            if not isinstance(spec.validator, FormValidator):
                raise ImproperlyConfigured(
                    f"{label} renders the form its spec validates with, unless form_class names "
                    "one: set form_class, or the spec's validator must be a FormValidator; got "
                    f"{type(spec.validator).__name__}."
                )
            form_class = _form_class(spec)
        else:
            route = initkwargs.get("route_kwargs", cls.route_kwargs)
            _check_form_class(label, spec, form_class, route, server)
            actions = initkwargs.get("actions", cls.actions)
            if actions is not None:
                _check_action_keys(cls.__name__, actions, form_class)
        _check_asked_fields(
            label, form_class, initkwargs.get("asked_fields", cls.asked_fields), server
        )
        _check_commit_field(label, form_class, initkwargs.get("commit_button", cls.commit_button))
        _check_redirect_field(
            label, form_class, initkwargs.get("redirect_field_name", cls.redirect_field_name)
        )

    def get_form_kwargs(self) -> Mapping[str, Any]:
        """The keywords a ``form_class`` page's form is built with: none by default."""
        return {}

    def get_success_message(self, result: DispatchResult) -> str:
        """``success_message`` formatted with the Validator's data, a mapping or its attributes."""
        data = result.data
        return self.success_message % (data if isinstance(data, Mapping) else _Attributes(data))

    def _form(
        self, spec: ServiceSpec, principal: Any, target: Any, served: Served, **bound: Any
    ) -> forms.BaseForm:
        """The page's form: ``form_class`` with ``get_form_kwargs()``, or the Validator's."""
        if self.form_class is None:
            keywords = form_keywords(
                spec,
                principal,
                pool_seeds=served.seeds,
                target=None if target is UNSET else target,
                call_context=served.call_context,
            )
            return _page_form(_form_class(spec), spec, target, served, keywords, **bound)
        keywords = self.get_form_kwargs()
        taken = sorted(frozenset(keywords) & _PAGE_KEYWORDS)
        if taken:
            raise ImproperlyConfigured(
                f"{type(self).__name__}.get_form_kwargs() returned {taken[0]!r}, which the page "
                "sets itself: it returns the form's other keywords."
            )
        return _page_form(self.form_class, spec, target, served, keywords, **bound)

    def _arguments(
        self, form: forms.BaseForm, spec: ServiceSpec, url_kwargs: Mapping[str, Any], served: Served
    ) -> Mapping[str, Any]:
        """The post's arguments: the form's values, and the route's kwargs, coerced together."""
        parameters = spec.parameters(call_context=served.call_context)
        route = route_arguments(parameters, url_kwargs, route_kwargs=served.route_kwargs)
        return coerce_flat(parameters, {**form_values(form), **route})

    def _refreshed(
        self,
        request: HttpRequest,
        form: forms.BaseForm,
        spec: ServiceSpec,
        principal: Any,
        target: Any,
        served: Served,
        url_kwargs: Mapping[str, Any],
        /,
    ) -> None:
        """A refreshed form's error state: what the post refuses under ``validate_on_refresh``."""
        without_errors(form)
        if not self.validate_on_refresh:
            return
        try:
            read = self._arguments(form, spec, url_kwargs, served)
            bind_arguments(
                spec,
                with_binding(spec, read, served),
                principal=principal,
                target=target,
                unknown_arguments=self.unknown_arguments,
                pool_seeds=served.seeds,
                call_context=served.call_context,
            )
        except InvalidArguments as refused:
            self._place_refusal(form, refused, target)
            _triggered(form, trigger_name(request))

    def _place_refusal(
        self, form: forms.BaseForm, refused: DispatchError | ServiceError, target: Any, /
    ) -> None:
        """Place ``refused`` on the bound form, beside its own errors."""
        merged = (
            isinstance(refused, InvalidArguments)
            and self.form_class is None
            and not self.served_spec().partial
            and target is not UNSET
        )
        if merged:
            clean_held_to_their_columns(form)
        else:
            without_errors(form)
        add_argument_errors(form, refusal_tree(refused))

    def _render_page(
        self, form: forms.BaseForm, refreshed: bool, /, **context: Any
    ) -> HttpResponse:
        """The page GET renders, ``asked_fields`` held back; errors are ``_refreshed``'s."""
        _hold_back(form, self.asked_fields, ())
        return self._rendered(self.get_context_data(form=form, **context), status=200)

    def _render_refused(
        self,
        form: forms.BaseForm,
        refused: DispatchError | ServiceError,
        target: Any,
        /,
        *,
        status: int,
        requested: AdditionalInputRequired | None,
        **context: Any,
    ) -> HttpResponse:
        """The page again, the bound form carrying ``refused``, and its own errors where merged."""
        self._place_refusal(form, refused, target)
        schema: Mapping[str, Any] = {}
        if requested is not None and requested.schema is not None:
            schema = requested.schema
        _hold_back(form, self.asked_fields, schema)
        if requested is not None:
            context["requested_input"]["fields"] = [
                form[name] for name in schema if name in form.fields
            ]
        return self._rendered(self.get_context_data(form=form, **context), status=status)


def _check_commit_field(label: str, form_class: type[forms.BaseForm], button: Any) -> None:
    """Refuse a ``commit_button`` that is the name of one of the form's fields."""
    if button in _declared(form_class):
        raise ImproperlyConfigured(
            f"{label}.commit_button names {button!r}, a field {form_class.__name__} declares: a "
            "field is posted by every submit, so it cannot say which one commits."
        )


def _check_redirect_field(label: str, form_class: type[forms.BaseForm], name: Any) -> None:
    """Refuse a ``redirect_field_name`` that is the name of one of the form's fields."""
    if name in _declared(form_class):
        raise ImproperlyConfigured(
            f"{label}.redirect_field_name names {name!r}, a field {form_class.__name__} "
            "declares: the field's value would be read as the URL to return to."
        )


def _declared(form_class: type[forms.BaseForm]) -> dict[str, forms.Field]:
    """The fields ``form_class`` declares, by name."""
    return form_class.base_fields  # ty: ignore[unresolved-attribute]


def _check_asked_fields(
    label: str, form_class: type[forms.BaseForm], asked: Any, server: ServerArguments | None
) -> None:
    """Refuse ``asked_fields`` naming anything but optional fields of the form."""
    if not isinstance(asked, tuple):
        raise ImproperlyConfigured(
            f"{label}.asked_fields must be a tuple of the form's field names; got {asked!r}."
        )
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


def _triggered(form: forms.BaseForm, trigger: str | None) -> None:
    """The part of a refused refresh's errors to show: ``trigger``'s field, or all."""
    named = next((field.name for field in form if field.html_name == trigger), None)
    errors = form.errors
    for key in [key for key in errors if named is not None and key != named]:
        del errors[key]


def _hold_back(form: forms.BaseForm, held: tuple[str, ...], asked: Collection[str]) -> None:
    """Take the ``held`` fields ``asked`` does not name out of the form a page renders."""
    for name in held:
        if name in asked:
            posted: Any = form.data
            data = posted.copy()
            data.pop(form.add_prefix(name), None)
            form.data = data
        elif name not in form.errors:
            form.fields.pop(name, None)


def _form_class(spec: ServiceSpec) -> type[forms.BaseForm]:
    """The form the spec validates with, which ``as_view`` checked is there."""
    return cast("FormValidator", spec.validator).form_class


def _page_form(
    form_class: type[forms.BaseForm],
    spec: ServiceSpec,
    target: Any,
    served: Served,
    keywords: Mapping[str, Any],
    **bound: Any,
) -> forms.BaseForm:
    """The page's form, as ``_built_form`` builds it, with the binding's fields held."""
    form = _built_form(form_class, target, keywords, **bound)
    if spec.partial:
        form.use_required_attribute = False
    server = served.server
    owned = server_names(server)
    values: Mapping[str, Any] = {} if server is None else server.values
    for name in [name for name in form.fields if name in owned]:
        form.fields[name].disabled = True
        form.initial[name] = values.get(name)
    return form


def _built_form(
    form_class: type[forms.BaseForm], target: Any, keywords: Mapping[str, Any], **bound: Any
) -> forms.BaseForm:
    """The page's form, bound by ``bound`` if given, showing ``target``'s values."""
    if not issubclass(form_class, forms.BaseModelForm) and _declares_instance(form_class):
        bound["instance"] = None if target is UNSET else target
    if not isinstance(target, models.Model):
        return form_class(**bound, **keywords)
    if issubclass(form_class, forms.BaseModelForm) and isinstance(target, form_class._meta.model):
        return form_class(**bound, **keywords, instance=copy.copy(target))
    fields: dict[str, forms.Field] = form_class.base_fields  # ty: ignore[unresolved-attribute]
    names = [name for name, field in fields.items() if not field.disabled]
    own = {name: value for name, value in keywords.items() if name != "initial"}
    initial = {**keywords.get("initial", {}), **model_to_dict(target, fields=names)}
    return form_class(**bound, **own, initial=initial)


def _declares_instance(form_class: type[forms.BaseForm]) -> bool:
    """Whether the form's ``__init__`` names ``instance``, as the kernel's bind reads it."""
    parameter = inspect.signature(form_class.__init__).parameters.get("instance")
    return parameter is not None and parameter.kind in _NAMED


_NAMED = (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)


_PAGE_KEYWORDS = frozenset({"data", "files", "instance"})


class _Attributes:
    """An object read by attribute where ``%`` reads a mapping by key."""

    def __init__(self, data: Any) -> None:
        self.data = data

    def __getitem__(self, name: str) -> Any:
        try:
            return getattr(self.data, name)
        except AttributeError:
            raise KeyError(name) from None


def _check_action_keys(
    label: str, actions: Mapping[str, ServiceSpec], form_class: type[forms.BaseForm]
) -> None:
    """Refuse an action named like a field of the page's form."""
    named = sorted(frozenset(actions) & frozenset(_declared(form_class)))
    if named:
        raise ImproperlyConfigured(
            f"{label}.actions names {named[0]!r}, a field {form_class.__name__} declares: a "
            "field is posted by every submit, so it cannot say which action runs."
        )


def _check_form_class(
    label: str,
    spec: ServiceSpec,
    form_class: type[forms.BaseForm],
    route: Mapping[str, str | None] | tuple[str, ...] | None,
    server: ServerArguments | None,
) -> None:
    """Refuse a ``form_class`` whose post the spec would not read as the form declares it."""
    named = f"{label}.form_class {form_class.__name__}"
    validator: Validator | None = spec.validator
    if isinstance(validator, FormValidator):
        raise ImproperlyConfigured(
            f"{label} sets form_class over a FormValidator, whose form is already the page's: "
            "drop form_class."
        )
    _check_no_clean(named, form_class)
    try:
        posted = {each.name: each for each in FormValidator(form_class).parameters().items}
    except ImproperlyConfigured as refused:
        raise ImproperlyConfigured(f"{named} cannot be read off a post: {refused}") from refused
    unsendable = sorted(validator.unsendable) if validator is not None else []
    if unsendable:
        raise ImproperlyConfigured(
            f"{label}'s spec requires {unsendable[0]!r}, which its Validator cannot declare, so "
            "no form can post it."
        )
    declared = {each.name: each for each in spec.parameters().items}
    for name, field in posted.items():
        if name not in declared:
            raise ImproperlyConfigured(
                f"{named} posts {name!r}, which the spec does not declare: it would reach "
                "dispatch as an unknown argument."
            )
        _check_agreement(named, field, declared[name])
    filled = server_owned_keys(spec) | server_names(server)
    for name, parameter in declared.items():
        closed = isinstance(route, tuple) and name not in route
        if parameter.required and name not in posted and name not in filled and closed:
            raise ImproperlyConfigured(
                f"{named} does not post {name!r}, which the spec requires, and neither the "
                "server nor the route fills it."
            )


def _check_no_clean(named: str, form_class: type[forms.BaseForm]) -> None:
    """Refuse a ``clean()`` or ``clean_<field>()`` the form defines above Django's own."""
    for klass in form_class.__mro__:
        if klass.__module__.startswith("django."):
            continue
        own = sorted(name for name in vars(klass) if name == "clean" or name.startswith("clean_"))
        if own:
            raise ImproperlyConfigured(
                f"{named} defines {own[0]}(), which the page never runs: the rule belongs in the "
                "spec's Validator."
            )


def _check_agreement(named: str, field: Any, parameter: Any) -> None:
    """Refuse a field whose wire type or ``required`` the spec's parameter does not share."""
    if parameter.type != "any" and _shape(field, parameter.format) != _shape(
        parameter, parameter.format
    ):
        raise ImproperlyConfigured(
            f"{named} posts {field.name!r} as {_spelled(field)}, and the spec declares "
            f"{_spelled(parameter)}."
        )
    if field.required != parameter.required:
        verb = "requires" if field.required else "does not require"
        spec_verb = "does not" if field.required else "does"
        raise ImproperlyConfigured(
            f"{named} {verb} {field.name!r}, and the spec {spec_verb}: the form and the spec "
            "must agree on whether it may be left out."
        )


def _shape(parameter: Any, fmt: str | None) -> tuple[Any, ...]:
    """What the type rule compares: the type, an array's element type, a format the spec names."""
    return (parameter.type, parameter.items, parameter.format if fmt is not None else None)


def _spelled(parameter: Any) -> str:
    """A parameter's wire type as a refusal names it."""
    if parameter.type == "array":
        return f"array of {parameter.items}"
    if parameter.format is not None:
        return f"{parameter.type} ({parameter.format})"
    return parameter.type
