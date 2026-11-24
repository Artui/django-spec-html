from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import SelectorSpec, ServiceSpec, SpecSource, kernel_spec, route_captures


def route_arguments(
    spec: ServiceSpec | SelectorSpec | SpecSource,
    url_kwargs: Mapping[str, Any] | None,
    *,
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None = None,
    call_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The kwargs a route captured, as arguments, each one a parameter ``spec`` declares.

    How ``request_arguments`` reads the route, public so that a host's own
    view reading the rest of its arguments some other way - a bound form, a
    page's own fields - merges the route by the same rule and refuses the
    same misconfiguration.

    Every URL kwarg is an argument, except one named like a field of the
    spec's Validator: a capture holds the converter's ``to_python()`` value
    and a field reads what the caller sent, so a field-named capture is no
    argument and the field stays the caller's, by the kernel's
    [`route_captures`][django_service_specs.specs.route_captures.route_captures]
    (``test_the_field_is_the_body_s_through_a_value_changing_converter``). A
    target selector wanting that capture reads ``view.kwargs`` through its
    ``kwargs`` provider. A route capturing a name its spec does not declare
    is the host's configuration, wrong for every request the route serves,
    rather than anything a client sent. It is refused as one - Django
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
    parameter the spec declares, no Validator field and no ``many`` spec's
    list, checked whatever the route captured: the setting is the host's, the
    same on every route the view is mounted on, and no rename can make a URL
    value the caller's.
    Two kwargs filling one parameter, a rename onto a name the route also
    captures, are refused as well, since neither could win honestly. A route
    kwarg is never a server value, whatever it fills: the client's URL chose
    it, so a malformed one is the client's 400, as anything a client sent is
    (``test_a_renamed_kwarg_that_does_not_parse_is_the_clients_400``).

    Returned as a plain copy and uncoerced, so each caller merges it last,
    where the route wins a clash, and coerces it beside what the client sent:
    ``coerce_flat(parameters, {**read, **route_arguments(spec, url_kwargs)})``,
    so one refusal carries every problem with either. ``call_context``
    reaches the spec's parameters and its Validator, as in dispatch.

    Raises:
        ImproperlyConfigured: naming every undeclared kwarg, target or
            allowed name, in order; a target or allowed name that is a
            Validator field, with the kernel's message; one that is a ``many``
            spec's list, naming each capture; or a parameter more than one
            kwarg fills.
        TypeError: ``route_kwargs`` is a bare string, which would otherwise
            be read as a tuple of its characters.
    """
    if isinstance(route_kwargs, str):
        raise TypeError(
            f"route_kwargs is a tuple of names or a mapping, not one string: "
            f"write ({route_kwargs!r},)."
        )
    spec = kernel_spec(spec)
    mapped = _as_mapping(route_kwargs)
    _refuse_the_list(spec, mapped)
    filled = {target for name in url_kwargs or {} if (target := mapped.get(name, name)) is not None}
    if isinstance(route_kwargs, tuple):
        filled &= set(route_kwargs)
    _, namesakes = route_captures(spec, filled, mapped=mapped, call_context=call_context)
    captured = {name: value for name, value in (url_kwargs or {}).items() if name not in namesakes}
    declared = spec.parameters(call_context=call_context).names()
    if route_kwargs is None:
        _check_declared(captured, declared)
        return captured
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


def _as_mapping(
    route_kwargs: Mapping[str, str | None] | tuple[str, ...] | None,
) -> Mapping[str, str | None]:
    """``route_kwargs`` as the kernel reads a renaming: an allow-list maps each name to itself."""
    if isinstance(route_kwargs, Mapping):
        return route_kwargs
    return {name: name for name in route_kwargs or ()}


def _refuse_the_list(spec: ServiceSpec | SelectorSpec, mapped: Mapping[str, str | None]) -> None:
    """Refuse ``route_kwargs`` filling a ``many`` spec's list, naming each capture that would."""
    if not isinstance(spec, ServiceSpec) or not spec.many:
        return
    onto = sorted(name for name, target in mapped.items() if target == spec.many_argument)
    if onto:
        raise ImproperlyConfigured(
            f"route_kwargs fills {spec.many_argument}, the caller's list on a many spec, "
            f"from {', '.join(onto)}. The list is the body's: map each to a read, or to None "
            "to drop it."
        )


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
    """``captured`` under the names ``route_kwargs`` maps, the rest held to the strict rule."""
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
