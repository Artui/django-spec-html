"""``route_arguments`` - the kwargs a route captured, as arguments its spec declares."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import Parameters


def route_arguments(parameters: Parameters, url_kwargs: Mapping[str, Any] | None) -> dict[str, Any]:
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

    Returned as a plain copy and uncoerced, so each caller merges it last,
    where the route wins a clash, and coerces it beside what the client sent:
    ``coerce_flat(parameters, {**read, **route_arguments(parameters, url_kwargs)})``,
    so one refusal carries every problem with either.

    Raises:
        ImproperlyConfigured: naming every undeclared kwarg, in order.
    """
    route = dict(url_kwargs or {})
    undeclared = sorted(set(route) - parameters.names())
    if undeclared:
        raise ImproperlyConfigured(
            f"The route captures {', '.join(undeclared)}, which the spec does not declare. "
            "Every URL kwarg is an argument: declare each one as a parameter, or leave it "
            "out of url_kwargs."
        )
    return route
