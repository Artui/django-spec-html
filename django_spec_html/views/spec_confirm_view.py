from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.contrib import messages
from django.contrib.messages import MessageFailure
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import resolve_url
from django_service_specs import (
    AdditionalInputRequired,
    DispatchError,
    DispatchResult,
    FormValidator,
    InvalidArguments,
    PoolSeeds,
    ServerArguments,
    ServiceError,
    ServiceSpec,
    bind_arguments,
    coerce_flat,
    refusal_messages,
    server_owned_keys,
)

from django_spec_html.http.route_arguments import route_arguments
from django_spec_html.http.utils import route_targets
from django_spec_html.views.utils import (
    ClientRedirect,
    Served,
    SpecPageBase,
    refusal_code,
    refusal_tree,
)


class SpecConfirmView(SpecPageBase):
    """A ``ServiceSpec`` with no Validator: a page showing what it acts on, and a button."""

    template_name = "django_spec_html/spec_confirm.html"
    refusal_redirect: bool = False

    @classmethod
    def _check_spec(cls, label: str, spec: ServiceSpec) -> None:
        """Refuse a spec with a Validator, pointing a ``FormValidator``'s at ``SpecFormView``."""
        validator = spec.validator
        if validator is None:
            return
        if isinstance(validator, FormValidator):
            raise ImproperlyConfigured(
                f"{label} serves a spec with no Validator, and this one validates with "
                f"{validator.form_class.__name__}: serve it with SpecFormView, whose page is "
                "that form."
            )
        raise ImproperlyConfigured(
            f"{label} serves a spec whose every argument the server answers, so it must declare "
            f"validator=None; got {type(validator).__name__}."
        )

    @classmethod
    def _check_page(cls, label: str, spec: ServiceSpec, initkwargs: Mapping[str, Any]) -> None:
        """That the call can be made without a post: every parameter it demands is supplied."""
        _check_supplied(
            label,
            spec,
            initkwargs.get("route_kwargs", cls.route_kwargs),
            initkwargs.get("server_arguments", cls.server_arguments),
            initkwargs.get("pool_seeds", cls.pool_seeds),
        )
        if (
            initkwargs.get("refusal_redirect", cls.refusal_redirect)
            and initkwargs.get("success_url", cls.success_url) is None
            and initkwargs.get("get_refusal_url", cls.get_refusal_url)
            is SpecConfirmView.get_refusal_url
        ):
            raise ImproperlyConfigured(
                f"{label}.refusal_redirect needs a success_url, where a refused post returns "
                "without a next, or a get_refusal_url that returns one."
            )

    def _form(
        self, spec: ServiceSpec, principal: Any, target: Any, served: Served, **bound: Any
    ) -> None:
        """No form: the page renders the target and a button, and a post carries nothing."""

    def _arguments(
        self, form: None, spec: ServiceSpec, url_kwargs: Mapping[str, Any], served: Served
    ) -> Mapping[str, Any]:
        """The route's kwargs, as arguments coerced by their declarations, and nothing posted."""
        parameters = spec.parameters(call_context=served.call_context)
        route = route_arguments(parameters, url_kwargs, route_kwargs=served.route_kwargs)
        return coerce_flat(parameters, route)

    def _render_page(self, form: None, refreshed: bool, /, **context: Any) -> HttpResponse:
        """The page GET renders, and a post that does not commit, at 200: the base's context, and no form."""
        return self._rendered(self.get_context_data(**context), status=200)

    def _render_refused(
        self,
        form: None,
        refused: DispatchError | ServiceError,
        target: Any,
        /,
        *,
        status: int,
        requested: AdditionalInputRequired | None,
        **context: Any,
    ) -> HttpResponse:
        """The confirmation page again at ``status``, carrying the refusal as ``errors``."""
        context["errors"] = _errors(refused)
        return self._rendered(self.get_context_data(**context), status=status)

    def _refused_elsewhere(self, refused: DispatchError | ServiceError) -> HttpResponse | None:
        """The refusal's messages sent and the post redirected, or ``None`` without a redirect."""
        sent = [error for error in _errors(refused) if error]
        if not (self.refusal_redirect and sent):
            return None
        code = refusal_code(refused).get("code", "")
        try:
            for error in sent:
                messages.error(self.request, error, extra_tags=code)
        except MessageFailure:
            return None
        url = self.get_refusal_url(refused)
        if self.wants_fragment(self.request):
            return ClientRedirect(url)
        return HttpResponseRedirect(url)

    def get_refusal_url(self, refused: DispatchError | ServiceError) -> str:
        """Where a refused post returns: the way back, or ``success_url``."""
        return self.get_redirect_url() or resolve_url(self.success_url)

    def get_success_message(self, result: DispatchResult) -> str:
        """``success_message`` formatted with the target: ``"%(target)s was withdrawn."``."""
        return self.success_message % {"target": result.instance}


def _errors(refused: DispatchError | ServiceError) -> list[str]:
    """Every message of the refusal's tree, in its order, one at a parameter prefixed with its path."""
    return [
        f"{'.'.join(where)}: {message}" if where else message
        for where, message in refusal_messages(refusal_tree(refused))
    ]


def _check_supplied(
    label: str,
    spec: ServiceSpec,
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None,
    server: ServerArguments | None,
    seeds: PoolSeeds,
) -> None:
    """Refuse a spec demanding a parameter of the caller that only a post could supply."""
    try:
        bind_arguments(spec, {}, principal=None)
    except InvalidArguments as refused:
        demanded = frozenset(refused.detail)
    else:
        return
    supplied = (
        server_owned_keys(spec, pool_seeds=seeds)
        | frozenset({} if server is None else server.values)
        | route_targets(route_kwargs)
    )
    missing = sorted(demanded - supplied)
    if missing:
        raise ImproperlyConfigured(
            f"{label} requires {', '.join(missing)}, which no post to a confirmation carries: "
            "fill each from the route, naming it in route_kwargs, or from a pool seed, a "
            "kwargs provider or server_arguments."
        )
