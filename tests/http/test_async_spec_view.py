"""``AsyncSpecView``: ``SpecView`` with every handler async, served under ASGI."""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.utils.functional import SimpleLazyObject

from django_service_specs.http.async_spec_view import AsyncSpecView
from django_service_specs.validation.unknown_arguments import UnknownArguments
from tests.dispatch.utils import TENANT_SEEDS, make_user
from tests.dispatch_app.models import Note
from tests.http.utils import ASYNC_FACTORY, DEEP_JSON, body, note_spec, notes_spec, titled_spec

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
