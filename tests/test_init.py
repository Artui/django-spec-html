from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

import django_spec_html


def _subpackages() -> list[ModuleType]:
    return [
        importlib.import_module(info.name)
        for info in pkgutil.walk_packages(
            django_spec_html.__path__, prefix=f"{django_spec_html.__name__}."
        )
        if info.ispkg
    ]


def test_the_package_has_the_subpackages_it_re_exports() -> None:
    # The tests below walk the subpackages, so an empty walk would pass them
    # with nothing checked.
    assert sorted(package.__name__ for package in _subpackages()) == [
        "django_spec_html.http",
        "django_spec_html.views",
    ]


def test_every_subpackage_name_is_re_exported_at_the_root() -> None:
    # A subpackage's ``__all__`` is its public surface, and the root is where a
    # consumer reads the whole of it. A name added to a subpackage and not to
    # the root is public in one place and missing from the other, and nothing
    # else in the suite notices, because every test imports from leaf paths.
    missing = {
        f"{package.__name__}.{name}"
        for package in _subpackages()
        for name in package.__all__
        if name not in django_spec_html.__all__
    }
    assert not missing


def test_each_root_name_is_the_subpackage_s_own_object() -> None:
    # Identity rather than equality: a second definition of one name would
    # pass ``==`` for a class compared with itself nowhere, and an
    # ``isinstance`` against the other copy would fail in a consumer.
    for package in _subpackages():
        for name in package.__all__:
            assert getattr(django_spec_html, name) is getattr(package, name), name


def test_each_root_name_is_defined_in_this_package() -> None:
    # The kernel ships its own copy of the JSON half until the release that
    # removes it, and a re-export of that copy would pass every identity check
    # above while serving the kernel's code under this package's name.
    for name in django_spec_html.__all__:
        if name != "__version__":
            module = getattr(django_spec_html, name).__module__
            assert module.startswith("django_spec_html."), (name, module)


def test_the_root_names_nothing_it_does_not_define() -> None:
    assert [name for name in django_spec_html.__all__ if not hasattr(django_spec_html, name)] == []


def test_the_root_serves_the_two_halves_and_the_version() -> None:
    assert sorted(django_spec_html.__all__) == [
        "AsyncSpecView",
        "SpecFormView",
        "SpecView",
        "UnsupportedMediaType",
        "__version__",
        "add_argument_errors",
        "adispatch_request",
        "dispatch_request",
        "error_response",
        "request_arguments",
        "request_principal",
        "route_arguments",
    ]
    assert (
        django_spec_html.__version__
        == importlib.import_module("django_spec_html.version").__version__
    )
