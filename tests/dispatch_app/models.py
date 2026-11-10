from __future__ import annotations

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


class Tag(models.Model):
    key = models.CharField(max_length=50, primary_key=True)
    archived = models.BooleanField(default=False)

    class Meta:
        app_label = "dispatch_app"
