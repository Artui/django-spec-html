"""``SpecActionsMixin``: specs' pages served as a ``ModelAdmin``'s actions.

Every request goes through Django's handler and the admin's own stack -
``admin_view``, the changelist's action form, the change form - as a signed-in
client, since what the admin offers and refuses is decided there, and a call
to an action's function would cross none of it. The module is its own
URLconf: two admin sites over one model, the second deployed last, so a link
the chrome resolves without naming its site resolves into the wrong one.
"""

from __future__ import annotations

import re
import warnings
from typing import Any

import pytest
from django import forms
from django.contrib import admin, messages
from django.contrib.admin.utils import quote
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from django.test import Client
from django.urls import path, reverse
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    AdditionalInputRequired,
    FormValidator,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_html.model_admin.spec_actions_mixin import SpecActionsMixin
from django_spec_html.types.spec_action import SpecAction
from django_spec_html.views.utils import SpecPageBase
from tests.dispatch.utils import PK, note_by_pk
from tests.dispatch_app.models import Note

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
PKS = Parameters.of(Parameter("pks", "array", items="integer", required=True))


class Editors(PermissionCheck):
    """Admits a principal holding the change permission on notes: the spec's own rule, not the admin's."""

    message = "Only an editor may do this."

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return bool(principal.has_perm("dispatch_app.change_note"))


def archive(*, instance: Note) -> Note:
    instance.archived = True
    instance.save(update_fields=["archived"])
    return instance


def archive_all(*, collection: QuerySet[Note]) -> int:
    return collection.update(archived=True)


def own_notes(*, pks: list[int], user: Any) -> QuerySet[Note]:
    # The selector decides the set: the selected keys, narrowed to the
    # principal's own rows.
    return Note.objects.filter(pk__in=pks, owner=user)


class TitleForm(forms.Form):
    title = forms.CharField(max_length=100)
    confirmed = forms.BooleanField(required=False)


def retitle(*, instance: Note, data: Any) -> Note:
    if data["title"] == "Danger" and not data.get("confirmed"):
        raise AdditionalInputRequired(
            "Retitle it Danger?", schema={"confirmed": {"type": "boolean"}}
        )
    instance.title = data["title"]
    instance.save(update_fields=["title"])
    return instance


def retitle_all(*, collection: QuerySet[Note], data: Any) -> int:
    return collection.update(title=data["title"])


archive_spec = ServiceSpec(
    service=archive,
    permissions=[Editors()],
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=note_by_pk, reads=PK),
)
retitle_spec = ServiceSpec(
    service=retitle,
    # Open to every staff member, so the page is reached by one the admin
    # itself shows nothing of the model.
    permissions=[Unrestricted()],
    validator=FormValidator(TitleForm),
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=note_by_pk, reads=PK),
)
archive_all_spec = ServiceSpec(
    service=archive_all,
    permissions=[Editors()],
    collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=own_notes, reads=PKS),
)
retitle_all_spec = ServiceSpec(
    service=retitle_all,
    permissions=[Editors()],
    validator=FormValidator(TitleForm),
    collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=own_notes, reads=PKS),
)

ACTIONS = (
    SpecAction(
        "archive", archive_spec, "Archive", "pk", {"success_message": "%(target)s archived."}
    ),
    SpecAction(
        "retitle",
        retitle_spec,
        "Retitle",
        "pk",
        {"asked_fields": ("confirmed",), "success_message": "Retitled %(title)s."},
    ),
    SpecAction(
        "archive_all",
        archive_all_spec,
        "Archive selected %(verbose_name_plural)s",
        "pks",
        {"success_message": "Archived."},
    ),
    SpecAction("retitle_all", retitle_all_spec, "Retitle selected", "pks"),
)


class NoteAdmin(SpecActionsMixin, admin.ModelAdmin):
    spec_actions = ACTIONS


site = admin.AdminSite(name="spec_admin")
site.register(Note, NoteAdmin)
other_site = admin.AdminSite(name="other_admin")
other_site.register(Note, NoteAdmin)


def note_by_title(*, key: str) -> QuerySet[Note]:
    return Note.objects.filter(title=key)


by_title_spec = ServiceSpec(
    service=archive,
    permissions=[Unrestricted()],
    instance_selector_spec=SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=note_by_title,
        reads=Parameters.of(Parameter("key", "string", required=True)),
    ),
)


capped_spec = ServiceSpec(
    service=archive_all,
    permissions=[Unrestricted()],
    collection_selector_spec=SelectorSpec(
        kind=SelectorKind.LIST,
        selector=own_notes,
        reads=Parameters.of(Parameter("pks", "array", items="integer", max_length=1)),
    ),
)


class KeyedAdmin(SpecActionsMixin, admin.ModelAdmin):
    spec_actions = (
        SpecAction("by_title", by_title_spec, "By title", "key"),
        SpecAction("capped", capped_spec, "Capped", "pks"),
    )


keyed_site = admin.AdminSite(name="keyed_admin")
keyed_site.register(Note, KeyedAdmin)

urlpatterns = [
    path("other/", other_site.urls),
    path("keyed/", keyed_site.urls),
    path("admin/", site.urls),
]


@pytest.fixture(autouse=True)
def project(settings: Any) -> None:
    """The admin as a project serves it: this URLconf, and the messages app the admin lists."""
    settings.ROOT_URLCONF = __name__
    settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "django.contrib.messages"]


def staff(username: str, *codenames: str) -> Any:
    user = get_user_model().objects.create_user(username=username, is_staff=True)
    user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    return user


@pytest.fixture
def editor() -> Any:
    return staff("editor", "view_note", "change_note")


@pytest.fixture
def viewer() -> Any:
    """A staff member the admin shows notes to, and the spec's ``Editors`` refuses."""
    return staff("viewer", "view_note")


def client(user: Any) -> Client:
    signed_in = Client()
    signed_in.force_login(user)
    return signed_in


@pytest.fixture
def note(editor: Any) -> Note:
    return Note.objects.create(owner=editor, title="Buy milk")


def page(name: str, note: Note | None = None, prefix: str = "admin") -> str:
    """The URL of the action ``name`` on ``prefix``'s site, of ``note`` for an object action."""
    if note is None:
        return f"/{prefix}/dispatch_app/note/actions/{name}/"
    return f"/{prefix}/dispatch_app/note/{note.pk}/actions/{name}/"


def change_page(note: Note) -> str:
    return f"/admin/dispatch_app/note/{note.pk}/change/"


CHANGELIST = "/admin/dispatch_app/note/"


def sent(response: Any) -> list[tuple[int, str]]:
    return [(each.level, each.message) for each in get_messages(response.wsgi_request)]


def run_action(user: Any, name: str, *notes: Note) -> Any:
    """Post the changelist's action form, as the admin's own page posts it."""
    return client(user).post(
        CHANGELIST,
        {"action": name, "_selected_action": [each.pk for each in notes], "index": "0"},
    )


class TestTheUrls:
    def test_each_page_is_served_at_its_own_url(self, editor: Any, note: Note) -> None:
        # Ahead of the admin's catch-all, which would redirect any
        # ``<object_id>/...`` to the change page.
        assert reverse("admin:dispatch_app_note_archive", args=(note.pk,)) == page("archive", note)
        assert reverse("admin:dispatch_app_note_archive_all") == page("archive_all")
        for url in (page("archive", note), page("retitle", note), page("archive_all")):
            assert client(editor).get(url).status_code == 200

    @pytest.mark.parametrize("name", ["archive", "archive_all"])
    def test_a_member_who_is_not_staff_is_sent_to_sign_in(self, note: Note, name: str) -> None:
        outsider = get_user_model().objects.create_user(username="outsider")
        outsider.user_permissions.set(Permission.objects.filter(codename="change_note"))
        url = page(name, note if name == "archive" else None)
        for user in (outsider, None):
            visitor = Client() if user is None else client(user)
            response = visitor.get(url)
            assert response.status_code == 302
            assert response["Location"].startswith("/admin/login/?next=")
        assert Note.objects.get().archived is False

    def test_a_quoted_key_reaches_the_selector_unquoted(self, editor: Any) -> None:
        # A key holding a character the admin escapes in its URLs, read by a
        # selector of a string, so only the key unquoted finds the row.
        keyed = Note.objects.create(owner=editor, title="a/b")
        url = f"/keyed/dispatch_app/note/{quote('a/b')}/actions/by_title/"
        assert url == "/keyed/dispatch_app/note/a_2Fb/actions/by_title/"
        assert client(editor).get(url).context_data["target"] == keyed


class TestTheObjectToolsLink:
    def test_an_admitted_principal_is_offered_the_link(self, editor: Any, note: Note) -> None:
        body = client(editor).get(change_page(note)).content.decode()
        assert f'<li><a href="{page("archive", note)}">Archive</a></li>' in body
        assert f'<li><a href="{page("retitle", note)}">Retitle</a></li>' in body

    def test_a_refused_principal_is_offered_no_link(self, viewer: Any, note: Note) -> None:
        response = client(viewer).get(change_page(note))
        body = response.content.decode()
        assert response.status_code == 200
        assert page("archive", note) not in body
        # Only the spec it is refused: the open one is still offered.
        assert response.context_data["spec_object_actions"] == [
            {"label": "Retitle", "url": page("retitle", note)}
        ]

    def test_a_changelist_action_is_no_object_tool(self, editor: Any, note: Note) -> None:
        listed = client(editor).get(change_page(note)).context_data["spec_object_actions"]
        assert [each["label"] for each in listed] == ["Archive", "Retitle"]

    def test_a_refused_principal_hitting_the_url_gets_the_page_s_denial(
        self, viewer: Any, note: Note
    ) -> None:
        assert client(viewer).get(page("archive", note)).status_code == 403
        assert client(viewer).post(page("archive", note)).status_code == 403
        assert Note.objects.get().archived is False


class TestTheDropdownEntry:
    def test_an_admitted_principal_is_offered_the_entry(self, editor: Any, note: Note) -> None:
        body = client(editor).get(CHANGELIST).content.decode()
        assert '<option value="archive_all">Archive selected notes</option>' in body
        assert '<option value="retitle_all">Retitle selected</option>' in body

    def test_a_refused_principal_is_offered_no_entry(self, viewer: Any, note: Note) -> None:
        body = client(viewer).get(CHANGELIST).content.decode()
        assert body.count("<option") == 0
        assert "archive_all" not in body

    def test_posting_an_entry_not_offered_runs_nothing(self, viewer: Any, note: Note) -> None:
        # The changelist again, with no action to run: offered, the entry
        # would redirect to its page.
        response = run_action(viewer, "archive_all", note)
        assert (response.status_code, response.has_header("Location")) == (200, False)
        assert Note.objects.get().archived is False

    def test_a_host_action_sharing_an_object_action_s_name_is_kept(
        self, viewer: Any, note: Note
    ) -> None:
        def archive(modeladmin: Any, request: Any, queryset: Any) -> None: ...

        class SharingAName(SpecActionsMixin, admin.ModelAdmin):
            actions = [archive]
            spec_actions = (ACTIONS[0],)

        request = client(viewer).get(CHANGELIST).wsgi_request
        assert "archive" in SharingAName(Note, admin.AdminSite()).get_actions(request)

    def test_actions_turned_off_offer_none(self, viewer: Any, editor: Any, note: Note) -> None:
        class Off(SpecActionsMixin, admin.ModelAdmin):
            actions = None
            spec_actions = ACTIONS

        off = Off(Note, admin.AdminSite())
        for user in (editor, viewer):
            assert off.get_actions(client(user).get(CHANGELIST).wsgi_request) == {}

    def test_django_reads_get_actions_as_its_own(self, editor: Any, note: Note) -> None:
        # From Django 6.1 an override without ``action_location`` is warned
        # about, and turns the change form's actions off.
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client(editor).get(change_page(note))
            client(editor).get(CHANGELIST)
        assert [str(each.message) for each in caught if "get_actions" in str(each.message)] == []


class TestThePage:
    def test_it_renders_inside_the_admin_with_the_target(self, editor: Any, note: Note) -> None:
        response = client(editor).get(page("archive", note))
        context = response.context_data
        assert (context["target"], context["opts"], context["title"]) == (
            note,
            Note._meta,
            "Archive",
        )
        assert context["site_header"] == site.site_header
        body = response.content.decode()
        assert "<title>Archive | Django site admin</title>" in body
        assert "<h1>Archive</h1>" in body
        assert '<div id="content-main">' in body
        assert f'<a href="{change_page(note)}">{note}</a>' in body
        assert '<button type="submit">Confirm</button>' in body

    def test_the_breadcrumbs_link_only_what_the_admin_shows(self, note: Note) -> None:
        # Admitted by the open spec, and shown nothing by the admin.
        response = client(staff("stranger")).get(page("retitle", note))
        body = response.content.decode()
        assert response.status_code == 200
        assert response.context_data["has_view_permission"] is False
        assert CHANGELIST not in body
        assert change_page(note) not in body

    def test_the_chrome_links_into_the_admin_site_serving_it(self, editor: Any, note: Note) -> None:
        body = client(editor).get(page("archive", note, prefix="other")).content.decode()
        assert '<a href="/other/">Home</a>' in body
        assert '<a href="/other/dispatch_app/note/">Notes</a>' in body

    def test_a_valid_post_returns_to_the_change_page_with_the_message(
        self, editor: Any, note: Note
    ) -> None:
        response = client(editor).post(page("archive", note))
        assert (response.status_code, response["Location"]) == (302, change_page(note))
        assert sent(response) == [(messages.SUCCESS, f"{note} archived.")]
        assert Note.objects.get().archived is True

    def test_the_message_is_shown_on_the_admin_page_it_returns_to(
        self, editor: Any, note: Note
    ) -> None:
        response = client(editor).post(page("retitle", note), {"title": "Buy bread"}, follow=True)
        assert response.redirect_chain == [(change_page(note), 302)]
        assert '<li class="success">Retitled Buy bread.</li>' in response.content.decode()

    def test_a_refused_post_is_the_admin_page_again(self, editor: Any, note: Note) -> None:
        response = client(editor).post(page("retitle", note), {"title": ""})
        assert response.status_code == 400
        assert response.context_data["form"].errors == {"title": ["This field is required."]}
        assert "<h1>Retitle</h1>" in response.content.decode()
        assert Note.objects.get().title == "Buy milk"

    def test_an_htmx_request_gets_the_fragment(self, editor: Any, note: Note) -> None:
        response = client(editor).get(page("retitle", note), **HTMX)
        body = response.content.decode()
        assert response.status_code == 200
        assert '<form method="post">' in body
        assert "breadcrumbs" not in body
        assert "<html" not in body

    def test_an_htmx_success_is_sent_back_to_the_change_page(self, editor: Any, note: Note) -> None:
        response = client(editor).post(page("retitle", note), {"title": "Hi"}, **HTMX)
        assert (response.status_code, response["HX-Redirect"]) == (200, change_page(note))

    def test_the_question_is_asked_and_answered_on_the_admin_page(
        self, editor: Any, note: Note
    ) -> None:
        asked = client(editor).post(page("retitle", note), {"title": "Danger"})
        assert asked.status_code == 422
        assert asked.context_data["requested_input"]["message"] == "Retitle it Danger?"
        body = asked.content.decode()
        assert "<h1>Retitle</h1>" in body
        assert 'name="confirmed"' in body
        assert Note.objects.get().title == "Buy milk"
        answered = client(editor).post(
            page("retitle", note), {"title": "Danger", "confirmed": "on"}
        )
        assert (answered.status_code, answered["Location"]) == (302, change_page(note))
        assert Note.objects.get().title == "Danger"

    def test_the_offer_is_asked_with_the_page_s_seeds_and_context(
        self, editor: Any, note: Note
    ) -> None:
        handed: list[tuple[Any, Any, Any]] = []

        class Seeded(PermissionCheck):
            def has_permission(
                self, principal: Any, spec: Any, *, desk: str, request: Any, view: Any
            ) -> bool:
                handed.append((desk, request, view))
                return True

        spec = ServiceSpec(
            service=archive_all,
            permissions=[Seeded()],
            collection_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST, selector=own_notes, reads=PKS
            ),
        )
        seeds = DEFAULT_POOL_SEEDS.extend(desk=lambda *, user: f"desk-of-{user.username}")

        class Seeding(SpecActionsMixin, admin.ModelAdmin):
            spec_actions = (SpecAction("seeded", spec, "Seeded", "pks", {"pool_seeds": seeds}),)

        request = client(editor).get(CHANGELIST).wsgi_request
        assert "seeded" in Seeding(Note, admin.AdminSite()).get_actions(request)
        desk, asked_with, view = handed[0]
        assert (desk, asked_with) == ("desk-of-editor", request)
        assert isinstance(view, SpecPageBase)
        assert view.request is request


class TestTheChangelistAction:
    @pytest.fixture
    def theirs(self) -> Note:
        return Note.objects.create(owner=staff("someone"), title="Not yours")

    def test_it_redirects_to_the_page_carrying_the_selection(
        self, editor: Any, note: Note, theirs: Note
    ) -> None:
        response = run_action(editor, "archive_all", note, theirs)
        assert response.status_code == 302
        assert response["Location"] == f"{page('archive_all')}?pks={note.pk}&pks={theirs.pk}"

    def test_the_page_acts_on_the_selector_s_intersection_alone(
        self, editor: Any, note: Note, theirs: Note
    ) -> None:
        mine = Note.objects.create(owner=editor, title="Mine too")
        url = run_action(editor, "archive_all", note, theirs)["Location"]
        shown = client(editor).get(url)
        assert shown.context_data["target_count"] == 1
        assert "<h1>Archive selected notes</h1>" in shown.content.decode()
        response = client(editor).post(url)
        assert (response.status_code, response["Location"]) == (302, CHANGELIST)
        assert sent(response) == [(messages.SUCCESS, "Archived.")]
        archived = set(Note.objects.filter(archived=True).values_list("pk", flat=True))
        assert archived == {note.pk}
        assert mine.pk not in archived

    def test_a_form_page_acts_on_the_selection(self, editor: Any, note: Note) -> None:
        url = run_action(editor, "retitle_all", note)["Location"]
        response = client(editor).post(url, {"title": "Done"})
        assert (response.status_code, response["Location"]) == (302, CHANGELIST)
        assert Note.objects.get().title == "Done"

    def test_a_posted_selection_never_replaces_the_query_string_s(
        self, editor: Any, note: Note
    ) -> None:
        other = Note.objects.create(owner=editor, title="Other")
        response = client(editor).post(
            f"{page('retitle_all')}?pks={note.pk}", {"title": "Done", "pks": [other.pk]}
        )
        assert response.status_code == 302
        assert dict(Note.objects.values_list("pk", "title")) == {note.pk: "Done", other.pk: "Other"}

    def test_a_key_the_parameter_refuses_is_a_bad_request(self, editor: Any, note: Note) -> None:
        assert client(editor).get(f"{page('archive_all')}?pks=abc").status_code == 400
        assert client(editor).post(f"{page('archive_all')}?pks=abc").status_code == 400
        assert Note.objects.get().archived is False

    def test_a_selection_past_the_parameter_s_length_is_a_bad_request(
        self, editor: Any, note: Note
    ) -> None:
        url = "/keyed/dispatch_app/note/actions/capped/"
        assert client(editor).get(f"{url}?pks={note.pk}").status_code == 200
        assert client(editor).get(f"{url}?pks={note.pk}&pks={note.pk + 1}").status_code == 400

    def test_a_selection_past_the_kernel_s_array_length_is_a_bad_request(
        self, editor: Any, note: Note, settings: Any
    ) -> None:
        # Where the parameter declares no max_length, the kernel's setting is
        # the cap, read on the request.
        settings.SERVICE_SPECS_MAX_ARRAY_LENGTH = 2
        url = page("archive_all")
        assert client(editor).get(f"{url}?pks={note.pk}&pks={note.pk + 1}").status_code == 200
        three = f"{url}?pks={note.pk}&pks={note.pk + 1}&pks={note.pk + 2}"
        assert client(editor).get(three).status_code == 400
        assert client(editor).post(three).status_code == 400
        assert Note.objects.get().archived is False

    def test_the_form_posts_back_to_the_url_carrying_the_selection(
        self, editor: Any, note: Note
    ) -> None:
        # The selection is in the query string alone, so a browser keeps it
        # only by posting to the page's full URL: the form names no action,
        # on the page and on the page a refusal re-renders.
        url = run_action(editor, "retitle_all", note)["Location"]
        shown = client(editor).get(url).content.decode()
        refused = client(editor).post(url, {"title": ""})
        assert refused.status_code == 400
        for body in (shown, refused.content.decode()):
            forms_posted = re.findall(r'<form method="post"[^>]*>', body)
            assert len(forms_posted) == 1
            assert "action=" not in forms_posted[0]
        assert Note.objects.get().title == "Buy milk"

    def test_no_selection_is_the_empty_set(self, editor: Any, note: Note) -> None:
        response = client(editor).get(page("archive_all"))
        assert (response.status_code, response.context_data["target_count"]) == (200, 0)


def built(*actions: Any, **attributes: Any) -> SpecActionsMixin:
    """A ``ModelAdmin`` declaring ``actions``, registered as ``admin.site.register`` builds one."""
    declared = type("Declared", (SpecActionsMixin, admin.ModelAdmin), {"spec_actions": actions})
    return declared(Note, admin.AdminSite())


def create(**fields: Any) -> None: ...


class TestTheDeclaration:
    @pytest.mark.parametrize(
        ("actions", "message"),
        [
            (
                ("archive",),
                "Declared.spec_actions holds 'archive', and each entry must be a SpecAction.",
            ),
            (
                (SpecAction("two words", archive_spec, "Archive", "pk"),),
                "Declared.spec_actions['two words']: an action's name is the last segment of "
                "its URL, so it must be a slug.",
            ),
            (
                (ACTIONS[0], SpecAction("archive", archive_all_spec, "Again", "pks")),
                "Declared.spec_actions['archive']: another of the admin's spec_actions has the "
                "name.",
            ),
            (
                (SpecAction("history", archive_spec, "History", "pk"),),
                "Declared.spec_actions['history']: 'history' is one of the admin's own URL "
                "names for the model.",
            ),
            (
                (
                    SpecAction(
                        "read", SelectorSpec(kind=SelectorKind.LIST, selector=create), "Read", "pk"
                    ),
                ),
                "Declared.spec_actions['read']: the spec must be a ServiceSpec; got SelectorSpec(",
            ),
            (
                (
                    SpecAction(
                        "many",
                        ServiceSpec(
                            service=create,
                            permissions=[Unrestricted()],
                            validator=FormValidator(TitleForm),
                            many=True,
                        ),
                        "Many",
                        "pk",
                    ),
                ),
                "Declared.spec_actions['many']: a many spec takes a list of rows, the page "
                "SpecFormSetView serves, and an admin action acts on one row or on the selected "
                "rows: serve it from a page of its own.",
            ),
            (
                (
                    SpecAction(
                        "create",
                        ServiceSpec(
                            service=create,
                            permissions=[Unrestricted()],
                            validator=FormValidator(TitleForm),
                        ),
                        "Create",
                        "pk",
                    ),
                ),
                "Declared.spec_actions['create']: an admin action acts on one row or on the "
                "selected rows, and the spec declares neither an instance_selector_spec nor a "
                "collection_selector_spec.",
            ),
            (
                (SpecAction("archive", archive_spec, "Archive", "id"),),
                "Declared.spec_actions['archive']: the admin fills 'id', which the spec's target "
                "selector does not read; it reads pk.",
            ),
            (
                (
                    SpecAction(
                        "all",
                        ServiceSpec(
                            service=archive_all,
                            permissions=[Unrestricted()],
                            collection_selector_spec=SelectorSpec(
                                kind=SelectorKind.LIST, selector=lambda: Note.objects.all()
                            ),
                        ),
                        "All",
                        "pks",
                    ),
                ),
                "Declared.spec_actions['all']: the admin fills 'pks', which the spec's target "
                "selector does not read; it reads nothing.",
            ),
            (
                (
                    SpecAction(
                        "all",
                        ServiceSpec(
                            service=archive_all,
                            permissions=[Unrestricted()],
                            collection_selector_spec=SelectorSpec(
                                kind=SelectorKind.LIST,
                                selector=own_notes,
                                reads=Parameters.of(Parameter("pks", "integer", required=True)),
                            ),
                        ),
                        "All",
                        "pks",
                    ),
                ),
                "Declared.spec_actions['all']: the selected rows' keys fill 'pks', a list, and "
                "the collection selector reads it as 'integer': declare it an array.",
            ),
            (
                (
                    SpecAction(
                        "archive",
                        archive_spec,
                        "Archive",
                        "pk",
                        {"success_url": "/", "template_name": "x.html"},
                    ),
                ),
                "Declared.spec_actions['archive']: page_kwargs sets success_url, template_name, "
                "which the admin answers for.",
            ),
        ],
        ids=[
            "not-a-declaration",
            "not-a-slug",
            "a-name-twice",
            "an-admin-url-name",
            "not-a-service-spec",
            "many",
            "no-target",
            "a-parameter-not-read",
            "a-selector-reading-nothing",
            "a-selection-that-is-not-a-list",
            "a-keyword-the-admin-answers",
        ],
    )
    def test_is_refused_when_the_admin_is_built(self, actions: Any, message: str) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            built(*actions)
        assert str(refused.value).startswith(message)

    def test_the_page_s_own_check_names_the_declaration(self) -> None:
        # The page's ``as_view()`` is the page's: a keyword it does not take
        # is refused there, under the admin's name for the action.
        with pytest.raises(ImproperlyConfigured) as refused:
            built(SpecAction("archive", archive_spec, "Archive", "pk", {"commit_button": ""}))
        assert str(refused.value).startswith("Declared['archive'].commit_button must be None")

    def test_a_selection_no_list_meets_empty_is_refused_by_the_kernel(self) -> None:
        # The binding is declared empty, so a parameter needing a row is
        # refused in the kernel's words when the admin is built.
        reads = Parameters.of(Parameter("pks", "array", items="integer", min_length=1))
        spec = ServiceSpec(
            service=archive_all,
            permissions=[Unrestricted()],
            collection_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST, selector=own_notes, reads=reads
            ),
        )
        with pytest.raises(ImproperlyConfigured, match="pks"):
            built(SpecAction("all", spec, "All", "pks"))

    def test_an_admin_declaring_nothing_is_the_admin(self, editor: Any) -> None:
        plain = built()
        assert plain.get_urls()[0].name == "dispatch_app_note_changelist"
        assert plain.actions == ()
