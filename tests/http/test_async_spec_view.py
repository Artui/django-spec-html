"""``AsyncSpecView``: ``SpecView`` with every handler async, served under ASGI."""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, SynchronousOnlyOperation
from django.utils.functional import SimpleLazyObject
from django_service_specs import DEFAULT_POOL_SEEDS, PoolSeeds, ServerArguments, UnknownArguments

from django_spec_html.http.async_spec_view import AsyncSpecView
from tests.dispatch.utils import TENANT_SEEDS, make_user
from tests.dispatch_app.models import Note
from tests.http.utils import (
    ASYNC_FACTORY,
    DEEP_JSON,
    Foreign,
    SeesTenant,
    body,
    limited_spec,
    note_spec,
    notes_spec,
    reflected_tenant_spec,
    titled_spec,
)

# transaction=True for the reason test_adispatch_request gives.
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def ada() -> Any:
    user = make_user("ada")
    Note.objects.create(owner=user, title="One")
    return user


async def send(view: Any, method: str, user: Any, payload: Any = None, **url_kwargs: Any) -> Any:
    data = json.dumps(payload) if payload is not None else b""
    request = ASYNC_FACTORY.generic(method, "/", data=data, content_type="application/json")
    # The lazy user AuthenticationMiddleware leaves, whose first read is a query.
    request.user = SimpleLazyObject(lambda: get_user_model().objects.get(pk=user.pk))
    return await view(request, **url_kwargs)


def test_every_handler_is_async() -> None:
    # Django serves a view async only when all of its handlers are, and
    # refuses a class that mixes the two at as_view.
    assert AsyncSpecView.view_is_async is True
    AsyncSpecView.as_view(spec=notes_spec())


@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_a_selector_answers_get_and_head(ada: Any, method: str) -> None:
    response = await send(AsyncSpecView.as_view(spec=notes_spec()), method, ada)
    assert (response.status_code, body(response)) == (200, [{"title": "One"}])


async def test_a_service_answers_post(ada: Any) -> None:
    view = AsyncSpecView.as_view(spec=titled_spec(None))
    response = await send(view, "POST", ada, {"title": "x"})
    assert (response.status_code, response.content) == (204, b"")


async def test_a_method_it_does_not_answer_is_djangos_405(ada: Any) -> None:
    response = await send(AsyncSpecView.as_view(spec=notes_spec()), "POST", ada)
    assert (response.status_code, response["Allow"]) == (405, "GET, HEAD, OPTIONS")


async def test_options_keeps_djangos_answer(ada: Any) -> None:
    response = await send(AsyncSpecView.as_view(spec=notes_spec()), "OPTIONS", ada)
    assert (response.status_code, response["Allow"]) == (200, "GET, HEAD, OPTIONS")


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
async def test_named_methods_are_answered(ada: Any, method: str) -> None:
    view = AsyncSpecView.as_view(spec=titled_spec({"id": 1}), methods=["put", "patch", "delete"])
    response = await send(view, method, ada, {"title": "x"})
    assert (response.status_code, body(response)) == (200, {"id": 1})


async def test_the_views_settings_and_route_reach_dispatch(ada: Any) -> None:
    note = await Note.objects.aget(owner=ada)
    view = AsyncSpecView.as_view(
        spec=note_spec(),
        success_status=203,
        unknown_arguments=UnknownArguments.IGNORE,
        pool_seeds=TENANT_SEEDS,
    )
    request = ASYNC_FACTORY.get("/?colour=red")
    request.user = ada
    response = await view(request, pk=str(note.pk))
    assert (response.status_code, body(response)) == (203, {"title": "One"})


async def test_a_seed_reaches_the_service(ada: Any) -> None:
    spec = titled_spec({"id": 1})
    view = AsyncSpecView.as_view(spec=spec, pool_seeds=TENANT_SEEDS)
    await send(view, "POST", ada, {"title": "x"})
    assert spec.service.calls[0]["tenant"] == "tenant-of-ada"


async def test_a_body_nested_past_the_decoder_is_the_clients_400(ada: Any) -> None:
    # Decoded off the loop, in the executor hop, where the RecursionError
    # would otherwise reach the host as surely as on the sync path. That
    # thread can have a deeper stack than the loop's, so the body is checked
    # against the decoder there: one it decodes would be refused as "Expected
    # an object." and never reach the refusal this names.
    with pytest.raises(RecursionError):
        await sync_to_async(json.loads, thread_sensitive=True)(DEEP_JSON)
    spec = titled_spec({"id": 1})
    request = ASYNC_FACTORY.generic("POST", "/", data=DEEP_JSON, content_type="application/json")
    request.user = ada
    response = await AsyncSpecView.as_view(spec=spec)(request)
    assert (response.status_code, body(response)) == (
        400,
        {"non_field_errors": ["The request body is not valid JSON."]},
    )
    assert spec.service.calls == []


async def test_an_undeclared_url_kwarg_propagates_from_the_executor_hop(ada: Any) -> None:
    # The route is read beside the principal, off the loop, and a refusal
    # raised there must reach the host rather than be answered as a client's.
    with pytest.raises(ImproperlyConfigured, match="The route captures org,"):
        await send(AsyncSpecView.as_view(spec=notes_spec()), "GET", ada, org="acme")


def test_a_view_with_no_spec_is_refused_at_as_view() -> None:
    with pytest.raises(ImproperlyConfigured, match="AsyncSpecView.as_view"):
        AsyncSpecView.as_view()


async def test_a_selector_source_answers_get_and_refuses_post(ada: Any) -> None:
    view = AsyncSpecView.as_view(spec=Foreign(notes_spec()))

    response = await send(view, "GET", ada)
    assert (response.status_code, body(response)) == (200, [{"title": "One"}])
    assert (await send(view, "POST", ada)).status_code == 405


async def test_a_service_source_answers_post_as_its_compiled_spec(ada: Any) -> None:
    view = AsyncSpecView.as_view(spec=Foreign(titled_spec(None)))

    response = await send(view, "POST", ada, {"title": "x"})

    assert (response.status_code, response.content) == (204, b"")


async def query(view: Any, user: Any, path: str, **url_kwargs: Any) -> Any:
    request = ASYNC_FACTORY.get(path)
    request.user = SimpleLazyObject(lambda: get_user_model().objects.get(pk=user.pk))
    return await view(request, **url_kwargs)


async def test_route_kwargs_reach_the_route(ada: Any) -> None:
    class Notes(AsyncSpecView):
        spec = notes_spec()
        route_kwargs = {"org": None}

    response = await send(Notes.as_view(), "GET", ada, org="acme")
    assert (response.status_code, body(response)) == (200, [{"title": "One"}])


async def test_a_seed_the_hook_reads_off_the_request_reaches_the_check_off_the_loop(
    ada: Any,
) -> None:
    # The hook reads the lazy user eagerly, which is a query: on the loop it
    # would raise SynchronousOnlyOperation.
    check = SeesTenant()

    class Tenanted(AsyncSpecView):
        spec = notes_spec(permissions=(check,))
        pool_seeds = TENANT_SEEDS

        def get_pool_seeds(self, request: Any) -> PoolSeeds:
            name = request.user.username
            return DEFAULT_POOL_SEEDS.extend(tenant=lambda: f"by-hook-{name}")

    assert (await send(Tenanted.as_view(), "GET", ada)).status_code == 200
    assert check.tenants == ["by-hook-ada"]


async def test_a_bound_value_the_hook_queries_for_outranks_the_query(ada: Any) -> None:
    class Bound(AsyncSpecView):
        spec = note_spec()
        server_arguments = ServerArguments(values={"pk": 0})

        def get_server_arguments(self, request: Any) -> ServerArguments | None:
            return ServerArguments(values={"pk": Note.objects.get(owner=request.user).pk})

    response = await query(Bound.as_view(), ada, "/?pk=abc")
    assert (response.status_code, body(response)) == (200, {"title": "One"})


async def test_a_withheld_name_is_dropped_however_malformed(ada: Any) -> None:
    await Note.objects.acreate(owner=ada, title="Two")

    class Withheld(AsyncSpecView):
        spec = limited_spec()
        server_arguments = ServerArguments(withheld=frozenset({"limit"}))

    view = Withheld.as_view()
    response = await query(view, ada, "/?limit=abc")
    assert (response.status_code, body(response)) == (200, [{"title": "One"}, {"title": "Two"}])


def test_a_binding_that_cannot_mean_what_it_says_is_refused_at_as_view() -> None:
    class Bound(AsyncSpecView):
        spec = notes_spec()
        server_arguments = ServerArguments(values={"nope": 1})

    with pytest.raises(ImproperlyConfigured, match="ServerArguments names nope, which the"):
        Bound.as_view()


async def test_a_per_call_binding_with_other_names_is_refused(ada: Any) -> None:
    class Unbound(AsyncSpecView):
        spec = note_spec()
        server_arguments = ServerArguments(values={"pk": 1})

        def get_server_arguments(self, request: Any) -> ServerArguments | None:
            return None

    with pytest.raises(ImproperlyConfigured, match=r"Unbound.get_server_arguments\(\) binds"):
        await query(Unbound.as_view(), ada, "/?pk=1")


async def test_a_hook_on_the_loop_would_be_refused(ada: Any) -> None:
    # What the executor hop is for: the same read the hook makes, made on the
    # loop, is refused by Django.
    with pytest.raises(SynchronousOnlyOperation):
        Note.objects.get(owner=ada)


async def test_a_per_call_seed_occupying_a_bound_name_is_refused(ada: Any) -> None:
    class Seeded(AsyncSpecView):
        spec = reflected_tenant_spec()
        server_arguments = ServerArguments(values={"tenant": "server"})

        def get_pool_seeds(self, request: Any) -> PoolSeeds:
            return self.pool_seeds.extend(tenant=lambda: "seeded")

    with pytest.raises(ImproperlyConfigured) as refused:
        await query(Seeded.as_view(), ada, "/")
    assert str(refused.value).startswith(
        "Seeded.get_pool_seeds() adds tenant, which server_arguments binds or withholds, "
        "and the check when the view was built never saw a seed added per request"
    )


async def test_a_route_capturing_a_bound_name_is_refused(ada: Any) -> None:
    class Bound(AsyncSpecView):
        spec = note_spec()
        server_arguments = ServerArguments(values={"pk": 1})

    with pytest.raises(ImproperlyConfigured, match="The route captures pk, which server_arguments"):
        await query(Bound.as_view(), ada, "/", pk="1")


def test_route_kwargs_are_checked_at_as_view() -> None:
    class Mistyped(AsyncSpecView):
        spec = notes_spec()
        route_kwargs = {"org": "team"}

    with pytest.raises(ImproperlyConfigured, match="maps a kwarg to team, which the spec"):
        Mistyped.as_view()


@pytest.mark.parametrize("hook", ["get_pool_seeds", "get_server_arguments"])
def test_an_async_hook_is_refused_at_as_view(hook: str) -> None:
    # The view is async, its hooks are not: they run in an executor hop.
    async def asynchronous(self: Any, request: Any) -> Any: ...

    view = type("Hooked", (AsyncSpecView,), {"spec": notes_spec(), hook: asynchronous})
    with pytest.raises(ImproperlyConfigured) as refused:
        view.as_view()
    assert str(refused.value).startswith(f"Hooked.{hook} is async")
