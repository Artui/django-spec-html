"""A stored row for the page's tests that clear or keep a file field."""

from __future__ import annotations

from django.core.files.base import ContentFile

from tests.adapter_app.models import Document

# Where ``stored_document`` puts its optional file, under the field's ``upload_to``.
STORED = "documents/notes.txt"


def stored_document(*, attachment: bool = True) -> Document:
    """A saved row holding its required file and, unless told not to, its optional one."""
    row = Document(title="Notes")
    if attachment:
        row.attachment.save("notes.txt", ContentFile(b"notes"), save=False)
    row.scan.save("scan.txt", ContentFile(b"scan"), save=False)
    row.save()
    return row
