"""``route_arguments`` - the kwargs a route captured, as arguments its spec declares."""

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
    """The kwargs a route captured, as arguments, each one a parameter the spec declares.

    How ``request_arguments`` reads the route, public so that a host's own
    view reading the rest of its arguments some other way - a bound form, a
    page's own fields - merges the route by the same rule and refuses the
    same misconfiguration.

    Every URL kwarg is an argument, so a route capturing one its spec does not
    declare is the host's configuration, wrong for every request the route
    serves, rather than anything a client sent. It is refused as one - Django
    answers it 500 and shows it under ``DEBUG`` - where a client's own
    undeclared argument is a 400. The ``unknown_arguments`` policy does not
    reach it: that policy is about what a client sends, and no client can send
    a route kwarg or stop sending one.

    **``route_kwargs`` says which kwargs fill which parameters**, for a route
    whose kwargs are not all the spec's, as an ``include()`` prefix such as
    ``<org>/`` reaches every view under it:

    - ``None``, the default, is the strict rule above, unchanged.
    - A mapping from a captured name to the parameter it fills, or to
      ``None`` to drop it: ``{"note": "pk", "org": None}``. A captured name
      the mapping does not mention is still held to the strict rule, so a
      typo in the route is caught as before.
    - A tuple of names is an allow-list: each passes under its own name, and
      every other captured kwarg is dropped.

    Every target a mapping names, and every name an allow-list lists, is a
    parameter the spec declares, checked whatever the route captured: the
    setting is the host's, the same on every route the view is mounted on.
    Two kwargs filling one parameter, a rename onto a name the route also
    captures, are refused as well, since neither could win honestly. A route
    kwarg is never a server value, whatever it fills: the client's URL chose
    it, so a malformed one is the client's 400, as anything a client sent is
    (``test_a_renamed_kwarg_that_does_not_parse_is_the_clients_400``).

    Returned as a plain copy and uncoerced, so each caller merges it last,
    where the route wins a clash, and coerces it beside what the client sent:
    ``coerce_flat(parameters, {**read, **route_arguments(parameters, url_kwargs)})``,
    so one refusal carries every problem with either.

    Raises:
        ImproperlyConfigured: naming every undeclared kwarg, target or
            allowed name, in order; or a parameter more than one kwarg fills.
        TypeError: ``route_kwargs`` is a bare string, which would otherwise
            be read as a tuple of its characters.
    """
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
