"""``SpecFormView`` - a spec validated by a Django form, served as the page that form is."""

from __future__ import annotations

import datetime
import math
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any, Final, cast

from django import forms
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBase, HttpResponseRedirect
from django.shortcuts import resolve_url
from django.utils.decorators import classonlymethod
from django.views import View
from django.views.generic.base import ContextMixin, TemplateResponseMixin

from django_service_specs.adapters.forms.form_validator import FormValidator
from django_service_specs.authorization.authorize import authorize
from django_service_specs.authorization.grant import Grant
from django_service_specs.authorization.not_permitted import NotPermitted
from django_service_specs.authorization.principal_unavailable import PrincipalUnavailable
from django_service_specs.dispatch.dispatch import dispatch
from django_service_specs.dispatch.dispatch_result import DispatchResult
from django_service_specs.http.add_argument_errors import add_argument_errors
from django_service_specs.http.utils import request_principal, route_arguments
from django_service_specs.parameters.coerce_flat import coerce_flat
from django_service_specs.parameters.invalid_arguments import InvalidArguments
from django_service_specs.parameters.parameters import Parameters
from django_service_specs.parameters.utils import NON_FIELD_ERRORS
from django_service_specs.pool.pool_seeds import DEFAULT_POOL_SEEDS, PoolSeeds
from django_service_specs.services.service_conflict import ServiceConflict
from django_service_specs.services.service_error import ServiceError
from django_service_specs.services.service_not_found import ServiceNotFound
from django_service_specs.services.service_validation_error import ServiceValidationError
from django_service_specs.specs.service_spec import ServiceSpec
from django_service_specs.types.dispatch_error import DispatchError
from django_service_specs.validation.unknown_arguments import UnknownArguments

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
    [`authorize`][django_service_specs.authorization.authorize.authorize], so
    a page is never offered to a principal the post would refuse. The form is
    always unbound: showing an update's current row as its initial data is a
    page of its own, which this view does not build.

    **POST starts as GET does**: the route first, then that check, and only
    then is the form built from the post. A refused post is answered with the
    page again, and dispatch's shape check comes before its own permission
    check, so without it a principal the spec refuses would be shown the
    page - every row a choice field lists, and ``extra_context`` - by posting
    a malformed form, and a form whose constructor reads rows would read them
    for that principal. The
    [`Grant`][django_service_specs.authorization.grant.Grant] it returns goes
    to dispatch, so the class-level check runs once; the object-level check
    is still dispatch's, on the row it resolves.

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
    [`request_arguments`][django_service_specs.http.request_arguments.request_arguments].
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
      [`add_argument_errors`][django_service_specs.http.add_argument_errors.add_argument_errors].
      A service's string or list detail is about the whole form.
    - **Any other refusal** re-renders it with the message as a non-field
      error: ``ServiceConflict`` at 409, any other ``ServiceError`` at 422, any
      other ``DispatchError`` at 400. The statuses are ``error_response``'s, so
      a client reading a refused post - htmx, or Turbo, which will not render
      a failed post answered 200 - reads it the same way.
    - ``NotPermitted`` and ``PrincipalUnavailable`` raise Django's
      ``PermissionDenied``, and a not-found result or ``ServiceNotFound``
      raises ``Http404``, for the host's own 403 and 404 pages.

    **A re-rendered form carries the refusal and nothing else**: the page says
    what dispatch decided. The page's own form is bound to no row, so its own
    errors are not shown: its uniqueness check would tell an update page that
    an unchanged unique value was taken. After a shape-check refusal, then,
    the form's further checks - a length, ``clean()``, uniqueness - answer the
    next post rather than this one.

    ``as_view()`` refuses, when the URLconf is imported, a view with no
    ``ServiceSpec``, a spec whose Validator is not a ``FormValidator``, and a
    view with no ``success_url`` that does not override ``get_success_url``.

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
        """The page, with an unbound form, for a principal the spec's class check admits."""
        spec = self.served_spec()
        _admitted(spec, request, kwargs)
        return self.render_to_response(self.get_context_data(form=_form_class(spec)()))

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
        # runs once; it leaves the object-level check to dispatch.
        principal, grant = _admitted(spec, request, kwargs)
        form = _form_class(spec)(data=request.POST, files=request.FILES)
        try:
            arguments = _form_arguments(form, spec.parameters(), kwargs)
            result = dispatch(
                spec,
                principal=principal,
                arguments=arguments,
                grant=grant,
                pool_seeds=self.pool_seeds,
                unknown_arguments=self.unknown_arguments,
            )
        except (NotPermitted, PrincipalUnavailable) as refused:
            # The object-level check, on the row dispatch resolved, or either
            # raised by the operation: answered as ``error_response`` answers
            # it, whoever refused.
            raise PermissionDenied(refused.message) from refused
        except ServiceNotFound as refused:
            raise Http404(refused.message) from refused
        except InvalidArguments as refused:
            return self._render_refused(form, refused.detail, status=400)
        except ServiceValidationError as refused:
            detail = refused.detail
            # Anything but a mapping is about the whole form: a string, a list,
            # or a lazy string, which ``add_argument_errors`` reads as one message.
            tree = detail if isinstance(detail, Mapping) else {NON_FIELD_ERRORS: detail}
            return self._render_refused(form, tree, status=400)
        except ServiceConflict as refused:
            return self._render_refused(form, {NON_FIELD_ERRORS: [refused.message]}, status=409)
        except ServiceError as refused:
            return self._render_refused(form, {NON_FIELD_ERRORS: [refused.message]}, status=422)
        except DispatchError as refused:
            # Raised by nothing in this package: a refusal of the call a later
            # release may add, answered as ``error_response`` answers one.
            return self._render_refused(form, {NON_FIELD_ERRORS: [refused.message]}, status=400)
        if result.kind == "not_found":
            raise Http404
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
        would add. That form is bound to no row, so its own errors are not
        shown: its uniqueness check told an update page that an unchanged
        unique value was taken, where the Validator's form, bound to the row,
        passed it. Django's ``add_error`` needs the form cleaned first, so its
        validation runs, and its errors are cleared before the refusal is
        placed.
        """
        form.errors.clear()
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
        raise PermissionDenied(refused.message) from refused


def _form_arguments(
    form: forms.BaseForm, parameters: Parameters, url_kwargs: Mapping[str, Any]
) -> dict[str, Any]:
    """The arguments a bound form's post carries, read as the form reads it, route merged last."""
    read: dict[str, Any] = {}
    for name, field in form.fields.items():
        if field.disabled:
            continue
        value = form[name].data
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
    return typed if math.isfinite(typed) else value
