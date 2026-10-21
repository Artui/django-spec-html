"""``SpecFormView`` - a spec validated by a Django form, served as the page that form is."""

from __future__ import annotations

import copy
import datetime
import math
from collections.abc import Callable, Mapping
from contextlib import suppress
from decimal import Decimal
from typing import Any, Final, cast

from django import forms
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
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBase, HttpResponseRedirect
from django.shortcuts import resolve_url
from django.utils.decorators import classonlymethod
from django.views import View
from django.views.generic.base import ContextMixin, TemplateResponseMixin
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    NON_FIELD_ERRORS,
    UNSET,
    DispatchError,
    DispatchResult,
    FormValidator,
    Grant,
    InvalidArguments,
    NotPermitted,
    Parameters,
    PoolSeeds,
    PrincipalUnavailable,
    ServiceError,
    ServiceNotFound,
    ServiceSpec,
    ServiceValidationError,
    UnknownArguments,
    authorize,
    coerce_flat,
    dispatch,
    resolve_target,
)

from django_spec_html.http.add_argument_errors import add_argument_errors
from django_spec_html.http.request_principal import request_principal
from django_spec_html.http.route_arguments import route_arguments
from django_spec_html.http.utils import field_map, refusal_status

_TYPED: Final = (forms.IntegerField, forms.DateField, forms.DateTimeField)
"""The fields whose input formats are wider than the wire form the shape check reads.

``DecimalField`` and ``FloatField`` subclass ``IntegerField``, so the first
row covers all three numbers. Each row is held by its own test:
test_an_integer_the_form_reads_is_not_refused,
test_dates_in_the_forms_input_formats_are_read_as_the_form_reads_them (both
date rows) and test_localized_numbers_are_read_as_the_form_reads_them."""


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
      a client reading a refused post - htmx, or Turbo, which will not render
      a failed post answered 200 - reads it the same way.
    - ``NotPermitted`` and ``PrincipalUnavailable`` raise Django's
      ``PermissionDenied``, and a not-found result or ``ServiceNotFound``
      raises ``Http404``, for the host's own 403 and 404 pages.

    **A re-rendered form carries the refusal and nothing else**: the page says
    what dispatch decided. The page's own form is never validated: its
    errors start empty and the refusal is placed on them, so nothing the
    Validator did not decide reaches the page, and a value the Validator
    refused because no column holds it never reaches the database through
    the page's own checks. After a shape-check refusal, then,
    the form's further checks - a length, ``clean()``, uniqueness - answer the
    next post rather than this one.

    ``as_view()`` refuses, when the URLconf is imported, a view with no
    ``ServiceSpec``, a spec whose Validator is not a ``FormValidator``, a
    ``many`` spec, whose list of rows no form posts, and a view with no
    ``success_url`` that does not override ``get_success_url``.

    **Sync only**, in this release. **CSRF is the host's middleware**, as for
    any Django view, and the template renders ``{% csrf_token %}``.

    Attributes:
        spec: The operation served. Required.
        template_name: The page's template, as for any Django template view.
        success_url: Where a successful post redirects, read by
            ``get_success_url``: a path, a ``reverse_lazy``, or a URL name.
        pool_seeds: The seeds the spec's callables are resolved with.
        unknown_arguments: What becomes of an argument no parameter declares.
    """

    spec: ServiceSpec | None = None
    success_url: Any = None
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT

    @classonlymethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., HttpResponseBase]:
        """Django's ``as_view``, then the spec and the redirect checked, once, for every request."""
        # Django's own check of the keywords first, so a misspelt ``sepc=`` is
        # refused as the typo it is rather than read as a view with no spec.
        view = super().as_view(**initkwargs)
        _check_configuration(cls, initkwargs)
        return view

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """The page, with an unbound form showing the target row, for a principal admitted to it."""
        spec = self.served_spec()
        principal, grant = _admitted(spec, request, kwargs)
        try:
            target, _ = _found(spec, principal, grant, kwargs, self.pool_seeds)
        except InvalidArguments as refused:
            # No post to place it on beside the form's: the route names a
            # target its reads refuse, which a post answers at 400 too.
            raise BadRequest(str(refused.detail)) from refused
        return self.render_to_response(self.get_context_data(form=_page_form(spec, target)))

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Dispatch the posted form: a redirect on success, the form re-rendered on a refusal."""
        spec = self.served_spec()
        # Admitted as ``get`` admits, before the form is built from the post:
        # re-rendering a refused post offers the page, and dispatch's shape
        # check - and ``coerce_flat`` before it - runs before its permission
        # check, so a principal the spec refuses was otherwise answered a
        # malformed post with the page, every row a choice field lists
        # included. A form's constructor may query, for choices it builds, so
        # it waits too. The grant hands dispatch the check already made, so it
        # runs once.
        principal, grant = _admitted(spec, request, kwargs)
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
            target, grant = _found(spec, principal, grant, kwargs, self.pool_seeds)
        form = _page_form(spec, target, data=request.POST, files=request.FILES)
        try:
            arguments = _form_arguments(form, spec.parameters(), kwargs)
            result = dispatch(
                spec,
                principal=principal,
                arguments=arguments,
                grant=grant,
                pool_seeds=self.pool_seeds,
                unknown_arguments=self.unknown_arguments,
                target=target,
            )
        except (DispatchError, ServiceError) as refused:
            # At the status ``error_response`` answers it with, whoever
            # refused: a denial raised by the operation, the target's checks
            # having answered before dispatch, is the host's 403 or 404 page.
            # Both statuses are held by
            # test_the_pages_outcome_table_is_the_one_shipped.
            status = refusal_status(refused)
            if status in (403, 404):
                raise _denial(refused) from refused
            return self._render_refused(form, _refusal_tree(refused), status=status)
        # Never ``not_found``: the target was found before dispatch, which
        # looks nothing up when handed it, and where the route's value was
        # refused the shape check refuses it again before any lookup.
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

    def _render_refused(
        self, form: forms.BaseForm, detail: Mapping[Any, Any], *, status: int
    ) -> HttpResponse:
        """The page again, the bound form carrying ``detail`` and nothing else.

        The page says what dispatch decided, and nothing the page's own form
        would add: its own errors are not shown, whatever its own checks
        would find, since the Validator's form is the one that decided. So
        the page's form is never validated: its error state is set to none
        directly, which is what Django's ``add_error`` needs, and the refusal
        is placed on it. Validated, it would hand a number no integer column
        holds to the database driver, with none of the Validator's guards,
        and SQLite's raises ``OverflowError``
        (test_a_list_s_key_past_its_column_is_refused_on_the_page).
        """
        # Django's own two attributes, as ``full_clean()`` starts them, with
        # the form's renderer as Django 5.0 and later pass it; ``Any``, as the
        # stubs declare neither as assignable.
        bound: Any = form
        bound._errors = ErrorDict(renderer=form.renderer)
        bound.cleaned_data = {}
        add_argument_errors(form, detail)
        return self.render_to_response(self.get_context_data(form=form), status=status)

    def served_spec(self) -> ServiceSpec:
        """``spec``, which ``as_view`` refused to build a view without."""
        return cast("ServiceSpec", self.spec)


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


def _form_class(spec: ServiceSpec) -> type[forms.BaseForm]:
    """The form the spec validates with, which ``as_view`` checked is there."""
    return cast("FormValidator", spec.validator).form_class


def _admitted(
    spec: ServiceSpec, request: HttpRequest, url_kwargs: Mapping[str, Any]
) -> tuple[Any, Grant]:
    """The principal and its grant, once the route and the class-level check pass.

    The route first, as ``SpecView`` refuses it on any method: a route
    capturing a kwarg the spec does not declare is wrong for every request and
    every principal, and a page served anyway fails only once someone has
    filled it in. Then the principal the spec's class-level check admits, so
    neither the page nor anything the post reaches is offered to one it
    refuses.
    """
    route_arguments(spec.parameters(), url_kwargs)
    try:
        principal = request_principal(request)
        return principal, authorize(spec, principal)
    except (NotPermitted, PrincipalUnavailable) as refused:
        raise _denial(refused) from refused


def _found(
    spec: ServiceSpec,
    principal: Any,
    grant: Grant,
    url_kwargs: Mapping[str, Any],
    pool_seeds: PoolSeeds,
) -> tuple[Any, Grant]:
    """The row, set or ``None`` the post acts on, and the grant vouching for it.

    Found by ``resolve_target`` as dispatch would find it, and refused as
    dispatch would refuse it, with Django's exceptions for the host's 403 and
    404 pages. A spec with no target selector - a create - resolves ``None``,
    which the scope check is asked about. The route's kwargs are coerced as a
    post's are, since a route captures strings and the target's reads are
    typed; a value they refuse raises ``InvalidArguments``, for the caller to
    answer.
    """
    parameters = spec.parameters()
    try:
        resolved = resolve_target(
            spec,
            principal=principal,
            arguments=coerce_flat(parameters, route_arguments(parameters, url_kwargs)),
            grant=grant,
            pool_seeds=pool_seeds,
        )
    except (NotPermitted, PrincipalUnavailable, ServiceNotFound) as refused:
        raise _denial(refused) from refused
    if isinstance(resolved, DispatchResult):
        raise Http404
    return resolved


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


def _page_form(spec: ServiceSpec, target: Any, **bound: Any) -> forms.BaseForm:
    """The page's form, bound by ``bound`` if given, showing ``target``'s values where it is a row.

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
        return form_class(**bound)
    if issubclass(form_class, forms.BaseModelForm) and isinstance(target, form_class._meta.model):
        # A copy, as for the Validator: a post builds this form before
        # dispatch, on the very row it then hands dispatch as its target, so
        # anything the form's constructor writes onto its instance - a
        # default filled in - would reach the Validator and the service as
        # the row's stored values
        # (test_the_page_form_s_writes_never_reach_the_row_dispatch_is_handed).
        return form_class(**bound, instance=copy.copy(target))
    # ``base_fields`` is set by the metaclass, which the stubs do not declare.
    fields: dict[str, forms.Field] = form_class.base_fields  # ty: ignore[unresolved-attribute]
    names = [name for name, field in fields.items() if not field.disabled]
    return form_class(**bound, initial=model_to_dict(target, fields=names))


def _form_arguments(
    form: forms.BaseForm, parameters: Parameters, url_kwargs: Mapping[str, Any]
) -> dict[str, Any]:
    """The arguments a bound form's post carries, read as the form reads it, route merged last."""
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
    return coerce_flat(parameters, {**read, **route_arguments(parameters, url_kwargs)})


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
