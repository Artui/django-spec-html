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
    """A ``ServiceSpec`` validated by a form, served as the page a person fills that form in on."""

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
        """Refuse ``asked_fields``, or a ``commit_button`` or ``redirect_field_name`` as a field."""
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


def _check_commit_field(label: str, spec: ServiceSpec, button: Any) -> None:
    """Refuse a ``commit_button`` that is the name of one of the form's fields."""
    form_class = _form_class(spec)
    if button in _declared(form_class):
        raise ImproperlyConfigured(
            f"{label}.commit_button names {button!r}, a field {form_class.__name__} declares: a "
            "field is posted by every submit, so it cannot say which one commits."
        )


def _check_redirect_field(label: str, spec: ServiceSpec, name: Any) -> None:
    """Refuse a ``redirect_field_name`` that is the name of one of the form's fields."""
    form_class = _form_class(spec)
    if name in _declared(form_class):
        raise ImproperlyConfigured(
            f"{label}.redirect_field_name names {name!r}, a field {form_class.__name__} "
            "declares: the field's value would be read as the URL to return to."
        )


def _declared(form_class: type[forms.BaseForm]) -> dict[str, forms.Field]:
    """The fields ``form_class`` declares, by name."""
    return form_class.base_fields  # ty: ignore[unresolved-attribute]


def _check_asked_fields(
    label: str, spec: ServiceSpec, asked: Any, server: ServerArguments | None
) -> None:
    """Refuse ``asked_fields`` naming anything but optional fields of the form."""
    if not isinstance(asked, tuple):
        raise ImproperlyConfigured(
            f"{label}.asked_fields must be a tuple of the form's field names; got {asked!r}."
        )
    form_class = _form_class(spec)
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
    spec: ServiceSpec, principal: Any, target: Any, served: Served, **bound: Any
) -> forms.BaseForm:
    """The page's form, as ``_built_form`` builds it, with the binding's fields held."""
    keywords = form_keywords(
        spec,
        principal,
        pool_seeds=served.seeds,
        target=None if target is UNSET else target,
        call_context=served.call_context,
    )
    form = _built_form(spec, target, keywords, **bound)
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
    spec: ServiceSpec, target: Any, keywords: Mapping[str, Any], **bound: Any
) -> forms.BaseForm:
    """The page's form, bound by ``bound`` if given, showing ``target``'s values."""
    form_class = _form_class(spec)
    if not isinstance(target, models.Model):
        return form_class(**bound, **keywords)
    if issubclass(form_class, forms.BaseModelForm) and isinstance(target, form_class._meta.model):
        return form_class(**bound, **keywords, instance=copy.copy(target))
    fields: dict[str, forms.Field] = form_class.base_fields  # ty: ignore[unresolved-attribute]
    names = [name for name, field in fields.items() if not field.disabled]
    own = {name: value for name, value in keywords.items() if name != "initial"}
    initial = {**keywords.get("initial", {}), **model_to_dict(target, fields=names)}
    return form_class(**bound, **own, initial=initial)
