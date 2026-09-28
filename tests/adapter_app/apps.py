from __future__ import annotations

from django.apps import AppConfig


class AdapterAppConfig(AppConfig):
    name = "tests.adapter_app"
    default_auto_field = "django.db.models.BigAutoField"
