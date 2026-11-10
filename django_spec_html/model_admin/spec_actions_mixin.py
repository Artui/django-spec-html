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
    """A ``ModelAdmin`` offering each spec in ``spec_actions`` as an action."""

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
        """Every action's page at an admin URL of its own, ahead of the admin's."""
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
        """The admin's actions, less each changelist action whose spec refuses this principal."""
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
        """The change page, carrying ``spec_object_actions`` as its object actions."""
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
    base = _AdminFormPage if isinstance(action.spec.validator, FormValidator) else _AdminConfirmPage
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
