"""The admin example, followed from the admin's own pages as a signed-in staff member.

``docs/admin.md`` includes its specs and its ``ModelAdmin`` from
``docs/examples/admin.py``, whose URLconf every test is served through:
Django's handler, the admin's ``admin_view``, its change form and its
changelist's action form. Where the page states an outcome, the assertion here
is that statement.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.test import Client

from tests.adapter_app.models import Author, Book, Status

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
CHANGELIST = "/admin/adapter_app/book/"


@pytest.fixture(autouse=True)
def project(settings: Any) -> None:
    settings.ROOT_URLCONF = "docs.examples.admin"
    settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "django.contrib.messages"]


def staff(username: str, *codenames: str) -> Client:
    user = get_user_model().objects.create_user(username=username, is_staff=True)
    user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def editor() -> Client:
    return staff("editor", "view_book", "change_book")


@pytest.fixture
def viewer() -> Client:
    """A staff member who may view the books, and is not an editor."""
    return staff("viewer", "view_book")


def book(title: str, status: Status = Status.PUBLISHED) -> Book:
    author, _ = Author.objects.get_or_create(name="Le Guin")
    return Book.objects.create(author=author, title=title, price=Decimal("9.99"), status=status)


def withdrawing(row: Book) -> str:
    return f"/admin/adapter_app/book/{row.pk}/actions/withdraw/"


def select(client: Client, *rows: Book) -> Any:
    """The changelist's action form, posted with ``rows`` ticked and the reprice entry chosen."""
    return client.post(
        CHANGELIST,
        {"action": "reprice", "_selected_action": [each.pk for each in rows], "index": "0"},
    )


def sent(response: Any) -> list[str]:
    return [each.message for each in get_messages(response.wsgi_request)]


class TestTheObjectAction:
    def test_the_change_page_links_to_it(self, editor: Client) -> None:
        row = book("The Lathe of Heaven")
        body = editor.get(f"{CHANGELIST}{row.pk}/change/").content.decode()
        assert f'<li><a href="{withdrawing(row)}">Withdraw</a></li>' in body

    def test_its_page_names_the_book_inside_the_admin(self, editor: Client) -> None:
        row = book("The Lathe of Heaven")
        response = editor.get(withdrawing(row))
        assert response.context_data["target_display"]["label"] == "The Lathe of Heaven"
        body = response.content.decode()
        assert '<div class="breadcrumbs">' in body
        assert "<h1>Withdraw</h1>" in body

    def test_it_withdraws_the_book_and_returns_to_its_change_page(self, editor: Client) -> None:
        row = book("The Lathe of Heaven")
        response = editor.post(withdrawing(row))
        assert (response.status_code, response["Location"]) == (
            302,
            f"{CHANGELIST}{row.pk}/change/",
        )
        assert sent(response) == ["Book withdrawn."]
        row.refresh_from_db()
        assert row.status == Status.DRAFT


class TestTheChangelistAction:
    def test_the_dropdown_offers_it(self, editor: Client) -> None:
        book("The Lathe of Heaven")
        body = editor.get(CHANGELIST).content.decode()
        assert '<option value="reprice">Reprice selected books</option>' in body

    def test_it_redirects_with_the_selection_in_the_query_string(self, editor: Client) -> None:
        first, second = book("The Lathe of Heaven"), book("The Dispossessed")
        response = select(editor, first, second)
        assert response["Location"] == (
            f"{CHANGELIST}actions/reprice/?pks={first.pk}&pks={second.pk}"
        )

    def test_it_reprices_the_published_books_among_those_ticked(self, editor: Client) -> None:
        published, draft = book("The Lathe of Heaven"), book("Draft", Status.DRAFT)
        url = select(editor, published, draft)["Location"]
        assert editor.get(url).context_data["target_count"] == 1
        response = editor.post(url, {"price": "5.00"})
        assert (response.status_code, response["Location"]) == (302, CHANGELIST)
        assert sent(response) == ["Repriced at 5.00."]
        prices = dict(Book.objects.values_list("title", "price"))
        assert prices == {"The Lathe of Heaven": Decimal("5.00"), "Draft": Decimal("9.99")}

    def test_a_key_the_parameter_refuses_is_a_400(self, editor: Client) -> None:
        assert editor.get(f"{CHANGELIST}actions/reprice/?pks=abc").status_code == 400


class TestWhoIsOffered:
    def test_a_viewer_who_is_not_an_editor_sees_neither(self, viewer: Client) -> None:
        row = book("The Lathe of Heaven")
        change = viewer.get(f"{CHANGELIST}{row.pk}/change/")
        assert change.status_code == 200
        assert withdrawing(row) not in change.content.decode()
        assert 'value="reprice"' not in viewer.get(CHANGELIST).content.decode()

    def test_reaching_the_url_directly_is_the_admin_s_403(self, viewer: Client) -> None:
        row = book("The Lathe of Heaven")
        assert viewer.get(withdrawing(row)).status_code == 403
        assert viewer.post(withdrawing(row)).status_code == 403
        row.refresh_from_db()
        assert row.status == Status.PUBLISHED

    def test_anyone_but_staff_is_sent_to_sign_in(self) -> None:
        row = book("The Lathe of Heaven")
        response = Client().get(withdrawing(row))
        assert response["Location"].startswith("/admin/login/")


def test_the_page_links_the_agent_runtime_beside_the_form_route() -> None:
    page = (ROOT / "docs" / "admin.md").read_text()
    assert "## The confirmation gates the form route only" in page
    assert "(https://artui.github.io/django-spec-agents/)" in page
    assert "(confirm.md#not-a-gate-on-an-agent-route)" in page
