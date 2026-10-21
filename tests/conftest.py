"""Fixtures shared by the suite, and the guard that it tests this package and not the kernel's copy."""

from __future__ import annotations

import sys
from collections.abc import Iterator, Mapping
from types import ModuleType
from typing import Any

import pytest
from django.apps import apps
from django.db import connection, models


def _forget_range_validators() -> None:
    # A model field reads the range into its validators once and caches them,
    # so a range stated or not leaks into whichever test reads them next.
    for model in apps.get_models():
        for field in model._meta.get_fields():
            if isinstance(field, models.IntegerField):
                field.__dict__.pop("validators", None)


@pytest.fixture
def unstated_ranges(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The backend states no integer column's range, as Django 4.2's SQLite backend does.

    From 5.0 the SQLite backend states 64 bits, and Django refuses a value
    past them itself, so this is what lets the kernel's own refusal be held
    on every version the suite runs on.
    """
    # On the class, since each thread holds a connection of its own, and an
    # async write's queries run on another thread's.
    monkeypatch.setattr(
        type(connection.ops), "integer_field_range", lambda self, internal_type: (None, None)
    )
    _forget_range_validators()
    yield
    _forget_range_validators()


# The guard that the suite tests this package and not the kernel's copy of it.
#
# Until the kernel release that removes it, the kernel ships the JSON half this
# package was moved out of, and its root imports every module of it. A missed
# import rewrite therefore still resolves, against the kernel's copy, and a test
# passes while testing the wrong code. Nothing at import fails, so this reads
# what the run actually loaded once it is over: no module of this package, of
# the suite or of the docs examples may hold a module of the kernel's copy, or
# a class or function one of them defines. Unlike a guard over a kernel module
# nothing loads, it cannot ask that the copy stay out of ``sys.modules``: the
# kernel's root imports all of it, and every kernel name this package reads
# comes through the root. ``tests/test_kernel_copy_references.py`` reads the
# sources for the routes a loaded module cannot show.

_KERNEL_COPY = ("django_service_specs.http",)
_OURS = ("django_spec_html", "tests", "docs")


def _within(name: str, packages: tuple[str, ...]) -> bool:
    return any(name == package or name.startswith(f"{package}.") for package in packages)


def _kernel_copy_of(value: Any) -> str | None:
    """The kernel module ``value`` comes from, when that module is the kernel's copy.

    The module arm is held by
    test_the_guard_finds_a_module_of_the_kernel_s_copy_held_as_a_global, the
    ``__module__`` arm by test_the_guard_finds_a_name_imported_from_the_kernel_s_copy,
    and the string check beside it by
    test_the_guard_passes_a_value_whose_module_is_not_a_string.
    """
    if isinstance(value, ModuleType):
        origin = value.__name__
    else:
        origin = getattr(value, "__module__", None)
        if not isinstance(origin, str):
            return None
    return origin if _within(origin, _KERNEL_COPY) else None


def kernel_copies_loaded(modules: Mapping[str, ModuleType | None]) -> list[str]:
    """Every place in ``modules`` that reaches the kernel's copy of this package.

    The ``module is None`` condition is held by
    test_the_guard_skips_a_module_import_blocked_with_none, the package
    condition beside it by test_the_guard_reads_only_this_repository_s_modules.
    """
    found = []
    for name, module in sorted(modules.items()):
        if not _within(name, _OURS) or module is None:
            continue
        for attribute, value in sorted(vars(module).items()):
            origin = _kernel_copy_of(value)
            if origin is not None:
                found.append(f"{name}.{attribute} is the kernel's {origin}")
    return found


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    found = kernel_copies_loaded(dict(sys.modules))
    if found:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        lines = ["The suite reached the kernel's copy of this package:", *found]
        if reporter is not None:
            reporter.write_line("\n".join(lines), red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
