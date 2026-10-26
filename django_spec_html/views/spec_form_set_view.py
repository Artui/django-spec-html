"""``SpecFormSetView`` - a ``many`` spec validated by a Django form, served as a formset of that form."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any, cast

from django import forms
from django.contrib import messages
from django.core.exceptions import BadRequest, ImproperlyConfigured
from django.forms.formsets import TOTAL_FORM_COUNT, BaseFormSet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django_service_specs import (
    NON_FIELD_ERRORS,
    NON_FIELD_KEYS,
    UNSET,
    AdditionalInputRequired,
    DispatchError,
    DispatchResult,
    FormValidator,
    Grant,
    InvalidArguments,
    NotPermitted,
    Parameters,
    PrincipalUnavailable,
    ServiceError,
    ServiceSpec,
    authorize_scope,
    base_pool,
    check_requested_input,
    clean_held_to_their_columns,
    coerce_flat,
    dispatch,
    form_keywords,
    refusal_messages,
)

from django_spec_html.http.add_argument_errors import add_argument_errors
from django_spec_html.http.route_arguments import route_arguments
from django_spec_html.http.utils import refusal_outcome, refusal_status
from django_spec_html.types.outcome import Outcome

# The single page's readers, shared rather than copied, and private to
# ``spec_form_view.py`` until the parent moves them to ``views/utils.py``:
# ``_form_arguments`` is called once per row with no route (see
# ``_row_arguments``), and ``_without_errors`` is the same two attributes.
from django_spec_html.views.spec_form_view import _form_arguments, _without_errors

# ``_admitted``, ``_bound``, ``_denial``, ``_refusal_code`` and ``_unavailable``
# are the base's step functions, private to ``views/utils.py``, which the two
# overrides below and ``_no_target`` call as the base does; imported here only
# until those are deleted.
from django_spec_html.views.utils import (
    Served,
    SpecPageBase,
    _admitted,
    _bound,
    _denial,
    _refusal_code,
    _unavailable,
    refusal_tree,
)


class SpecFormSetView(SpecPageBase):
    """A ``many`` ``ServiceSpec`` validated by a form, served as a formset of that form, one form a row.

    A ``many`` spec takes a list of rows under its ``many_argument``, and the
    kernel validates the call **per row**: the shape check and the Validator
    run on each row, and a refusal is addressed by the row's index. So the
    page is a formset of the spec's form class, and the formset is this
    page's alone: ``FormValidator`` never sees one. The spec's Validator must
    be a
    [`FormValidator`][django_service_specs.adapters.forms.form_validator.FormValidator]:

        urlpatterns = [
            path(
                "books/new/",
                SpecFormSetView.as_view(
                    spec=add_books_spec,
                    template_name="books/book_formset.html",
                    success_url=reverse_lazy("books"),
                ),
            ),
        ]

    **GET** renders ``template_name`` with an unbound formset under
    ``formset``, beside ``view`` and ``spec``, once the spec's class-level
    check admits the principal, as ``SpecFormView``'s page does. The formset
    shows ``extra`` blank forms, after the spec's ``many_min_length`` forms,
    which a person must fill; and never more than ``many_max_length``. The
    template renders ``{{ formset }}`` - its management form included - inside
    a POST form with its token.

    **No target pane.** A ``many`` spec declares no target selector, which the
    kernel refuses when the spec is built, so there is no row or set to
    show: the context carries no ``target``, ``target_count`` or
    ``target_display``, and ``display_target`` is refused at ``as_view()``.

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
    blank forms posts only those a person filled in. The first
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
    request hooks** and **denials** are ``SpecFormView``'s, by the same code:
    see that page. With ``commit_button`` set, ``add_button`` names a second
    submit button whose post is the page again with one more form, bound to
    everything posted, never past ``many_max_length``, and nothing dispatched.

    **A success message** is ``success_message`` formatted with
    ``{"count": n}``, the number of rows the call carried, so
    ``"%(count)d books were added."`` reads the post's own count. The single
    page formats with the cleaned data, and a ``many`` call's is a list,
    which ``%`` with a name cannot read. Override ``get_success_message`` to
    read ``result.data``, the rows' cleaned data, or ``result.value``.

    ``as_view()`` refuses, when the URLconf is imported, a spec whose
    Validator is not a ``FormValidator``, a spec that is not ``many``, a
    view displaying its target, an ``extra`` that is not a non-negative int,
    and an ``add_button`` that is not a button's name, that is set without a
    ``commit_button``, or that is the ``commit_button``; and either button
    named under the formset's ``form-`` prefix, which every post carries.

    **Sync only**, in this release. **CSRF is the host's middleware.**

    Attributes:
        spec: The ``many`` operation served. Required.
        template_name: The page's template, as for any Django template view.
        success_url: Where a successful post redirects, read by
            ``get_success_url``.
        extra: How many blank forms the page offers beside those the spec's
            ``many_min_length`` requires: a non-negative int, 1 by default,
            checked at ``as_view()``. Django's ``formset_factory`` reads it.
        add_button: The ``name`` of the submit button whose post re-renders
            the page with one more form: ``None``, the default, offers none;
            otherwise a non-empty string, beside a ``commit_button`` it is
            not, checked at ``as_view()``.
        commit_button: The ``name`` of the submit button whose post commits,
            as on ``SpecFormView``.
        success_message: The message a successful post sends, formatted by
            ``get_success_message`` with the rows' ``count``.
    """

    extra: int = 1
    add_button: str | None = None

    @classmethod
    def _check_spec(cls, label: str, spec: ServiceSpec) -> None:
        """Refuse a spec whose Validator is not a ``FormValidator``, and a spec that is not ``many``."""
        if not isinstance(spec.validator, FormValidator):
            raise ImproperlyConfigured(
                f"{label} renders a formset of the form its spec validates each row with, so "
                f"the spec's validator must be a FormValidator; got {type(spec.validator).__name__}."
            )
        if not spec.many:
            raise ImproperlyConfigured(
                f"{label} posts a list of rows, and its spec takes one: serve a spec that is "
                "not many from SpecFormView."
            )

    @classmethod
    def _check_page(cls, label: str, spec: ServiceSpec, initkwargs: Mapping[str, Any]) -> None:
        """Refuse a target display, ``extra``, ``add_button``, and a button under the formset's prefix.

        ``display_target`` is refused here, after the base's display check:
        a spec whose Output cannot display a target is refused there first,
        in its words, and one that can is refused here, since a ``many`` spec
        has no target (test_displaying_the_target_is_refused).
        """
        if initkwargs.get("display_target", cls.display_target):
            raise ImproperlyConfigured(
                f"{label} serves a many spec, which declares no target selector, so there is no "
                "target to display: leave display_target off."
            )
        extra = initkwargs.get("extra", cls.extra)
        # One branch to coverage, so each condition is held by its own case
        # of test_anything_but_a_non_negative_int_is_refused: ``'1'`` and
        # ``None`` the first, ``True`` the second (a bool is an int), ``-1``
        # the third.
        if not isinstance(extra, int) or isinstance(extra, bool) or extra < 0:
            raise ImproperlyConfigured(f"{label}.extra must be a non-negative int; got {extra!r}.")
        commit = initkwargs.get("commit_button", cls.commit_button)
        add = initkwargs.get("add_button", cls.add_button)
        if add is not None:
            _check_add_button(label, add, commit)
        prefix = f"{BaseFormSet.get_default_prefix()}-"
        for attribute, name in (("commit_button", commit), ("add_button", add)):
            # Each a string by now or ``None``, which names no button: the
            # base refused a commit button that is neither, and
            # ``_check_add_button`` an add button. One branch to coverage, so
            # each condition is held by its own test:
            # test_renders_an_unbound_formset_of_extra_forms (a page naming
            # no button) and test_the_commit_button_commits (a button's name
            # outside the prefix), beside
            # test_a_button_under_the_formset_s_prefix_is_refused.
            if name is not None and name.startswith(prefix):
                raise ImproperlyConfigured(
                    f"{label}.{attribute} names {name!r}, under the formset's {prefix!r} "
                    "prefix: every post carries the formset's keys, so it cannot say which "
                    "button was pressed."
                )

    def _form(
        self, spec: ServiceSpec, principal: Any, target: Any, served: Served, **bound: Any
    ) -> BaseFormSet:
        """The page's formset of the spec's form, each form built with what ``form_kwargs`` answers.

        Bound by ``bound`` if given, and with one more form where the post
        carries ``add_button``, as ``_one_more_form`` adds it. ``target`` is
        never a row: a ``many`` spec has none.
        """
        keywords = form_keywords(
            spec, principal, pool_seeds=served.seeds, call_context=served.call_context
        )
        formset_class = _formset_class(spec, self.extra)
        data = bound.get("data")
        # One branch to coverage, so each condition is held by its own test:
        # test_an_add_button_re_renders_with_one_more_form_and_no_dispatch
        # (its GET: no post to add to) and test_a_refresh_validates_nothing
        # (a post without the button adds nothing).
        if data is not None and self.add_button in data:
            bound = {**bound, "data": _one_more_form(formset_class, data)}
        return formset_class(**bound, form_kwargs=dict(keywords))

    def _arguments(
        self, form: BaseFormSet, spec: ServiceSpec, url_kwargs: Mapping[str, Any], served: Served
    ) -> Mapping[str, Any]:
        """The post's arguments: each sent form's row, as one list under ``many_argument``, beside the route.

        Each row is read and coerced against the list's own rows, as
        ``_row_arguments`` reads it, and every row's refusal is collected
        under its index in the list before any is raised, so one refusal
        carries every row, as the kernel's does. The route's kwargs are read
        and coerced once, against the spec's top level, and merged last, so
        the route wins a clash as on the single page. Never ``coerce_flat``
        over the list: it refuses any parameter with nested rows by name.

        A management form Django refuses is refused here, in its words,
        before any row is read: a formset without one reads no form at all,
        and would send an empty list
        (test_a_post_without_the_management_form_is_refused_in_django_s_words).
        """
        management = form.management_form
        if not management.is_valid():
            # Django's message, named as its ``full_clean`` names the missing
            # keys; ``Any``, as the stubs declare no ``error_messages``.
            formset: Any = form
            missing = ", ".join(management.add_prefix(name) for name in management.errors)
            message = formset.error_messages["missing_management_form"] % {"field_names": missing}
            raise InvalidArguments({NON_FIELD_ERRORS: [message]})
        rows_parameters = _rows_parameters(spec)
        rows: list[dict[str, Any]] = []
        refused_rows: dict[int, Any] = {}
        # Collected, not raised as it comes: a row's tree raised alone would
        # read as the call's own keys, and land on no form
        # (test_a_row_its_coercion_refuses_is_refused_at_its_form).
        for index, row in enumerate(_sent(form)):
            try:
                rows.append(_row_arguments(row, rows_parameters, served))
            except InvalidArguments as refused:
                refused_rows[index] = refused.detail
        parameters = spec.parameters()
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
                _without_errors(each)
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
        """The page again, the refusal split onto the forms that posted each row and the formset's own errors.

        Every form's error state is set to none first; then, for an
        argument refusal of a whole call, each sent form is validated as the
        Validator validates a row's, under ``clean_held_to_their_columns``,
        so a row's own checks answer beside the shape check's refusal
        (test_a_row_s_own_check_answers_beside_another_row_s_shape_refusal).
        One branch to coverage, so each condition is held by its own test:
        test_a_conflict_carries_its_message_alone (an argument refusal) and
        test_a_partial_spec_s_rows_carry_the_refusal_alone (a whole call).
        Then ``_split`` places the tree. A question's ``requested_input`` is
        left as the base put it, its message and its schema: no form
        declares a top-level key, so there are no fields to reveal.
        """
        spec = self.served_spec()
        sent = _sent(form)
        for each in form.forms:
            _without_errors(each)
        if isinstance(refused, InvalidArguments) and not spec.partial:
            for row in sent:
                clean_held_to_their_columns(row)
        _set_formset_errors(form, _split(refusal_tree(refused), sent, spec.many_argument))
        return self._rendered(self.get_context_data(formset=form, **context), status=status)

    def get_success_message(self, result: DispatchResult) -> str:
        """The message a successful post sends: ``success_message`` formatted with the rows' ``count``.

        ``%`` with ``{"count": n}``, ``n`` the number of rows ``result.data``
        holds, so ``"%(count)d books were added."`` says how many
        (test_the_default_message_is_formatted_with_the_rows_count). A
        ``many`` call's cleaned data is a list, which ``%`` with a name
        cannot read, so the single page's default would raise once the write
        had committed. Override it to read the rows, under ``result.data``,
        or the result, under ``result.value``.
        """
        return self.success_message % {"count": len(result.data)}

    # The two overrides below are the base's ``_page`` and ``post``, as
    # ``views/utils.py`` has them, with the one step a ``many`` spec cannot
    # take replaced: ``_found``, which asks the kernel's ``resolve_target``,
    # and that refuses every ``many`` spec with ``ImproperlyConfigured``, as
    # ``dispatch`` refuses ``target=`` on one, ``None`` included. The parent
    # adds that guard to ``_found`` - return ``UNSET`` and the grant for a
    # ``many`` spec once the route is coerced - and the scope check of
    # nothing to ``_page`` for one, and then deletes both overrides and
    # ``_no_target``; this file's tests hold the behaviour either way.

    def _page(
        self, request: HttpRequest, url_kwargs: Mapping[str, Any], **bound: Any
    ) -> HttpResponse:
        """The base's ``_page``, with no target looked for, and the scope check asked of nothing."""
        spec = self.served_spec()
        served = self._served(request)
        principal, grant = _admitted(spec, request, url_kwargs, served)
        try:
            target, _ = _no_target(spec, principal, grant, url_kwargs, served)
        except InvalidArguments as refused:
            raise BadRequest(str(refused.detail)) from refused
        pool = base_pool(user=principal, seeds=served.seeds)
        unavailable = _unavailable(spec, target, served, pool)
        context: dict[str, Any] = {} if unavailable is None else {"unavailable": unavailable}
        form = self._form(spec, principal, target, served, **bound)
        if bound:
            context["refreshed"] = True
        context.update(self._target_context(spec, principal, target, served, pool))
        return self._render_page(form, bool(bound), **context)

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """The base's ``post``, with no target looked for: a ``many`` call is handed none.

        Nor is the scope check asked before dispatch, which asks it of
        nothing on every ``many`` call and cannot be told it ran: a grant
        saying the target was checked is honoured only beside a ``target=``,
        which a ``many`` call refuses. Asked here too, it would run twice
        (test_the_scope_check_is_asked_once_a_post).
        """
        if not self.commits(request):
            return self._page(request, kwargs, data=request.POST, files=request.FILES)
        spec = self.served_spec()
        served = self._served(request)
        principal, grant = _admitted(spec, request, kwargs, served)
        target: Any = UNSET
        form = self._form(spec, principal, target, served, data=request.POST, files=request.FILES)
        try:
            read = self._arguments(form, spec, kwargs, served)
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
            outcome = refusal_outcome(refused)
            if outcome is Outcome.DENIED:
                raise _denial(refused) from refused
            requested: AdditionalInputRequired | None = None
            if outcome is Outcome.NEEDS_INPUT:
                requested = cast("AdditionalInputRequired", refused)
                check_requested_input(spec, requested.schema, pool_seeds=served.seeds)
            context = {
                **_refusal_code(refused),
                **self._target_context(spec, principal, target, served, None),
            }
            if requested is not None:
                context["requested_input"] = {
                    "message": requested.message,
                    "schema": requested.schema,
                }
            return self._render_refused(
                form,
                refused,
                target,
                status=refusal_status(refused),
                requested=requested,
                **context,
            )
        messages.success(request, self.get_success_message(result), fail_silently=True)
        if self.wants_fragment(request):
            return self.fragment_success(result)
        return HttpResponseRedirect(self.get_success_url(result))


def _no_target(
    spec: ServiceSpec, principal: Any, grant: Grant, url_kwargs: Mapping[str, Any], served: Served
) -> tuple[Any, Grant]:
    """``UNSET`` and the grant, once the route is coerced and the scope check asked of nothing.

    The route's kwargs coerced and bound first, as ``_found`` does before it
    asks ``resolve_target``, so a route value the spec's reads refuse is a
    ``BadRequest`` on GET, as on every page
    (test_a_route_value_the_reads_refuse_is_a_bad_request). Then the scope
    check, by the kernel's ``authorize_scope``, of ``None``, as dispatch asks
    it of a ``many`` call and as the single page asks it of a create, so a
    page is never offered to a principal the post would refuse
    (test_a_principal_the_scope_check_refuses_is_never_offered_the_page).
    """
    parameters = spec.parameters()
    route = route_arguments(parameters, url_kwargs, route_kwargs=served.route_kwargs)
    arguments = _bound(spec, coerce_flat(parameters, route), served.server)
    try:
        authorize_scope(
            spec,
            principal,
            None,
            grant=grant,
            arguments=arguments,
            pool_seeds=served.seeds,
            call_context=served.call_context,
        )
    except (NotPermitted, PrincipalUnavailable) as refused:
        raise _denial(refused) from refused
    return UNSET, grant


def _check_add_button(label: str, add: Any, commit: Any) -> None:
    """Refuse an ``add_button`` that is not a button's name, has no ``commit_button`` beside it, or is it.

    Three refusals, each its own test: test_anything_but_a_button_s_name_is_refused
    (a non-empty string), test_an_add_button_without_a_commit_button_is_refused
    (with every post committing, the add button's would run the operation)
    and test_the_commit_button_is_no_add_button (one name cannot both commit
    and not).
    """
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
    """A formset of the spec's form, bounded by the spec's own list.

    ``min_num`` and ``max_num`` are the spec's ``many_min_length`` and
    ``many_max_length``, never attributes of the view, so the page cannot
    offer fewer forms than the list requires or more than it takes, and no
    second declaration can drift from the first. Neither is validated by the
    formset (``validate_min`` and ``validate_max`` are off): the list's
    length is the kernel's to refuse, in its words. The formset's
    ``absolute_max``, Django's default of ``max_num`` plus 1000, sits above
    the kernel's own default maximum, so a post never has rows dropped
    before the kernel's rule reads the list.
    """
    return forms.formset_factory(
        cast("FormValidator", spec.validator).form_class,
        extra=extra,
        min_num=spec.many_min_length,
        max_num=spec.many_max_length,
    )


def _one_more_form(formset_class: type[BaseFormSet], data: Any) -> Any:
    """``data`` with the management form's total one higher, never past the formset's ``max_num``.

    A copy, since a request's ``POST`` is immutable. A management form
    Django refuses is left as it is, for the page to render as posted
    (test_an_add_button_beside_a_broken_management_form_adds_nothing). At
    ``max_num`` the total stays where it is, since the spec's list takes no
    more (test_the_add_button_stops_at_the_list_s_maximum).
    """
    formset = formset_class(data=data)
    management = formset.management_form
    if not management.is_valid():
        return data
    # ``formset_factory`` sets ``max_num`` to Django's default where the spec
    # sets no maximum.
    total = min(formset.total_form_count() + 1, formset.max_num)
    added = data.copy()
    added[formset.add_prefix(TOTAL_FORM_COUNT)] = str(total)
    return added


def _rows_parameters(spec: ServiceSpec) -> Parameters:
    """The list's rows, as the kernel checks each: the items of the parameter ``many_argument`` names."""
    the_list = spec.parameters().get(spec.many_argument)
    return cast(Parameters, cast(Any, the_list).items)


def _sent(formset: BaseFormSet) -> list[forms.BaseForm]:
    """The forms whose rows a post sends, in the formset's order: all but an unchanged extra form.

    An extra form is one Django lets through empty, ``empty_permitted``, and
    left unchanged it is the one Django skips validating, so it is no row a
    person meant. One branch to coverage, so each condition is held by its
    own test: test_a_required_form_left_blank_is_sent_and_refused (a form
    within ``many_min_length`` is sent however it is left) and
    test_an_unchanged_extra_form_sends_no_row (only an unchanged one is
    dropped).
    """
    return [form for form in formset.forms if not (form.empty_permitted and not form.has_changed())]


def _row_arguments(
    form: forms.BaseForm, rows_parameters: Parameters, served: Served
) -> dict[str, Any]:
    """One sent form's row: its fields read and coerced as ``_form_arguments`` reads a page's form, with no route.

    ``_form_arguments`` is the single page's reader whole: the widget-reading
    loop, then ``coerce_flat`` over the read values and the route's kwargs.
    Called with the list's rows as its parameters and no route - no URL
    kwargs, and no ``route_kwargs`` to check them by - it is the loop, then
    the row coerced against its own declarations, which is what a row
    needs; the route is the call's, read once beside the list. The parent
    may split ``_form_arguments`` into the loop and the route's merge in
    ``views/utils.py``; until then the loop is called here, never copied,
    and these hold it per row
    (test_a_checked_box_is_true_and_an_unchecked_one_false,
    test_a_blank_optional_field_is_absent,
    test_a_date_in_the_form_s_input_format_is_read_as_the_form_reads_it,
    test_a_choice_s_key_is_coerced_in_its_row).
    """
    return _form_arguments(
        form, rows_parameters, {}, dataclasses.replace(served, route_kwargs=None)
    )


def _split(tree: Mapping[Any, Any], sent: list[forms.BaseForm], many_argument: str) -> list[str]:
    """Place a refusal tree's rows on the forms that sent them, and return the messages left for the formset.

    Under ``many_argument``, an ``int`` key is the row's index in the list
    the post sent, so it is the form at that place in ``sent``
    (test_a_refusal_of_row_i_lands_on_the_form_that_sent_it); any other key
    there, ``non_field_errors``, and a list in place of the mapping, are
    about the list as a whole
    (test_a_refusal_of_the_list_lands_on_the_non_form_errors). Every other key
    is about the call, prefixed with its path as ``add_argument_errors``
    prefixes a key no field names (test_a_route_value_is_a_prefixed_non_form_error).
    Each message in the order it came.
    """
    left: list[str] = []
    for key, value in tree.items():
        # One branch to coverage, so each condition is held by its own test:
        # test_a_service_s_tree_under_another_key_is_a_prefixed_non_form_error
        # (a mapping under another key is no rows) and the ``the-list-s-length``
        # case of test_a_refusal_of_the_list_lands_on_the_non_form_errors (a
        # list under the list's name is no rows either).
        if key == many_argument and isinstance(value, Mapping):
            for index, node in value.items():
                if isinstance(index, int):
                    add_argument_errors(sent[index], node)
                else:
                    left.extend(message for _, message in refusal_messages(node))
            continue
        # Unprefixed, each condition held by its own test:
        # test_a_post_without_the_management_form_is_refused_in_django_s_words
        # (a non-field key) and the ``the-list-s-length`` case above (the
        # list is the formset itself, and its name means nothing to a reader).
        for path, message in refusal_messages(value):
            where = path if key in NON_FIELD_KEYS or key == many_argument else (str(key), *path)
            left.append(f"{'.'.join(where)}: {message}" if where else message)
    return left


def _set_formset_errors(formset: BaseFormSet, non_form: list[str]) -> None:
    """Set the formset's error state directly: each form's errors as they stand, and ``non_form``.

    Never by the formset's ``full_clean``, which would validate every form
    without the guards ``clean_held_to_their_columns`` validates with, and
    ask the management form again. ``Any``, as the stubs declare neither
    attribute as assignable, nor the formset's ``renderer``.
    """
    bound: Any = formset
    bound._errors = [form.errors for form in formset.forms]
    bound._non_form_errors = formset.error_class(
        non_form, error_class="nonform", renderer=bound.renderer
    )
