from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django_service_specs import SelectorKind, SelectorSpec

from django_spec_html.views.spec_selector_view import SpecSelectorView
from tests.dispatch.utils import OPEN, make_user, notes_of
from tests.dispatch_app.models import Note
from tests.http.utils import FACTORY, signed_in
from tests.views.test_spec_selector_view import OFFERED

pytestmark = pytest.mark.django_db

WRAPPED = "spec_selector_wrapped.html"


@dataclasses.dataclass(frozen=True)
class Wrapped:
    note: Note


def wrapped_notes(*, user: Any) -> list[Wrapped]:
    return [Wrapped(note) for note in notes_of(user=user)]


def spec(**fields: Any) -> SelectorSpec:
    fields.setdefault("selector", wrapped_notes)
    fields.setdefault("row_object", "note")
    return SelectorSpec(kind=SelectorKind.LIST, permissions=OPEN, affordances=OFFERED, **fields)


@pytest.fixture
def ada() -> Any:
    return make_user("ada")


def rendered(chosen: SelectorSpec, ada: Any) -> tuple[list[str], int]:
    view = SpecSelectorView.as_view(spec=chosen, template_name=WRAPPED, display_rows=False)
    request = signed_in(FACTORY.get("/notes/"), ada)
    with CaptureQueriesContext(connection) as queries:
        response = view(request)
        response.render()
    return response.content.decode().split("\n"), len(queries)


def test_a_wrapper_row_renders_its_reason_through_the_unavailable_filter(ada: Any) -> None:
    Note.objects.create(owner=ada, title="live")
    Note.objects.create(owner=ada, title="archived", archived=True)

    lines, _ = rendered(spec(), ada)

    reasons = [line for line in lines if line.startswith(("live:", "archived:"))]
    assert reasons[0] == "live:ok"
    assert reasons[1].startswith("archived:") and reasons[1] != "archived:ok"


def test_the_queries_stay_flat_as_the_rows_grow(ada: Any) -> None:
    Note.objects.create(owner=ada, title="n0")
    _, few = rendered(spec(), ada)
    for each in range(1, 6):
        Note.objects.create(owner=ada, title=f"n{each}")

    _, many = rendered(spec(), ada)

    assert many == few


def test_a_callable_row_object_is_read_the_same_way(ada: Any) -> None:
    Note.objects.create(owner=ada, title="archived", archived=True)

    lines, _ = rendered(spec(row_object=lambda row: row.note), ada)

    assert any(line.startswith("archived:") and line != "archived:ok" for line in lines)
