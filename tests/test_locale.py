"""Tests for the package's translation catalog, ``django_spec_html/locale/``.

``UnsupportedMediaType``'s default message is a msgid in the shipped source
catalog, marked for extraction where the class is defined and looked up in the
active language when the refusal is built. These tests translate through a real
compiled catalog rather than a patched ``gettext``: a patch would agree with any
lookup, including one made once at import. The catalog is written for a
language Django ships none for (``xx``), so nothing but the test's own catalog
can answer, and it is compiled by ``_write_mo`` below rather than ``msgfmt``,
which not every machine running the suite has.
"""

from __future__ import annotations

import array
import ast
import os
import re
import struct
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.apps import apps
from django.test import override_settings
from django.utils import translation
from django.utils.translation import trans_real
from django_service_specs import DispatchError, ServiceError

from django_spec_html.http.unsupported_media_type import UnsupportedMediaType

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "django_spec_html"
SOURCE_CATALOG = PACKAGE_ROOT / "locale" / "en" / "LC_MESSAGES" / "django.po"

# No catalog Django or any installed app ships answers for this code, so a
# translated message can only have come from the catalog a test wrote.
LANGUAGE = "xx"

# The English msgid, as a literal rather than read off the class, so a refusal
# writing its looked-up message back onto the class fails here instead of
# moving the expectation along with it.
MEDIA_TYPE_MSGID = "The request body is in a format this transport does not read."


def _refusal_classes() -> list[type[Exception]]:
    # Read off the class tree rather than listed, so a refusal added later is
    # held to the catalog without anyone remembering to add it here. Subclasses
    # a test declares are not the package's and carry no msgid.
    found: list[type[Exception]] = []
    pending: list[type[Exception]] = [ServiceError, DispatchError]
    while pending:
        cls = pending.pop()
        if cls.__module__.startswith("django_spec_html."):
            found.append(cls)
        pending.extend(cls.__subclasses__())
    return found


def _default_messages() -> set[str]:
    return {
        vars(cls)["default_message"] for cls in _refusal_classes() if "default_message" in vars(cls)
    }


def _write_mo(path: Path, messages: dict[str, str]) -> None:
    """Compile ``messages`` into a GNU ``.mo`` file, as ``msgfmt`` would.

    The format is a header, a table of (length, offset) pairs for the sorted
    msgids, one for their translations, then the NUL-terminated strings. The
    empty msgid is the catalog's header, and its charset is what makes the
    stdlib reader decode the strings as UTF-8 rather than ASCII.
    """
    catalog = {"": "Content-Type: text/plain; charset=UTF-8\n", **messages}
    keys = sorted(catalog)
    ids = strs = b""
    spans: list[tuple[int, int, int, int]] = []
    for key in keys:
        msgid, msgstr = key.encode(), catalog[key].encode()
        spans.append((len(ids), len(msgid), len(strs), len(msgstr)))
        ids += msgid + b"\0"
        strs += msgstr + b"\0"
    key_start = 7 * 4 + 16 * len(keys)
    value_start = key_start + len(ids)
    offsets: list[int] = []
    for id_offset, id_length, _, _ in spans:
        offsets += [id_length, id_offset + key_start]
    for _, _, str_offset, str_length in spans:
        offsets += [str_length, str_offset + value_start]
    header = struct.pack("Iiiiiii", 0x950412DE, 0, len(keys), 7 * 4, 7 * 4 + len(keys) * 8, 0, 0)
    path.parent.mkdir(parents=True)
    path.write_bytes(header + array.array("i", offsets).tobytes() + ids + strs)


def _translated(msgid: str) -> str:
    return msgid.upper()


def _write_test_catalog(locale_dir: Path) -> None:
    _write_mo(
        locale_dir / LANGUAGE / "LC_MESSAGES" / "django.mo",
        {MEDIA_TYPE_MSGID: _translated(MEDIA_TYPE_MSGID)},
    )


def _msgids(po: Path) -> set[str]:
    # A msgid may run over several quoted lines, which the reader joins; the
    # header's empty msgid and obsolete (``#~``) entries are not messages.
    msgids: set[str] = set()
    current: list[str] | None = None
    for line in [*po.read_text(encoding="utf-8").splitlines(), ""]:
        if line.startswith("msgid "):
            current = [ast.literal_eval(line.removeprefix("msgid "))]
        elif line.startswith('"') and current is not None:
            current.append(ast.literal_eval(line))
        elif current is not None:
            msgids.add("".join(current))
            current = None
    msgids.discard("")
    return msgids


def _literal_gettext_msgids() -> set[str]:
    """Each string literal a ``gettext`` call in the package passes, read off its source."""
    found: set[str] = set()
    for path in PACKAGE_ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "gettext"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found.add(node.args[0].value)
    return found


def _unmarked_default_messages() -> list[str]:
    """Each class-level ``default_message`` in the package that is not ``gettext_noop("...")``."""
    unmarked: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        for cls in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(cls, ast.ClassDef):
                continue
            for statement in cls.body:
                if isinstance(statement, ast.AnnAssign):
                    names, value = [statement.target], statement.value
                elif isinstance(statement, ast.Assign):
                    names, value = statement.targets, statement.value
                else:
                    continue
                if not any(isinstance(n, ast.Name) and n.id == "default_message" for n in names):
                    continue
                marked = (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "gettext_noop"
                    and len(value.args) == 1
                    and isinstance(value.args[0], ast.Constant)
                    and isinstance(value.args[0].value, str)
                )
                if not marked:
                    unmarked.append(f"{path.relative_to(REPO_ROOT)}: {cls.name}")
    return unmarked


@pytest.fixture
def locale_paths(tmp_path: Path) -> Iterator[None]:
    # Changing LOCALE_PATHS fires Django's own setting_changed receiver, which
    # empties the translation cache on the way in and on the way out, so the
    # test catalog is read fresh and leaves nothing behind.
    _write_test_catalog(tmp_path)
    with override_settings(LOCALE_PATHS=[str(tmp_path)]):
        yield


def test_the_refusal_declares_its_msgid() -> None:
    # Ties the literal above to the class, and lists every refusal in the
    # package that declares one, so a new member is added here rather than missed.
    assert vars(UnsupportedMediaType)["default_message"] == MEDIA_TYPE_MSGID
    assert [c.__name__ for c in _refusal_classes() if "default_message" in vars(c)] == [
        "UnsupportedMediaType"
    ]


def test_every_default_message_is_marked_for_extraction() -> None:
    # A bare string is a msgid no extraction finds, so the refusal would read
    # in English in every language and appear in no catalog. Marked with the
    # package's own ``gettext_noop`` because xgettext matches the call by name
    # (a differently named wrapper is not extracted), and because Django's reads
    # a setting each time it is called, which test_marking_a_msgid_reads_no_setting
    # holds a class body to.
    assert _unmarked_default_messages() == []


@pytest.mark.usefixtures("locale_paths")
def test_a_default_message_is_read_in_the_active_language() -> None:
    with translation.override(LANGUAGE):
        error = UnsupportedMediaType()
    assert (error.message, str(error)) == (
        _translated(MEDIA_TYPE_MSGID),
        _translated(MEDIA_TYPE_MSGID),
    )


@pytest.mark.usefixtures("locale_paths")
def test_nothing_is_frozen_at_import_or_at_the_last_call() -> None:
    # The class keeps the msgid, and each construction looks it up again: a
    # refusal built in another language before this one does not decide its words.
    with translation.override(LANGUAGE):
        UnsupportedMediaType()
        assert vars(UnsupportedMediaType)["default_message"] == MEDIA_TYPE_MSGID
    assert UnsupportedMediaType().message == MEDIA_TYPE_MSGID


@pytest.mark.usefixtures("locale_paths")
def test_a_message_the_caller_passes_is_used_as_given() -> None:
    # Passed the string the test catalog does translate, so a lookup on the
    # caller's message would show.
    with translation.override(LANGUAGE):
        error = UnsupportedMediaType(MEDIA_TYPE_MSGID)
    assert error.message == MEDIA_TYPE_MSGID


def test_listing_the_app_loads_a_catalog_beside_the_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The route the docs give: Django reads ``<app path>/locale`` for every
    # installed app, which is where the shipped catalog sits. The app's path is
    # pointed at a copy holding a compiled test language, so the package's own
    # tree is never written to.
    config = apps.get_app_config("django_spec_html")
    assert SOURCE_CATALOG.is_relative_to(Path(config.path) / "locale")
    _write_test_catalog(tmp_path / "locale")
    monkeypatch.setattr(config, "path", str(tmp_path))
    monkeypatch.setattr(trans_real, "_translations", {})
    with translation.override(LANGUAGE):
        assert UnsupportedMediaType().message == _translated(MEDIA_TYPE_MSGID)


def test_the_source_catalog_lists_every_default_message_and_gettext_literal() -> None:
    assert SOURCE_CATALOG.is_file()
    expected = _default_messages() | _literal_gettext_msgids()
    assert MEDIA_TYPE_MSGID in expected
    assert sorted(expected - _msgids(SOURCE_CATALOG)) == []


def test_the_source_catalog_lists_every_translate_tag_in_the_templates() -> None:
    # ``blocktranslate`` bodies span lines and take variables, so the catalog
    # check in CI (make messages-check) is what holds those; the single-line
    # tag is read here.
    tags = {
        match
        for template in (PACKAGE_ROOT / "templates").rglob("*.html")
        for match in re.findall(r'{% translate "([^"]+)" %}', template.read_text(encoding="utf-8"))
    }
    assert "Save" in tags
    assert sorted(tags - _msgids(SOURCE_CATALOG)) == []


def test_the_catalog_reader_joins_a_wrapped_msgid_and_skips_the_header(tmp_path: Path) -> None:
    po = tmp_path / "django.po"
    po.write_text(
        'msgid ""\nmsgstr ""\n"Language: \\n"\n\nmsgid ""\n"One "\n"two."\nmsgstr ""\n\n'
        '#~ msgid "Gone."\n#~ msgstr ""\n',
        encoding="utf-8",
    )
    assert _msgids(po) == {"One two."}


def test_marking_a_msgid_reads_no_setting() -> None:
    # Each default is marked when its class is defined, at import, and the
    # package imports before a project's settings are configured. Only the
    # lookup, made when a refusal is built with no message, reads them; one the
    # caller words needs none. A fresh interpreter with no settings module,
    # because this one is configured. Django's own ``gettext_noop`` reads
    # ``USE_I18N`` when called, so swapping it in for the package's fails here.
    probe = """
from django_spec_html.http.unsupported_media_type import UnsupportedMediaType
print(UnsupportedMediaType("Ask the owner.").message)
"""
    env = {key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"}
    completed = subprocess.run(
        [sys.executable, "-c", probe], cwd=REPO_ROOT, env=env, capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "Ask the owner.\n"


def test_the_package_ships_the_source_language_and_no_translation() -> None:
    # A translated language is a contribution someone can review; until one is
    # reviewed and added, ``locale/`` holds the source catalog alone.
    assert sorted(path.name for path in (PACKAGE_ROOT / "locale").iterdir()) == ["en"]
