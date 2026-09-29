"""``adispatch_request``: ``dispatch_request`` from async code, with nothing queried on the loop.

Each test runs with ``transaction=True``, for the reason ``test_adispatch``
gives: the executor thread's connection cannot see rows written inside the
ordinary test transaction. Any query that strays onto the event loop raises
``SynchronousOnlyOperation``, so a test passing is also evidence that nothing
did - and the principal here is the lazy object AuthenticationMiddleware
leaves on a request, whose first read *is* a query.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.utils.functional import SimpleLazyObject

from django_service_specs.authorization.grant import Grant
from django_service_specs.http.adispatch_request import adispatch_request
from django_service_specs.parameters.parameters import Parameters
from django_service_specs.specs.service_spec import ServiceSpec
from django_service_specs.validation.unknown_arguments import UnknownArguments
from tests.dispatch.utils import TENANT_SEEDS, Record, Refuse, Titled, make_user
from tests.dispatch_app.models import Note
from tests.http.utils import ASYNC_FACTORY, Conflicting, body, note_spec, notes_spec, titled_spec

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def ada() -> Any:
    user = make_user("ada")
    Note.objects.create(owner=user, title="One")
    return user


def lazy(user: Any) -> Any:
    """``request.user`` as AuthenticationMiddleware sets it: nothing read until first touched."""
    return SimpleLazyObject(lambda: get_user_model().objects.get(pk=user.pk))


def get(user: Any, path: str = "/") -> Any:
    request = ASYNC_FACTORY.get(path)
    request.user = lazy(user)
    return request


def post(user: Any, payload: Any) -> Any:
    request = ASYNC_FACTORY.post("/", data=json.dumps(payload), content_type="application/json")
    request.user = lazy(user)
    return request


class Querying(Titled):
    """Declares its parameters from a query, as a model-backed Validator may."""

    def parameters(self) -> Parameters:
        Note.objects.count()
        return super().parameters()


async def test_the_principal_is_read_off_the_event_loop(ada: Any) -> None:
    # On the loop, touching the lazy user runs its query and Django refuses
    # it; Django 4.2, the floor, has no ``request.auser()`` to await instead.
    response = await adispatch_request(notes_spec(), get(ada))
    assert (response.status_code, body(response)) == (200, [{"title": "One"}])


async def test_the_parameters_are_read_off_the_event_loop(ada: Any) -> None:
    spec = titled_spec({"id": 1}, validator=Querying())
    response = await adispatch_request(spec, post(ada, {"title": "x"}))
    assert (response.status_code, body(response)) == (200, {"id": 1})


async def test_a_deactivated_user_is_refused(ada: Any) -> None:
    await get_user_model().objects.filter(pk=ada.pk).aupdate(is_active=False)
    response = await adispatch_request(notes_spec(), get(ada))
    assert (response.status_code, body(response)) == (
        403,
        {"detail": "The acting principal is unavailable."},
    )


async def test_a_missing_row_is_404(ada: Any) -> None:
    response = await adispatch_request(note_spec(), get(ada, "/?pk=404"))
    assert (response.status_code, body(response)) == (404, {"detail": "Not found."})


async def test_a_service_with_nothing_to_present_is_204(ada: Any) -> None:
    response = await adispatch_request(titled_spec(None), post(ada, {"title": "x"}))
    assert (response.status_code, response.content) == (204, b"")


async def test_the_callers_success_status_and_route_are_used(ada: Any) -> None:
    note = await Note.objects.aget(owner=ada)
    response = await adispatch_request(
        note_spec(), get(ada), url_kwargs={"pk": str(note.pk)}, success_status=203
    )
    assert (response.status_code, body(response)) == (203, {"title": "One"})


async def test_a_refusal_is_answered(ada: Any) -> None:
    # A well-shaped call, so the shape check, which runs first, has nothing to say.
    spec = titled_spec(permissions=[Refuse()])
    response = await adispatch_request(spec, post(ada, {"title": "x"}))
    assert (response.status_code, body(response)) == (403, {"detail": "Only editors may run this."})


async def test_a_refusal_while_presenting_is_answered(ada: Any) -> None:
    spec = titled_spec({"id": 1}, presenter=Conflicting())
    response = await adispatch_request(spec, post(ada, {"title": "x"}))
    assert (response.status_code, body(response)) == (
        409,
        {"detail": "Moved while you were reading."},
    )


async def test_a_malformed_body_is_400(ada: Any) -> None:
    request = ASYNC_FACTORY.post("/", data=b"{", content_type="application/json")
    request.user = lazy(ada)
    response = await adispatch_request(titled_spec(), request)
    assert (response.status_code, body(response)) == (
        400,
        {"non_field_errors": ["The request body is not valid JSON."]},
    )


async def test_a_configuration_error_is_not_answered(ada: Any) -> None:
    with pytest.raises(ImproperlyConfigured):
        await adispatch_request(titled_spec(permissions=None), post(ada, {"title": "x"}))


async def test_the_grant_seeds_and_policy_reach_adispatch() -> None:
    # A grant is bound to the principal object by identity, so this one is
    # minted for the very object the request carries.
    user = await get_user_model().objects.acreate(username="grace")
    request = ASYNC_FACTORY.post(
        "/", data=json.dumps({"title": "x", "colour": "red"}), content_type="application/json"
    )
    request.user = user
    spec = ServiceSpec(service=Record({"id": 1}), permissions=[Refuse()], validator=Titled())
    response = await adispatch_request(
        spec,
        request,
        grant=Grant(spec, user),
        pool_seeds=TENANT_SEEDS,
        unknown_arguments=UnknownArguments.IGNORE,
    )
    assert response.status_code == 200
    assert spec.service.calls[0]["tenant"] == "tenant-of-grace"
