"""The session-finish guard: each test hands it the kernel's objects and asserts it finds them.

A guard that finds nothing reads exactly like a clean tree, so every route it
watches is shown found here. The kernel's objects are read inside each test,
never held as a global of this module, or the guard would find this module too.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import django_service_specs

import django_spec_html
from tests.conftest import kernel_copies_loaded


def _module(name: str, **values: object) -> ModuleType:
    module = ModuleType(name)
    vars(module).update(values)
    return module


def test_the_guard_finds_a_name_imported_from_the_kernel_s_copy() -> None:
    view = django_service_specs.SpecView
    answer = django_service_specs.error_response
    found = kernel_copies_loaded(
        {"tests.stray": _module("tests.stray", SpecView=view, error_response=answer)}
    )
    assert found == [
        "tests.stray.SpecView is the kernel's django_service_specs.http.spec_view",
        "tests.stray.error_response is the kernel's django_service_specs.http.error_response",
    ]


def test_the_guard_finds_a_module_of_the_kernel_s_copy_held_as_a_global() -> None:
    # ``importlib.import_module`` of a kernel path, kept to monkeypatch a name
    # in it: the spy would watch the kernel's module, and the test would call
    # the kernel's reader.
    reader = sys.modules["django_service_specs.http.request_arguments"]
    found = kernel_copies_loaded({"docs.stray": _module("docs.stray", reader_module=reader)})
    assert found == [
        "docs.stray.reader_module is the kernel's django_service_specs.http.request_arguments"
    ]


def test_the_guard_finds_the_kernel_s_copy_in_this_package_itself() -> None:
    refusal = django_service_specs.UnsupportedMediaType
    found = kernel_copies_loaded(
        {"django_spec_html.http.stray": _module("x", UnsupportedMediaType=refusal)}
    )
    assert found == [
        "django_spec_html.http.stray.UnsupportedMediaType is the kernel's "
        "django_service_specs.http.unsupported_media_type"
    ]


def test_the_guard_reads_only_this_repository_s_modules() -> None:
    view = django_service_specs.SpecView
    assert kernel_copies_loaded({"elsewhere": _module("elsewhere", SpecView=view)}) == []


def test_the_guard_skips_a_module_import_blocked_with_none() -> None:
    # ``sys.modules[name] = None`` is how an import is blocked, and such an
    # entry has no namespace to read.
    assert kernel_copies_loaded({"tests.blocked": None}) == []


def test_the_guard_passes_a_kernel_name_that_stays_the_kernel_s() -> None:
    kept = {"dispatch": django_service_specs.dispatch, "kernel": django_service_specs}
    assert kernel_copies_loaded({"tests.own": _module("tests.own", **kept)}) == []


def test_the_guard_passes_this_package_s_own_copy() -> None:
    own = {"SpecView": django_spec_html.SpecView, "http": sys.modules["django_spec_html.http"]}
    assert kernel_copies_loaded({"tests.own": _module("tests.own", **own)}) == []


def test_the_guard_passes_a_value_whose_module_is_not_a_string() -> None:
    # A class's ``__module__`` is a string, but any object may carry the
    # attribute, and a mock answers every attribute it is asked for.
    odd = SimpleNamespace(__module__=None)
    assert kernel_copies_loaded({"tests.odd": _module("tests.odd", odd=odd)}) == []
