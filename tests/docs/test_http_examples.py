"""The HTTP page's examples, run through Django's own URL resolver.

``docs/http.md`` includes its views and URLconf from ``docs/examples/http.py``.
Each test here resolves a path against that URLconf, as Django's handler
would, and sends the view a request from ``RequestFactory``, so the page
cannot describe a route, a method or an answer the package does not have.
Where the page states an outcome - a status, a body, which note a clashing pk
renamed - the assertion here is that statement.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.middleware.csrf import CsrfViewMiddleware
from django.test import AsyncRequestFactory, RequestFactory
from django.urls import get_resolver

from django_service_specs import (
    ActionUnavailable,
    AdditionalInputRequired,
    DispatchError,
    InvalidArguments,
    NotPermitted,
    PrincipalUnavailable,
    ServiceConflict,
    ServiceError,
    ServiceNotFound,
    ServiceValidationError,
    UnsupportedMediaType,
    error_response,
)
from tests.dispatch_app.models import Note

ROOT = Path(__file__).resolve().parents[2]
RESOLVER = get_resolver("docs.examples.http")
FACTORY = RequestFactory()
ASYNC_FACTORY = AsyncRequestFactory()
OWNER_ONLY = {"detail": "Only the note's owner may rename it."}


def make_user(username: str, **extra: Any) -> Any:
    return get_user_model().objects.create_user(username=username, **extra)


def serve(request: Any, user: Any = None) -> Any:
    """``request`` through the example URLconf, as Django's handler would route it."""
    request.user = AnonymousUser() if user is None else user
    match = RESOLVER.resolve(request.path_info)
    return match.func(request, *match.args, **match.kwargs)


def body(response: Any) -> Any:
    return json.loads(response.content)


def as_json(method: str, path: str, payload: Any, factory: Any = FACTORY) -> Any:
    return factory.generic(method, path, data=json.dumps(payload), content_type="application/json")


@pytest.fixture
def ada() -> Any:
    return make_user("ada")


@pytest.mark.django_db
class TestFunctionView:
    def test_the_owner_renames_their_note(self, ada: Any) -> None:
        note = Note.objects.create(owner=ada, title="Draft")
        request = as_json("POST", f"/notes/{note.pk}/rename-by-hand/", {"title": "Final"})
        response = serve(request, ada)
        assert (response.status_code, body(response)) == (200, {"id": note.pk, "title": "Final"})

    def test_the_route_wins_a_clash_with_the_body(self, ada: Any) -> None:
        # The page: a body naming another pk cannot move the view off its note.
        mine = Note.objects.create(owner=ada, title="Mine")
        other = Note.objects.create(owner=ada, title="Other")
        request = as_json("POST", f"/notes/{mine.pk}/rename/", {"pk": other.pk, "title": "Final"})
        assert serve(request, ada).status_code == 200
        assert Note.objects.get(pk=mine.pk).title == "Final"
        assert Note.objects.get(pk=other.pk).title == "Other"

    def test_a_missing_note_is_404(self, ada: Any) -> None:
        response = serve(as_json("POST", "/notes/404/rename-by-hand/", {"title": "x"}), ada)
        assert (response.status_code, body(response)) == (404, {"detail": "Not found."})

    def test_anonymous_is_handed_to_the_permission_check(self) -> None:
        # Refused by the spec's own check, in its words, and never as a 401.
        response = serve(as_json("POST", "/notes/1/rename-by-hand/", {"title": "x"}))
        assert (response.status_code, body(response)) == (403, OWNER_ONLY)

    def test_a_deactivated_account_is_refused(self, ada: Any) -> None:
        note = Note.objects.create(owner=ada, title="Draft")
        ada.is_active = False
        response = serve(as_json("POST", f"/notes/{note.pk}/rename/", {"title": "x"}), ada)
        assert (response.status_code, body(response)) == (
            403,
            {"detail": "The acting principal is unavailable."},
        )


@pytest.mark.django_db
class TestSpecView:
    def test_a_read_answers_get_with_its_query_string(self, ada: Any) -> None:
        for title in ("Draft one", "Draft two", "Final"):
            Note.objects.create(owner=ada, title=title)
        response = serve(FACTORY.get("/notes/?search=draft&ordering=-title"), ada)
        assert response.status_code == 200
        assert [row["title"] for row in body(response)] == ["Draft two", "Draft one"]

    def test_a_read_refuses_post_with_the_methods_it_serves(self, ada: Any) -> None:
        response = serve(FACTORY.post("/notes/"), ada)
        assert (response.status_code, response["Allow"]) == (405, "GET, HEAD, OPTIONS")

    def test_a_write_takes_a_form_as_well_as_json(self, ada: Any) -> None:
        note = Note.objects.create(owner=ada, title="Draft")
        response = serve(FACTORY.post(f"/notes/{note.pk}/rename/", data={"title": "Final"}), ada)
        assert (response.status_code, body(response)) == (200, {"id": note.pk, "title": "Final"})

    def test_a_write_refuses_get(self, ada: Any) -> None:
        response = serve(FACTORY.get("/notes/1/rename/"), ada)
        assert (response.status_code, response["Allow"]) == (405, "POST, OPTIONS")


@pytest.mark.django_db(transaction=True)
class TestAsync:
    async def test_the_async_class_serves_its_named_method(self) -> None:
        user = await get_user_model().objects.acreate(username="ada")
        note = await Note.objects.acreate(owner=user, title="Draft")
        response = await serve(
            as_json("PATCH", f"/notes/{note.pk}/", {"title": "Final"}, ASYNC_FACTORY), user
        )
        assert (response.status_code, body(response)) == (200, {"id": note.pk, "title": "Final"})
        refused = await serve(as_json("POST", f"/notes/{note.pk}/", {}, ASYNC_FACTORY), user)
        assert (refused.status_code, refused["Allow"]) == (405, "PATCH, OPTIONS")

    async def test_the_async_function_view(self) -> None:
        user = await get_user_model().objects.acreate(username="ada")
        note = await Note.objects.acreate(owner=user, title="Draft")
        path = f"/notes/{note.pk}/arename-by-hand/"
        response = serve(as_json("POST", path, {"title": "Final"}, ASYNC_FACTORY), user)
        assert inspect.isawaitable(response)
        assert body(await response) == {"id": note.pk, "title": "Final"}


@pytest.mark.django_db
class TestCsrf:
    def _checked(self, path: str, user: Any) -> Any:
        request = FACTORY.post(path, data={"title": "Final"})
        request.user = user
        match = RESOLVER.resolve(path)
        return CsrfViewMiddleware(match.func).process_view(request, match.func, (), match.kwargs)

    def test_the_hosts_middleware_refuses_a_post_without_a_token(self, ada: Any) -> None:
        refused = self._checked("/notes/1/rename/", ada)
        assert refused is not None
        assert refused.status_code == 403

    def test_the_exempted_route_is_not_checked(self, ada: Any) -> None:
        assert self._checked("/hooks/notes/1/rename/", ada) is None


def _status_table() -> dict[str, int]:
    """The page's refusal table, as ``{class name: status}``."""
    page = (ROOT / "docs" / "http.md").read_text()
    table = page[page.index("| Refusal | Status | Body |") :]
    statuses: dict[str, int] = {}
    for line in table.splitlines()[2:]:
        if not line.startswith("|"):
            break
        refusals, status = line.split("|")[1:3]
        for name in re.findall(r"`(\w+)`", refusals):
            statuses[name] = int(status)
    return statuses


def test_the_pages_status_table_is_the_one_shipped() -> None:
    examples: list[DispatchError | ServiceError] = [
        InvalidArguments({}),
        ServiceValidationError("x"),
        NotPermitted(),
        PrincipalUnavailable(),
        ServiceNotFound(),
        ActionUnavailable(code="c"),
        ServiceConflict(),
        AdditionalInputRequired("x", schema={}),
        ServiceError(),
        UnsupportedMediaType(),
        DispatchError(),
    ]
    shipped = {type(exc).__name__: error_response(exc).status_code for exc in examples}
    assert _status_table() == shipped
