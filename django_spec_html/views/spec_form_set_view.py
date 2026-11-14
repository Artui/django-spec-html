from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, cast

from django import forms
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model, QuerySet
from django.forms.formsets import DELETION_FIELD_NAME, TOTAL_FORM_COUNT, BaseFormSet
from django.forms.models import BaseModelFormSet
from django.http import HttpResponse
from django.template.loader import get_template
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

_ROWS: Final = "spec-form-set-rows"
"""The ``id`` of the shipped fragment's rows, which its add button targets over htmx."""

_ROW_TEMPLATE: Final = "django_spec_html/spec_form_set_row.html"


class SpecFormSetView(SpecPageBase):
    """A ``many`` ``ServiceSpec`` validated by a form, served as a formset, one form a row.

    A ``many`` spec takes a list of rows under its ``many_argument``, and the
    kernel validates the call **per row**: the shape check and the Validator
    run on each row, and a refusal is addressed by the row's index. So the
    page is a formset of the spec's form class: the Validator's
    ``formset_class`` where it declares one, so the rule over the whole set
    is declared once, else one ``formset_factory`` builds. The spec's
    Validator must be a
    [`FormValidator`][django_service_specs.adapters.forms.form_validator.FormValidator]:

        urlpatterns = [
            path(
                "books/new/",
                SpecFormSetView.as_view(spec=add_books_spec, success_url=reverse_lazy("books")),
            ),
        ]

    **GET** renders ``template_name`` with an unbound formset under
    ``formset``, beside ``view`` and ``spec``, once the spec's class-level
    check admits the principal, as ``SpecFormView``'s page does. The formset
    shows ``extra`` blank forms, after the spec's ``many_min_length`` forms,
    which a person must fill; and never more than ``many_max_length``. The
    template renders ``{{ formset }}`` - its management form included - inside
    a POST form with its token.

    **The page's template is optional**, as on ``SpecFormView``:
    ``django_spec_html/spec_form_set.html`` by default, which this package
    ships, with its formset in ``django_spec_html/spec_form_set_fragment.html``.
    It renders the management form, the formset's non-form errors, each form
    as a fieldset of its own with its errors, through
    ``django_spec_html/spec_form_set_row.html`` inside
    ``id="spec-form-set-rows"``, and the submit button posting
    ``commit_button``'s name where one is set, then the ``add_button``, which
    skips the browser's checks since its post commits nothing, and carries
    ``hx-post``, ``hx-target="#spec-form-set-rows"`` and
    ``hx-swap="beforeend"``, inert without htmx. No target pane. Found,
    refused at ``as_view()`` and overridden as ``SpecFormView``'s is.

    **No target pane.** A ``many`` spec's rows are its forms, so
    ``display_target`` is refused at ``as_view()`` and the context carries no
    ``target_display``. A spec with no ``collection_selector_spec`` has no set
    either, and no ``target`` or ``target_count``.

    **An existing set is edited by a model formset.** When the Validator's
    ``formset_class`` is a ``BaseModelFormSet``, the page builds it with
    ``queryset=`` the collection target the spec's
    ``collection_selector_spec`` resolves on GET, one prefilled form a row,
    each posting its key, then the class's own ``extra`` blank forms; the
    management form's ``INITIAL_FORMS`` is the set's count. A post reads
    each initial form whole, its ``DELETE`` box with it, so a deleted row
    reaches the service with its key and ``DELETE`` true, and a row left
    out is left alone. ``as_view()`` refuses a model formset on a spec with
    no ``collection_selector_spec``.

    **Every form is built as the Validator builds a row's**, with what its
    ``form_kwargs`` answers, through the kernel's ``form_keywords``: asked
    once per request, with the principal, the request's seeds and its one
    call context, and handed to every form as the formset's
    ``form_kwargs``. There is no target to hand it, so the answer is the
    same for every row, and asking once is asking what each row would be
    asked. A form whose ``__init__`` takes a keyword is served.

    **POST** reads each form the formset bound, in the formset's order, as
    ``SpecFormView`` reads its one form - through its own widgets, a blank
    field absent, a disabled one not read - and sends the rows as one list
    under ``many_argument``. The route's kwargs are read and coerced once for
    the call, beside the list, never inside a row. The management form's
    keys are the formset's, and never become arguments, as the CSRF token
    never does. **An extra form left unchanged sends no row**: the form
    Django would let through empty is not sent, so a page offering three
    blank forms posts only those a person filled in, and an extra form with
    its ``DELETE`` box ticked sends none, as Django's formset skips it. The first
    ``many_min_length`` forms are sent however they are left, so the
    kernel refuses a row left blank where the spec requires it. A post whose
    management form is missing or tampered with is refused before
    dispatch, in Django's own words, at 400.

    **A refusal is split back onto the forms.** One addressed to row *i* of
    the list lands on the form that posted it, through
    [`add_argument_errors`][django_spec_html.http.add_argument_errors.add_argument_errors],
    so a dropped blank form shifts nothing; one about the list as a whole -
    its length, a message about the rows together - and one about a route
    value land on the formset's ``non_form_errors()``, a route value
    prefixed with its name as on the single page. Beside an
    ``InvalidArguments`` each sent form is validated too, under the kernel's
    ``clean_held_to_their_columns``, so every row's own checks answer in the
    same post; on a partial spec, and for any other refusal, the forms carry
    the refusal alone. Any other refusal's message is a non-form error, at the
    status ``SpecFormView`` answers it with.

    **A question is answered by its message alone.** An
    ``AdditionalInputRequired`` is checked by the kernel's
    ``check_requested_input`` as on every page, so a schema no route could
    hear answered - a key declared only inside a row among them - raises
    ``ImproperlyConfigured`` and is never rendered. Then the formset is
    re-rendered at 422 with the message as a non-form error and
    ``requested_input``, its ``message`` and ``schema``, in the context, with
    no ``fields``: a ``many`` call's top level is the list and the spec's
    ``reads``, which no form declares, and the page builds no widget from a
    schema, so a once-for-the-call confirmation is not built on this page.

    **A post that does not commit**, **the fragment wrapper**, **the
    request hooks**, **the URL a success returns to** under
    ``redirect_field_name`` and **denials** are ``SpecFormView``'s, by the
    same code:
    see that page. With ``commit_button`` set, ``add_button`` names a second
    submit button whose post is the page again with one more form, bound to
    everything posted, never past ``many_max_length``, and nothing dispatched.
    An htmx add whose ``HX-Target`` is ``spec-form-set-rows``, the shipped
    button's, is answered 200 with the new form alone - ``formset.empty_form``
    at the next index, built with the same ``form_kwargs`` and rendered by
    the row template - then the management form's ``TOTAL_FORMS`` input with
    ``hx-swap-oob="true"``; at ``many_max_length`` the answer is 204 with no
    body. Any other add, htmx included, is the formset again.

    **A success message** is ``success_message`` formatted with
    ``{"count": n}``, the number of rows the call carried, so
    ``"%(count)d books were added."`` reads the post's own count. The single
    page formats with the cleaned data, and a ``many`` call's is a list,
    which ``%`` with a name cannot read. Override ``get_success_message`` to
    read ``result.data``, the rows' cleaned data, or ``result.value``.

    ``as_view()`` refuses, when the URLconf is imported, a spec whose
    Validator is not a ``FormValidator``, a spec that is not ``many``, a
    view displaying its target, an ``extra`` that is not a non-negative int,
    an ``add_button`` that is not a button's name, that is set without a
    ``commit_button``, or that is the ``commit_button``; a
    ``redirect_field_name`` that is either button; and either button or the
    ``redirect_field_name`` named under the formset's ``form-`` prefix, which
    every post carries.

    **Sync only**, in this release. **CSRF is the host's middleware.**

    Attributes:
        spec: The ``many`` operation served. Required.
        template_name: The page's template, as for any Django template view:
            ``django_spec_html/spec_form_set.html``, the one this package
            ships, by default.
        success_url: Where a successful post redirects, read by
            ``get_success_url``.
        extra: How many blank forms the page offers beside those the spec's
            ``many_min_length`` requires: a non-negative int, 1 by default,
            checked at ``as_view()``. Django's ``formset_factory`` reads it;
            a Validator's ``formset_class`` brings its own.
        add_button: The ``name`` of the submit button whose post re-renders
            the page with one more form: ``None``, the default, offers none;
            otherwise a non-empty string, beside a ``commit_button`` it is
            not, checked at ``as_view()``.
        commit_button: The ``name`` of the submit button whose post commits,
            as on ``SpecFormView``.
        redirect_field_name: The key a post or its query carries the URL a
            success returns to under, as on ``SpecFormView``: ``None``, the
            default, reads none.
        success_message: The message a successful post sends, formatted by
            ``get_success_message`` with the rows' ``count``.
    """

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
        edits = _model_formset(spec.validator.formset_class)
        if edits is not None and spec.collection_selector_spec is None:
            raise ImproperlyConfigured(
                f"{label} renders {edits.__qualname__}, a model formset, over the rows "
                "its spec's collection_selector_spec resolves, and the spec declares none."
            )

    @classmethod
    def _check_page(cls, label: str, spec: ServiceSpec, initkwargs: Mapping[str, Any]) -> None:
        """Refuse a target display, ``extra``, ``add_button``, or a button under the prefix."""
        if initkwargs.get("display_target", cls.display_target):
            raise ImproperlyConfigured(
                f"{label} serves a many spec, whose set the formset itself shows, one form a "
                "row: leave display_target off."
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
        edits = _model_formset(formset_class)
        if edits is not None:
            bound["queryset"] = _rows(edits, target)
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
        added = self._added_row(form)
        if added is not None:
            return added
        if refreshed:
            for each in form.forms:
                without_errors(each)
            _set_formset_errors(form, [])
        return self._rendered(self.get_context_data(formset=form, **context), status=200)

    def _added_row(self, form: BaseFormSet) -> HttpResponse | None:
        """The new row and its out-of-band total for an htmx add into the rows, or ``None``."""
        request = self.request
        if self.add_button not in request.POST:
            return None
        if request.headers.get("HX-Target") != _ROWS:
            return None
        if not self.wants_fragment(request):
            return None
        before = type(form)(data=request.POST)
        if not before.management_form.is_valid():
            return None
        index = before.total_form_count()
        if form.total_form_count() <= index:
            return HttpResponse(status=204)
        row = form.empty_form
        row.prefix = form.add_prefix(index)
        template = get_template(_ROW_TEMPLATE, using=self.template_engine)
        rendered = template.render({"form": row, "number": index + 1}, request)
        total = form.management_form[TOTAL_FORM_COUNT].as_widget(attrs={"hx-swap-oob": "true"})
        return HttpResponse(f"{rendered}{total}\n", content_type=self.content_type)

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
        """The message a successful post sends: ``success_message`` with the rows' ``count``.

        ``%`` with ``{"count": n}``, ``n`` the number of rows ``result.data``
        holds, so ``"%(count)d books were added."`` says how many
        (test_the_default_message_is_formatted_with_the_rows_count). A
        ``many`` call's cleaned data is a list, which ``%`` with a name
        cannot read, so the single page's formatting would raise once the
        write had run
        (test_the_single_page_s_formatting_would_raise_once_the_write_ran).
        Override it to read the rows, under ``result.data``,
        or the result, under ``result.value``.
        """
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
    """The validator's ``formset_class``, else a formset of its form bounded by the spec's list."""
    validator = cast("FormValidator", spec.validator)
    if validator.formset_class is not None:
        return validator.formset_class
    return forms.formset_factory(
        validator.form_class,
        extra=extra,
        min_num=spec.many_min_length,
        max_num=spec.many_max_length,
    )


def _model_formset(formset_class: type[BaseFormSet] | None) -> type[BaseModelFormSet] | None:
    """``formset_class`` where it is a model formset, which edits the call's collection."""
    if formset_class is not None and issubclass(formset_class, BaseModelFormSet):
        return formset_class
    return None


def _rows(formset_class: type[BaseModelFormSet], target: Any) -> QuerySet[Any]:
    """The collection target the forms edit, or no row where the post's target went unresolved."""
    if isinstance(target, QuerySet):
        return target
    return cast("type[Model]", formset_class.model)._default_manager.none()


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
    """The forms whose rows a post sends, in order: all but an extra form unchanged or deleted."""
    return [
        form
        for form in formset.forms
        if not (form.empty_permitted and (not form.has_changed() or _marked(form)))
    ]


def _marked(form: forms.BaseForm) -> bool:
    """Whether ``form`` posts its ``DELETE`` box ticked, read before it is cleaned."""
    return DELETION_FIELD_NAME in form.fields and bool(form[DELETION_FIELD_NAME].data)


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
