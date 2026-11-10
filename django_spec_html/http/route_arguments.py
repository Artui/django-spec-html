from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import Parameters


def route_arguments(
    parameters: Parameters,
    url_kwargs: Mapping[str, Any] | None,
    *,
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """The kwargs a route captured, as arguments, each one a parameter the spec declares."""
    captured = dict(url_kwargs or {})
    declared = parameters.names()
    if route_kwargs is None:
        _check_declared(captured, declared)
        return captured
    if isinstance(route_kwargs, str):
        raise TypeError(
            f"route_kwargs is a tuple of names or a mapping, not one string: "
            f"write ({route_kwargs!r},)."
        )
    if isinstance(route_kwargs, Mapping):
        return _mapped(captured, declared, route_kwargs)
    allowed = sorted(set(route_kwargs) - declared)
    if allowed:
        raise ImproperlyConfigured(
            f"route_kwargs allows {', '.join(allowed)}, which the spec does not declare. "
            "Every name it allows passes under that name: declare each one as a parameter, "
            "or leave it out of route_kwargs."
        )
    return {name: value for name, value in captured.items() if name in route_kwargs}


def _check_declared(route: Mapping[str, Any], declared: frozenset[str]) -> None:
    """Refuse every kwarg ``route`` carries that is no parameter the spec declares."""
    undeclared = sorted(set(route) - declared)
    if undeclared:
        raise ImproperlyConfigured(
            f"The route captures {', '.join(undeclared)}, which the spec does not declare. "
            "Every URL kwarg is an argument: declare each one as a parameter, or leave it "
            "out of url_kwargs."
        )


def _mapped(
    captured: Mapping[str, Any],
    declared: frozenset[str],
    route_kwargs: Mapping[str, str | None],
) -> dict[str, Any]:
    """``captured`` under the names ``route_kwargs`` maps them to, the rest held to the strict rule."""
    targets = sorted({target for target in route_kwargs.values() if target is not None} - declared)
    if targets:
        raise ImproperlyConfigured(
            f"route_kwargs maps a kwarg to {', '.join(targets)}, which the spec does not "
            "declare. Each target is the parameter the kwarg fills: declare it, or map the "
            "kwarg to None to drop it."
        )
    _check_declared({name: None for name in captured if name not in route_kwargs}, declared)
    route: dict[str, Any] = {}
    clashing: set[str] = set()
    for name, value in captured.items():
        target = route_kwargs.get(name, name)
        if target is None:
            continue
        if target in route:
            clashing.add(target)
        route[target] = value
    if clashing:
        raise ImproperlyConfigured(
            f"The route fills {', '.join(sorted(clashing))} from more than one kwarg, so "
            "neither value could be the one it means. Map all but one of them to None, or "
            "to a parameter of its own."
        )
    return route
