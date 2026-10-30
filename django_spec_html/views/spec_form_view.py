"""``SpecFormView`` - a spec validated by a Django form, served as the page that form is."""

from __future__ import annotations

import copy
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
    FormValidator,
    InvalidArguments,
    ServerArguments,
    ServiceError,
    ServiceSpec,
    bind_arguments,
    clean_held_to_their_columns,
    coerce_flat,
    form_keywords,
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
    form as a refused commit's is, at 200 still, with ``refreshed`` in the
    context. Where ``HX-Trigger-Name`` names one of the form's fields, as
    its ``html_name``, the name it posts under, only that field's errors are
    placed and no non-field error; on every other refresh, the whole
    refusal. **It runs no provider**, as ``bind_arguments`` runs neither
    ``input_data`` nor ``kwargs``: a field the spec's ``input_data`` fills
    is judged as posted, so a required one left blank is refused by the
    refresh and accepted by the commit, which the provider fills.

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
    ``success_url`` that does not override ``get_success_url``, a
    ``commit_button`` that is not a button's name or is one of the form's
    fields, and a ``redirect_field_name`` that is not a key's name, is one of
    the form's fields or is the ``commit_button``.

    **Sync only**, in this release. **CSRF is the host's middleware**, as for
    any Django view, and the template renders ``{% csrf_token %}``.

    Attributes:
        spec: The operation served. Required.
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
    asked_fields: tuple[str, ...] = ()
    validate_on_refresh: bool = False

    @classmethod
    def _check_spec(cls, label: str, spec: ServiceSpec) -> None:
        """Refuse a spec whose Validator is not a ``FormValidator``, and a ``many`` spec."""
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

    @classmethod
    def _check_page(cls, label: str, spec: ServiceSpec, initkwargs: Mapping[str, Any]) -> None:
        """Refuse ``asked_fields``, and a ``commit_button`` or ``redirect_field_name`` that is one of the form's fields."""
        _check_asked_fields(
            label,
            spec,
            initkwargs.get("asked_fields", cls.asked_fields),
            initkwargs.get("server_arguments", cls.server_arguments),
        )
        _check_commit_field(label, spec, initkwargs.get("commit_button", cls.commit_button))
        _check_redirect_field(
            label, spec, initkwargs.get("redirect_field_name", cls.redirect_field_name)
        )

    def _form(
        self, spec: ServiceSpec, principal: Any, target: Any, served: Served, **bound: Any
    ) -> forms.BaseForm:
        """The page's form, as ``_page_form`` builds it."""
        return _page_form(spec, principal, target, served, **bound)

    def _arguments(
        self, form: forms.BaseForm, spec: ServiceSpec, url_kwargs: Mapping[str, Any], served: Served
    ) -> Mapping[str, Any]:
        """The post's arguments: the form's values, and the route's kwargs coerced in the same call.

        One refusal then carries every problem, once ``route_arguments`` -
        the check ``request_arguments`` makes too - has refused any kwarg the
        spec does not declare.
        """
        parameters = spec.parameters()
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
        """A refreshed form's error state: none, or what binding the post refuses under ``validate_on_refresh``.

        Set before ``_render_page``, whose ``_hold_back`` reads the form's
        errors and would otherwise validate it whole. Judged, the post is
        read as a commit reads it, the binding laid over it, and bound through
        the kernel's ``bind_arguments`` with the principal admitted, the
        target found, the request's seeds - so a ``form_kwargs`` naming one is
        handed it (test_a_form_whose_keywords_name_a_seed_is_bound_with_it) -
        and its one call context. Nothing is dispatched. What it refuses is
        placed as a refused commit's is, onto an empty error state, so the
        form's own validation adds nothing the bind did not say, and only
        what ``HX-Trigger-Name`` asks for, as ``_triggered`` reads it.
        """
        without_errors(form)
        if not self.validate_on_refresh:
            return
        try:
            read = self._arguments(form, spec, url_kwargs, served)
            bind_arguments(
                spec,
                with_binding(spec, read, served.server),
                principal=principal,
                target=target,
                unknown_arguments=self.unknown_arguments,
                pool_seeds=served.seeds,
                call_context=served.call_context,
            )
        except InvalidArguments as refused:
            add_argument_errors(form, _triggered(form, trigger_name(request), refused.detail))

    def _render_page(
        self, form: forms.BaseForm, refreshed: bool, /, **context: Any
    ) -> HttpResponse:
        """The page GET renders, ``asked_fields`` held back; a refreshed form's errors are ``_refreshed``'s."""
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
        """The page again, the bound form carrying ``refused``, and its own errors where merged.

        Merged, for an argument refusal of a whole call on a found target, the
        page's form is validated first, as the Validator
        validates its own, under the kernel's ``clean_held_to_their_columns``,
        and the refusal is placed beside what it found, never twice. So after
        a shape-check refusal the form's own checks - a length, ``clean()``,
        uniqueness - answer in the same post
        (test_a_blank_required_field_and_a_clean_violation_show_in_one_post).
        The guards are what validating it needs: without them it would hand a
        number no integer column holds to the database driver, and SQLite's
        raises ``OverflowError``
        (test_a_key_past_its_column_beside_a_shape_refusal_never_reaches_the_driver).

        Otherwise the form carries the refusal alone: its error state is set to
        none directly, which is what Django's ``add_error`` needs, and the
        refusal is placed on it.

        Then ``asked_fields`` are held back, but for those ``requested`` asks
        for, as ``_hold_back`` reads them, and a question's
        ``requested_input``, its message and its schema, takes the form's bound
        fields it names, in the schema's order. A key no field of the form
        declares is asked by the message alone, since the page builds no
        widget from a schema
        (test_a_key_no_field_declares_is_asked_by_its_message_alone).
        """
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
            isinstance(refused, InvalidArguments)
            and not self.served_spec().partial
            and target is not UNSET
        )
        if merged:
            clean_held_to_their_columns(form)
        else:
            without_errors(form)
        add_argument_errors(form, refusal_tree(refused))
        schema: Mapping[str, Any] = {}
        # One branch to coverage, so each condition is held by its own test:
        # test_any_other_refusal_holds_it_back (no question was asked) and
        # test_a_question_with_no_schema_reveals_nothing (one that names no key).
        if requested is not None and requested.schema is not None:
            schema = requested.schema
        _hold_back(form, self.asked_fields, schema)
        if requested is not None:
            context["requested_input"]["fields"] = [
                form[name] for name in schema if name in form.fields
            ]
        return self._rendered(self.get_context_data(form=form, **context), status=status)


def _check_commit_field(label: str, spec: ServiceSpec, button: Any) -> None:
    """Refuse a ``commit_button`` that is the name of one of the form's fields.

    Asked once the base has refused one that is not a button's name. A
    field's name is refused (test_a_field_s_name_is_refused), since a
    field posts its value on every submit, and the rule could never tell a
    post that commits from one that does not.
    """
    form_class = _form_class(spec)
    if button in _declared(form_class):
        raise ImproperlyConfigured(
            f"{label}.commit_button names {button!r}, a field {form_class.__name__} declares: a "
            "field is posted by every submit, so it cannot say which one commits."
        )


def _check_redirect_field(label: str, spec: ServiceSpec, name: Any) -> None:
    """Refuse a ``redirect_field_name`` that is the name of one of the form's fields.

    Asked once the base has refused one that is not a key's name. A field's
    name is refused (test_a_field_s_name_is_refused in
    ``TestTheRedirectFieldName``), since the field's value, posted by every
    submit, would be read as the URL a success returns to.
    """
    form_class = _form_class(spec)
    if name in _declared(form_class):
        raise ImproperlyConfigured(
            f"{label}.redirect_field_name names {name!r}, a field {form_class.__name__} "
            "declares: the field's value would be read as the URL to return to."
        )


def _declared(form_class: type[forms.BaseForm]) -> dict[str, forms.Field]:
    """The fields ``form_class`` declares, by name."""
    # ``base_fields`` is set by the metaclass, which the stubs do not declare.
    return form_class.base_fields  # ty: ignore[unresolved-attribute]


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


def _triggered(
    form: forms.BaseForm, trigger: str | None, detail: Mapping[Any, Any]
) -> Mapping[Any, Any]:
    """The part of a refresh's refusal to show: one field's, where ``trigger`` names it, or all of it.

    ``trigger`` is ``HX-Trigger-Name``, the element an htmx request was
    issued from, matched against each field's ``html_name``, the name it
    posts under. A field's own request, an
    ``hx-trigger="change"`` on its input, shows that field's errors and
    neither another field's nor the form's: a person has not reached the
    rest yet. Any other refresh - no header, the form's own name, a refresh
    button - shows the whole refusal.
    """
    named = next((field.name for field in form if field.html_name == trigger), None)
    # One branch arc inside the comprehension, so each condition is held by
    # its own test: test_a_refresh_no_field_issued_shows_every_error (no
    # field named, so every key), test_a_field_s_own_request_shows_that_field_s_errors_alone
    # (a field named, so its key alone, and no non-field error), with
    # test_a_prefixed_form_s_field_is_named_as_it_posts holding the match on
    # ``html_name`` rather than ``name``.
    return {key: value for key, value in detail.items() if named is None or key == named}


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


def _form_class(spec: ServiceSpec) -> type[forms.BaseForm]:
    """The form the spec validates with, which ``as_view`` checked is there."""
    return cast("FormValidator", spec.validator).form_class


def _page_form(
    spec: ServiceSpec, principal: Any, target: Any, served: Served, **bound: Any
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
    post never reads it (``form_values`` skips a disabled field), so the
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
