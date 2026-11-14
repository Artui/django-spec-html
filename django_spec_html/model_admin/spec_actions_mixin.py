from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final
from urllib.parse import urlencode

from django.contrib import admin
from django.contrib.admin.utils import model_format_dict, quote, unquote
from django.core.exceptions import (
    BadRequest,
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
from django.db.models import QuerySet
from django.db.models.options import Options
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBase, HttpResponseRedirect
from django.urls import URLPattern, path, reverse
from django_service_specs import (
    DispatchResult,
    FormValidator,
    InvalidArguments,
    NotPermitted,
    Parameter,
    Parameters,
    PrincipalUnavailable,
    ServerArguments,
    ServiceSpec,
    authorize,
    coerce_flat,
    flat_arguments,
)

from django_spec_html.http.request_principal import request_principal
from django_spec_html.http.utils import html_call_context
from django_spec_html.types.spec_action import SpecAction
from django_spec_html.views.spec_confirm_view import SpecConfirmView
from django_spec_html.views.spec_form_view import SpecFormView
from django_spec_html.views.utils import SpecPageBase

_ADMIN_URL_NAMES: Final = frozenset({"add", "change", "changelist", "delete", "history"})
"""The admin's own URL names for a model, which an action's name would shadow."""

_OWNED: Final = frozenset(
    {
        "fragment_template_name",
        "get_server_arguments",
        "route_kwargs",
        "server_arguments",
        "spec",
        "success_url",
        "template_name",
    }
)
"""The page keywords the mixin answers for, and so refuses in an action's ``page_kwargs``.

The route and the selection are how the admin reaches the target, the
templates are the admin's chrome around the shipped fragments, and a success
returns to the admin's own page. The binding is the selection's on a
changelist action, so neither kind takes one: a value the server answers for
comes from a pool seed instead."""

_SLUG: Final = re.compile(r"[-a-zA-Z0-9_]+\Z")


class SpecActionsMixin(admin.ModelAdmin):
    """A ``ModelAdmin`` offering each spec in ``spec_actions`` as an action, on the spec's page.

    Mixed in ahead of ``admin.ModelAdmin``, or of any subclass of it, with
    one ``SpecAction`` per operation:

        @admin.register(Book)
        class BookAdmin(SpecActionsMixin, admin.ModelAdmin):
            spec_actions = (
                SpecAction("withdraw", withdraw_book_spec, "Withdraw", parameter="pk"),
                SpecAction("publish", publish_books_spec, "Publish", parameter="pks"),
            )

    **Which kind of action** is the spec's target's answer. A spec with an
    ``instance_selector_spec`` is an **object action**: a link among the
    change page's object tools, to ``<object_id>/actions/<name>/``, whose
    ``object_id`` fills the selector's ``parameter`` through the page's
    ``route_kwargs``, unquoted as the admin quotes it. A spec with a
    ``collection_selector_spec`` is a **changelist action**: an entry in the
    action dropdown, which redirects to ``actions/<name>/`` with the
    selected rows' primary keys in the query string, repeated under
    ``parameter``. The page binds them to that parameter as
    ``server_arguments``, read from the query string on GET and on the post
    the page sends back to its own URL, so nothing posted stands in for the
    selection; and the collection selector still decides the set, so a key
    it does not answer is not acted on.

    **The admin's scope comes first**, as for its own actions and its change
    page: a page is served only to a principal holding the admin's view or
    change permission, the row's on an object action, and acts only on rows
    in the admin's ``get_queryset()``. An object action's row outside it is
    a 404, and a changelist action's selection is narrowed to it before it
    is bound, so a key outside it never reaches the selector. Within that
    scope the spec's own checks run, and its selector decides the set.

    **Each page is the spec's own**: ``SpecFormView`` for a spec validating
    with a ``FormValidator`` or an action whose ``page_kwargs`` carry a
    ``form_class``, ``SpecConfirmView`` for one with none, built
    once when the admin is registered, so ``as_view()`` refuses then what it
    refuses of any page. Its URL is added by ``get_urls()`` ahead of the
    admin's own, wrapped in ``admin_site.admin_view()``, so only a signed-in
    staff member the site admits reaches it; the page then admits the
    principal by the admin's permission and the spec's own checks, as on
    any route, and answers the rest with the host's 403 page. Its templates,
    ``django_spec_html/admin/spec_form.html`` and
    ``django_spec_html/admin/spec_confirm.html``, extend
    ``admin/base_site.html`` with the admin's breadcrumbs and title around
    the very fragment the shipped page includes, so an htmx request is
    answered with that fragment, as on any page. A success returns to the
    row's change page or to the changelist, with the page's
    ``success_message`` among the admin's messages.

    **Offered only to a principal both admit.** The object-tools link and
    the dropdown entry are shown only where the kernel's ``authorize``, the
    spec's class-level check, admits the request's principal, asked with
    the page's own seeds and call context, and the admin's view or change
    permission does too. The admin's model permissions say nothing about
    the spec's, so they are not the whole gate: the spec's checks are asked
    as well, and the page asks both again, the object-level ones included,
    on every request. An entry not offered is
    not in ``get_actions()``, which the changelist reads again on the post
    that runs an action, so posting its name runs nothing.

    The change page is rendered from
    ``django_spec_html/admin/change_form.html``, which extends
    ``admin/change_form.html`` and lists the links ahead of the admin's own
    tools: a ``ModelAdmin`` setting a ``change_form_template`` of its own
    extends that template to keep them.

    ``ImproperlyConfigured`` is raised when the admin is registered for a
    declaration that is not a ``SpecAction``, a name that is not a slug, is
    used twice or is one of the admin's own URL names, a label that does not
    format with the model's names, a spec that is not a
    ``ServiceSpec``, a ``many`` spec, a spec with no target, a ``parameter``
    its target selector does not read or, on a changelist action, one that
    is not an ``array``, and a ``page_kwargs`` key the mixin answers for.

    Attributes:
        spec_actions: The actions, each a ``SpecAction``.
        change_form_template: ``django_spec_html/admin/change_form.html``,
            which carries the object actions' links.
    """

    spec_actions: Sequence[SpecAction] = ()
    change_form_template = "django_spec_html/admin/change_form.html"

    def __init__(self, model: Any, admin_site: Any) -> None:
        super().__init__(model, admin_site)
        label = type(self).__name__
        names: set[str] = set()
        pages: list[_Page] = []
        for action in self.spec_actions:
            selection = _check_declaration(label, action, names, self.opts)
            names.add(action.name)
            pages.append(_built(self, action, selection, f"{label}[{action.name!r}]"))
        self._spec_pages = tuple(pages)
        # On the instance, so Django reads them as any action it is handed.
        if self.actions is not None:
            self.actions = (
                *self.actions,
                *(_redirect(self, page.action) for page in pages if page.selection is not None),
            )

    def get_urls(self) -> list[URLPattern]:
        """Every action's page at an admin URL of its own, ahead of the admin's.

        Ahead, since the admin's last pattern takes any ``<object_id>/`` and
        redirects it to the change page
        (test_each_page_is_served_at_its_own_url).
        """
        info = f"{self.opts.app_label}_{self.opts.model_name}"
        own = [
            path(
                f"actions/{page.action.name}/"
                if page.selection is not None
                else f"<path:object_id>/actions/{page.action.name}/",
                self.admin_site.admin_view(page.served),
                name=f"{info}_{page.action.name}",
            )
            for page in self._spec_pages
        ]
        return [*own, *super().get_urls()]

    def get_actions(self, request: HttpRequest, action_location: Any = None) -> dict[str, Any]:
        """The admin's actions, less each changelist action whose spec refuses this principal.

        ``action_location`` is Django 6.1's, handed on only where Django
        hands it, so a Django that has none is called as it expects, and one
        that has it reads this signature as its own.
        """
        located = {} if action_location is None else {"action_location": action_location}
        actions = super().get_actions(request, **located)
        for page in self._spec_pages:
            if (
                page.selection is not None
                and page.action.name in actions
                and not _offered(page, request)
            ):
                del actions[page.action.name]
        return actions

    def change_view(
        self,
        request: HttpRequest,
        object_id: str,
        form_url: str = "",
        extra_context: Mapping[str, Any] | None = None,
    ) -> HttpResponse:
        """The change page, its context carrying ``spec_object_actions``, the object actions.

        Each a mapping of its ``label``, formatted as the page's title is
        (test_a_label_is_formatted_alike_on_the_link_and_the_page), and its
        ``url``, for the change form's object tools, in declaration order.
        """
        info = f"{self.opts.app_label}_{self.opts.model_name}"
        offered = [
            {
                "label": _title(page.action, self.opts),
                "url": reverse(
                    f"admin:{info}_{page.action.name}",
                    args=(object_id,),
                    current_app=self.admin_site.name,
                ),
            }
            for page in self._spec_pages
            if page.selection is None and _offered(page, request)
        ]
        context = {**(extra_context or {}), "spec_object_actions": offered}
        return super().change_view(request, object_id, form_url, context)


@dataclasses.dataclass(frozen=True)
class _Page:
    """An action's page as built: the class and keywords, and the view its URL serves."""

    action: SpecAction
    selection: Parameter | None
    """The collection selector's read the selected keys fill; ``None`` on an object action."""
    page_class: type[_AdminPage]
    keywords: Mapping[str, Any]
    served: Callable[..., HttpResponseBase]


class _AdminPage(SpecPageBase):
    """A spec's page as the admin serves it: its chrome, its way back, its selection."""

    model_admin: SpecActionsMixin
    action: SpecAction
    selection: Parameter | None

    # No ``request.current_app``: the admin site's namespace already resolves its own URLs.

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """The page, behind the admin's own scope, asked as its change page asks it."""
        model_admin = self.model_admin
        object_action = self.selection is None
        row = model_admin.get_object(request, kwargs["object_id"]) if object_action else None
        if not model_admin.has_view_or_change_permission(request, row):
            raise PermissionDenied
        if object_action and row is None:
            raise Http404(f"No {model_admin.opts.verbose_name} the admin shows has that key.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """The page's context inside the admin's: the site's own, ``opts`` and ``title``."""
        model_admin = self.model_admin
        return {
            **model_admin.admin_site.each_context(self.request),
            "opts": model_admin.opts,
            "title": _title(self.action, model_admin.opts),
            "spec_action": self.action,
            "object_id": self.kwargs.get("object_id"),
            "has_view_permission": model_admin.has_view_permission(self.request),
            **super().get_context_data(**kwargs),
        }

    def get_success_url(self, result: DispatchResult) -> str:
        """The row's change page after an object action, the changelist after a changelist one."""
        opts = self.model_admin.opts
        info = f"admin:{opts.app_label}_{opts.model_name}"
        current = self.model_admin.admin_site.name
        if self.selection is not None:
            return reverse(f"{info}_changelist", current_app=current)
        return reverse(
            f"{info}_change", args=(quote(self.kwargs["object_id"]),), current_app=current
        )

    def get_server_arguments(self, request: HttpRequest) -> ServerArguments | None:
        """The selected keys a changelist action reads from its query string."""
        selection = self.selection
        if selection is None:
            return super().get_server_arguments(request)
        name = selection.name
        try:
            declared = Parameters.of(selection)
            keys = coerce_flat(declared, flat_arguments(declared, request.GET)).get(name, [])
        except InvalidArguments as refused:
            raise BadRequest(str(refused.detail)) from refused
        primary_key = self.model_admin.opts.pk
        try:
            read = [primary_key.to_python(key) for key in keys]
        except ValidationError as refused:
            raise BadRequest("; ".join(refused.messages)) from refused
        scoped = self.model_admin.get_queryset(request).filter(pk__in=read)
        shown = set(scoped.values_list("pk", flat=True))
        return ServerArguments(
            values={name: [key for key, value in zip(keys, read, strict=True) if value in shown]}
        )


class _AdminFormPage(_AdminPage, SpecFormView):
    """A ``FormValidator``'s spec as an admin page."""

    template_name = "django_spec_html/admin/spec_form.html"
    fragment_template_name = "django_spec_html/spec_form_fragment.html"


class _AdminConfirmPage(_AdminPage, SpecConfirmView):
    """A spec with no Validator as an admin page."""

    template_name = "django_spec_html/admin/spec_confirm.html"
    fragment_template_name = "django_spec_html/spec_confirm_fragment.html"


def _title(action: SpecAction, opts: Options[Any]) -> str:
    """The action's label, formatted as Django formats an action's description."""
    return action.label % model_format_dict(opts)


def _check_declaration(
    label: str, action: Any, names: set[str], opts: Options[Any]
) -> Parameter | None:
    """Refuse a declaration the admin could not serve, naming what is wrong."""
    if not isinstance(action, SpecAction):
        raise ImproperlyConfigured(
            f"{label}.spec_actions holds {action!r}, and each entry must be a SpecAction."
        )
    where = f"{label}.spec_actions[{action.name!r}]"
    if not _SLUG.match(action.name):
        raise ImproperlyConfigured(
            f"{where}: an action's name is the last segment of its URL, so it must be a slug."
        )
    if action.name in names:
        raise ImproperlyConfigured(f"{where}: another of the admin's spec_actions has the name.")
    if action.name in _ADMIN_URL_NAMES:
        raise ImproperlyConfigured(
            f"{where}: {action.name!r} is one of the admin's own URL names for the model."
        )
    try:
        _title(action, opts)
    except (ValueError, KeyError, TypeError) as refused:
        raise ImproperlyConfigured(
            f"{where}: the label {action.label!r} is formatted with the model's names, as "
            f"Django formats an action's description, and does not format ({refused}); write "
            "a literal % as %%."
        ) from refused
    spec = action.spec
    if not isinstance(spec, ServiceSpec):
        raise ImproperlyConfigured(f"{where}: the spec must be a ServiceSpec; got {spec!r}.")
    if spec.many:
        raise ImproperlyConfigured(
            f"{where}: a many spec takes a list of rows, the page SpecFormSetView serves, and "
            "an admin action acts on one row or on the selected rows: serve it from a page of "
            "its own."
        )
    selector = spec.instance_selector_spec or spec.collection_selector_spec
    if selector is None:
        raise ImproperlyConfigured(
            f"{where}: an admin action acts on one row or on the selected rows, and the spec "
            "declares neither an instance_selector_spec nor a collection_selector_spec."
        )
    read = selector.reads.get(action.parameter)
    if read is None:
        reads = ", ".join(sorted(selector.reads.names())) or "nothing"
        raise ImproperlyConfigured(
            f"{where}: the admin fills {action.parameter!r}, which the spec's target selector "
            f"does not read; it reads {reads}."
        )
    owned = sorted(_OWNED & set(action.page_kwargs))
    if owned:
        raise ImproperlyConfigured(
            f"{where}: page_kwargs sets {', '.join(owned)}, which the admin answers for."
        )
    if spec.collection_selector_spec is None:
        return None
    if read.type != "array":
        raise ImproperlyConfigured(
            f"{where}: the selected rows' keys fill {action.parameter!r}, a list, and the "
            f"collection selector reads it as {read.type!r}: declare it an array."
        )
    return read


def _built(
    model_admin: SpecActionsMixin, action: SpecAction, selection: Parameter | None, label: str
) -> _Page:
    """The action's page, built by ``as_view()`` for the spec's Validator."""
    form = isinstance(action.spec.validator, FormValidator) or "form_class" in action.page_kwargs
    base = _AdminFormPage if form else _AdminConfirmPage
    attributes = {"model_admin": model_admin, "action": action, "selection": selection}
    page_class: type[_AdminPage] = type(label, (base,), attributes)
    keywords: dict[str, Any] = {"spec": action.spec, **action.page_kwargs}
    if selection is not None:
        keywords["server_arguments"] = ServerArguments(values={action.parameter: []})
        return _Page(action, selection, page_class, keywords, page_class.as_view(**keywords))
    keywords["route_kwargs"] = {"object_id": action.parameter}
    view = page_class.as_view(**keywords)

    def unquoted(request: HttpRequest, object_id: str) -> HttpResponseBase:
        # The selector takes the key as the row holds it, not as the admin quotes it.
        return view(request, object_id=unquote(object_id))

    return _Page(action, None, page_class, keywords, unquoted)


def _redirect(model_admin: SpecActionsMixin, action: SpecAction) -> Callable[..., HttpResponse]:
    """The changelist action: a redirect to its page, the selected keys in its query string."""
    opts = model_admin.opts

    def redirect(modeladmin: Any, request: HttpRequest, queryset: QuerySet[Any]) -> HttpResponse:
        url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_{action.name}",
            current_app=model_admin.admin_site.name,
        )
        keys = [(action.parameter, str(key)) for key in queryset.values_list("pk", flat=True)]
        return HttpResponseRedirect(f"{url}?{urlencode(keys)}")

    redirect.__name__ = action.name
    return admin.action(description=action.label)(redirect)


def _offered(page: _Page, request: HttpRequest) -> bool:
    """Whether the spec's check and the admin's permission admit this principal."""
    built = page.page_class(**page.keywords)
    built.setup(request)
    try:
        authorize(
            page.action.spec,
            request_principal(request),
            pool_seeds=built.get_pool_seeds(request),
            call_context=html_call_context(request, built),
        )
    except (NotPermitted, PrincipalUnavailable):
        return False
    return bool(page.page_class.model_admin.has_view_or_change_permission(request))
