"""Models for the dispatch_app tests.

One row type owned by a user: enough for a selector to scope rows to its
principal, a retrieve to miss, a queryset to shape through ``select_related``,
and a write to be rolled back. The app has no migrations, so the test database
creates its table directly, as Django does for any unmigrated app.

``archived`` is the row state an affordance condition reads, and ``LiveNote``
the same table behind a default manager that hides archived rows: a row
condition re-finds its row through ``_base_manager``, and only a model whose
default manager hides rows can tell the two apart.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models


class Note(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notes"
    )
    title = models.CharField(max_length=100)
    archived = models.BooleanField(default=False)

    class Meta:
        app_label = "dispatch_app"
        ordering = ("pk",)


class LiveNoteManager(models.Manager["LiveNote"]):
    def get_queryset(self) -> models.QuerySet[Any]:
        return super().get_queryset().filter(archived=False)


class LiveNote(Note):
    objects = LiveNoteManager()

    class Meta:
        app_label = "dispatch_app"
        proxy = True
