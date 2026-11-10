from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from django import forms
from django.core.exceptions import ImproperlyConfigured
from django.forms.formsets import TOTAL_FORM_COUNT, BaseFormSet
from django.http import HttpResponse
from django_service_specs import (
    NON_FIELD_ERRORS,
    NON_FIELD_KEYS,
    AdditionalInputRequired,
    DispatchError,
    DispatchResult,
    FormValidator,
    InvalidArguments,
    Parameters,
    ServiceError,
    ServiceSpec,
    clean_held_to_their_columns,
    coerce_flat,
    form_keywords,
    refusal_messages,
)

from django_spec_html.http.add_argument_errors import add_argument_errors
from django_spec_html.http.route_arguments import route_arguments
from django_spec_html.views.utils import (
    Served,
    SpecPageBase,
    form_values,
    refusal_tree,
    without_errors,
)


class SpecFormSetView(SpecPageBase):
    """A ``many`` ``ServiceSpec`` validated by a form, served as a formset, one form a row."""

    template_name = "django_spec_html/spec_form_set.html"
    extra: int = 1
    add_button: str | None = None

    @classmethod
    def _check_spec(cls, label: str, spec: ServiceSpec) -> None:
        """Refuse a spec whose Validator is not a ``FormValidator``, and a spec not ``many``."""
        if not isinstance(spec.validator, FormValidator):
            raise ImproperlyConfigured(
                f"{label} renders a formset of the form its spec validates each row with, so "
                f"the spec's validator must be a FormValidator; "
                f"got {type(spec.validator).__name__}."
            )
        if not spec.many:
            raise ImproperlyConfigured(
                f"{label} posts a list of rows, and its spec takes one: serve a spec that is "
                "not many from SpecFormView."
            )

    @classmethod
    def _check_page(cls, label: str, spec: ServiceSpec, initkwargs: Mapping[str, Any]) -> None:
        """Refuse a target display, ``extra``, ``add_button``, or a button under the prefix."""
        if initkwargs.get("display_target", cls.display_target):
            raise ImproperlyConfigured(
                f"{label} serves a many spec, which declares no target selector, so there is no "
                "target to display: leave display_target off."
            )
        extra = initkwargs.get("extra", cls.extra)
        if not isinstance(extra, int) or isinstance(extra, bool) or extra < 0:
            raise ImproperlyConfigured(f"{label}.extra must be a non-negative int; got {extra!r}.")
        commit = initkwargs.get("commit_button", cls.commit_button)
        add = initkwargs.get("add_button", cls.add_button)
        if add is not None:
            _check_add_button(label, add, commit)
        way_back = initkwargs.get("redirect_field_name", cls.redirect_field_name)
        if way_back is not None and way_back == add:
            raise ImproperlyConfigured(
                f"{label}.redirect_field_name and add_button both name {add!r}: the button's "
                "value would be read as the URL to return to."
            )
        prefix = f"{BaseFormSet.get_default_prefix()}-"
        pressed = "so it cannot say which button was pressed"
        named = (
            ("commit_button", commit, pressed),
            ("add_button", add, pressed),
            ("redirect_field_name", way_back, "and one would be read as the URL to return to"),
        )
        for attribute, name, why in named:
            if name is not None and name.startswith(prefix):
                raise ImproperlyConfigured(
                    f"{label}.{attribute} names {name!r}, under the formset's {prefix!r} "
                    f"prefix: every post carries the formset's keys, {why}."
                )

    def _form(
        self, spec: ServiceSpec, principal: Any, target: Any, served: Served, **bound: Any
    ) -> BaseFormSet:
        """The page's formset of the spec's form, each form built with ``form_kwargs``."""
        keywords = form_keywords(
            spec, principal, pool_seeds=served.seeds, call_context=served.call_context
        )
        formset_class = _formset_class(spec, self.extra)
        data = bound.get("data")
        if data is not None and self.add_button in data:
            bound = {**bound, "data": _one_more_form(formset_class, data)}
        return formset_class(**bound, form_kwargs=dict(keywords))

    def _arguments(
        self, form: BaseFormSet, spec: ServiceSpec, url_kwargs: Mapping[str, Any], served: Served
    ) -> Mapping[str, Any]:
        """The post's arguments: each sent form's row, as a list under ``many_argument``."""
        management = form.management_form
        if not management.is_valid():
            formset: Any = form
            missing = ", ".join(management.add_prefix(name) for name in management.errors)
            message = formset.error_messages["missing_management_form"] % {"field_names": missing}
            raise InvalidArguments({NON_FIELD_ERRORS: [message]})
        parameters = spec.parameters(call_context=served.call_context)
        rows_parameters = _rows_parameters(spec, parameters)
        rows: list[dict[str, Any]] = []
        refused_rows: dict[int, Any] = {}
        for index, row in enumerate(_sent(form)):
            try:
                rows.append(_row_arguments(row, rows_parameters))
            except InvalidArguments as refused:
                refused_rows[index] = refused.detail
        route = route_arguments(parameters, url_kwargs, route_kwargs=served.route_kwargs)
        problems: dict[Any, Any] = {}
        top: dict[str, Any] = {}
        try:
            top = coerce_flat(parameters, route)
        except InvalidArguments as refused:
            problems.update(refused.detail)
        if refused_rows:
            problems[spec.many_argument] = refused_rows
        if problems:
            raise InvalidArguments(problems)
        return {spec.many_argument: rows, **top}

    def _render_page(self, form: BaseFormSet, refreshed: bool, /, **context: Any) -> HttpResponse:
        """The page GET renders, a refreshed formset's error state, and each form's, set to none."""
        if refreshed:
            for each in form.forms:
                without_errors(each)
            _set_formset_errors(form, [])
        return self._rendered(self.get_context_data(formset=form, **context), status=200)

    def _render_refused(
        self,
        form: BaseFormSet,
        refused: DispatchError | ServiceError,
        target: Any,
        /,
        *,
        status: int,
        requested: AdditionalInputRequired | None,
        **context: Any,
    ) -> HttpResponse:
        """The page again, the refusal split onto the forms that posted each row."""
        spec = self.served_spec()
        sent = _sent(form)
        for each in form.forms:
            without_errors(each)
        if isinstance(refused, InvalidArguments) and not spec.partial:
            for row in sent:
                clean_held_to_their_columns(row)
        _set_formset_errors(form, _split(refusal_tree(refused), sent, spec.many_argument))
        return self._rendered(self.get_context_data(formset=form, **context), status=status)

    def get_success_message(self, result: DispatchResult) -> str:
        """The message a successful post sends: ``success_message`` with the rows' ``count``."""
        return self.success_message % {"count": len(result.data)}


def _check_add_button(label: str, add: Any, commit: Any) -> None:
    """Refuse an ``add_button`` that is not a button's name, or is the ``commit_button``."""
    if not isinstance(add, str) or not add:
        raise ImproperlyConfigured(
            f"{label}.add_button must be None or the name of the submit button whose post adds "
            f"a form; got {add!r}."
        )
    if commit is None:
        raise ImproperlyConfigured(
            f"{label}.add_button needs a commit_button: without one every post commits, the "
            "add button's included."
        )
    if add == commit:
        raise ImproperlyConfigured(
            f"{label}.add_button and commit_button both name {add!r}: a post that adds a form "
            "does not commit."
        )


def _formset_class(spec: ServiceSpec, extra: int) -> type[BaseFormSet]:
    """A formset of the spec's form, bounded by the spec's own list."""
    return forms.formset_factory(
        cast("FormValidator", spec.validator).form_class,
        extra=extra,
        min_num=spec.many_min_length,
        max_num=spec.many_max_length,
    )


def _one_more_form(formset_class: type[BaseFormSet], data: Any) -> Any:
    """``data`` with the management form's total one higher, within ``max_num``."""
    formset = formset_class(data=data)
    management = formset.management_form
    if not management.is_valid():
        return data
    total = min(formset.total_form_count() + 1, formset.max_num)
    added = data.copy()
    added[formset.add_prefix(TOTAL_FORM_COUNT)] = str(total)
    return added


def _rows_parameters(spec: ServiceSpec, parameters: Parameters) -> Parameters:
    """The list's rows, as the kernel checks each: the items of the ``many_argument`` parameter."""
    the_list = parameters.get(spec.many_argument)
    return cast(Parameters, cast(Any, the_list).items)


def _sent(formset: BaseFormSet) -> list[forms.BaseForm]:
    """The forms whose rows a post sends, in order: all but an unchanged extra form."""
    return [form for form in formset.forms if not (form.empty_permitted and not form.has_changed())]


def _row_arguments(form: forms.BaseForm, rows_parameters: Parameters) -> dict[str, Any]:
    """One sent form's row: its values read as the form page reads its form, coerced as a row."""
    return coerce_flat(rows_parameters, form_values(form))


def _split(tree: Mapping[Any, Any], sent: list[forms.BaseForm], many_argument: str) -> list[str]:
    """Place a refusal tree's rows on the forms that sent them."""
    left: list[str] = []
    for key, value in tree.items():
        if key == many_argument and isinstance(value, Mapping):
            for index, node in value.items():
                if isinstance(index, int) and 0 <= index < len(sent):
                    add_argument_errors(sent[index], node)
                elif isinstance(index, int):
                    left.extend(
                        f"{'.'.join((many_argument, str(index), *path))}: {message}"
                        for path, message in refusal_messages(node)
                    )
                else:
                    left.extend(message for _, message in refusal_messages(node))
            continue
        for path, message in refusal_messages(value):
            where = path if key in NON_FIELD_KEYS or key == many_argument else (str(key), *path)
            left.append(f"{'.'.join(where)}: {message}" if where else message)
    return left


def _set_formset_errors(formset: BaseFormSet, non_form: list[str]) -> None:
    """Set the formset's error state directly: each form's errors, and ``non_form``."""
    bound: Any = formset
    bound._errors = [form.errors for form in formset.forms]
    bound._non_form_errors = formset.error_class(
        non_form, error_class="nonform", renderer=bound.renderer
    )
