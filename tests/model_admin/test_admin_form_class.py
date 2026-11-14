from __future__ import annotations

import dataclasses
import sys
import types
from typing import Any

import pytest
from django import forms
from django.contrib import admin
from django.core.exceptions import ImproperlyConfigured
from django.urls import path
from django_service_specs import (
    DataclassValidator,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_html.model_admin.spec_actions_mixin import SpecActionsMixin
from django_spec_html.types.spec_action import SpecAction
from tests.dispatch.utils import PK, note_by_pk
from tests.dispatch_app.models import Note
from tests.model_admin.test_spec_actions_mixin import (  # noqa: F401
    client,
    editor,
    note,
    page,
)

pytestmark = pytest.mark.django_db


@dataclasses.dataclass
class Rename:
    title: str


class RenameForm(forms.Form):
    title = forms.CharField()


def rename(*, instance: Note, data: Rename) -> Note:
    instance.title = data.title
    instance.save(update_fields=["title"])
    return instance


spec = ServiceSpec(
    service=rename,
    permissions=[Unrestricted()],
    validator=DataclassValidator(Rename),
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=note_by_pk, reads=PK),
)


def declared(**page_kwargs: Any) -> Any:
    action = SpecAction("rename", spec, "Rename", "pk", page_kwargs)
    return type("Declared", (SpecActionsMixin, admin.ModelAdmin), {"spec_actions": (action,)})


@pytest.fixture(autouse=True)
def urlconf(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    site = admin.AdminSite(name="form_class_admin")
    site.register(Note, declared(form_class=RenameForm))
    module = types.ModuleType("form_class_urls")
    module.urlpatterns = [path("admin/", site.urls)]
    monkeypatch.setitem(sys.modules, "form_class_urls", module)
    settings.ROOT_URLCONF = "form_class_urls"
    settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "django.contrib.messages"]


class TestAnActionOverADataclassValidator:
    def test_it_renders_the_form_page(self, editor: Any, note: Note) -> None:
        shown = client(editor).get(f"/admin/dispatch_app/note/{note.pk}/actions/rename/")
        assert shown.status_code == 200
        assert 'name="title"' in shown.content.decode()

    def test_it_dispatches_through_the_dataclass(self, editor: Any, note: Note) -> None:
        url = f"/admin/dispatch_app/note/{note.pk}/actions/rename/"
        sent = client(editor).post(url, {"title": "Buy tea"})
        assert sent.status_code == 302
        note.refresh_from_db()
        assert note.title == "Buy tea"

    def test_without_a_form_class_it_is_refused_at_registration(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="must declare validator=None"):
            admin.AdminSite().register(Note, declared())

    def test_a_form_class_the_spec_cannot_take_is_refused(self) -> None:
        class Other(forms.Form):
            unknown = forms.CharField()

        with pytest.raises(ImproperlyConfigured):
            admin.AdminSite().register(Note, declared(form_class=Other))
