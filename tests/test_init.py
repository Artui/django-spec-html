from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import subprocess
import sys
from types import ModuleType

import django_service_specs
import pytest

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
        "django_spec_html.model_admin",
        "django_spec_html.types",
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


def test_the_root_names_nothing_it_does_not_define() -> None:
    assert [name for name in django_spec_html.__all__ if not hasattr(django_spec_html, name)] == []


def test_the_root_serves_the_two_halves_the_admin_face_and_the_version() -> None:
    assert sorted(django_spec_html.__all__) == [
        "AsyncSpecView",
        "Outcome",
        "SpecAction",
        "SpecActionsMixin",
        "SpecConfirmView",
        "SpecFormSetView",
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


_WITHOUT_THE_ADMIN = """
import django
from django.conf import settings

settings.configure(
    INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "django_spec_html"],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
)
django.setup()

import django_spec_html
from django.apps import apps

assert not apps.is_installed("django.contrib.admin")
print(django_spec_html.SpecActionsMixin.__name__)
"""


def test_the_package_imports_in_a_project_without_the_admin() -> None:
    # The root re-exports the admin face, so importing the package imports
    # ``django.contrib.admin``, and does so while the app registry is being
    # populated, since the package is an installed app. A project that does
    # not install the admin must still be able to install this package, so
    # the import may touch none of the admin's models. A process of its own,
    # as this suite's settings install the admin.
    ran = subprocess.run(
        [sys.executable, "-c", _WITHOUT_THE_ADMIN],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "SpecActionsMixin\n", "")


_MOVED_NAMES = [
    "AsyncSpecView",
    "SpecView",
    "UnsupportedMediaType",
    "add_argument_errors",
    "adispatch_request",
    "dispatch_request",
    "error_response",
    "request_arguments",
    "request_principal",
    "route_arguments",
]


def test_the_kernel_ships_no_http_package() -> None:
    # The JSON views live here alone. A kernel floor lowered past the release
    # that removed them would put a second SpecView beside this package's, one
    # that an isinstance check or a mock patch against either would miss. A
    # kernel source checkout can also leave an http/ directory holding only
    # __pycache__, which find_spec reports as a namespace package and fails here.
    assert importlib.util.find_spec("django_service_specs.http") is None
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("django_service_specs.http")


@pytest.mark.parametrize("name", _MOVED_NAMES)
def test_the_kernel_root_exports_none_of_the_moved_names(name: str) -> None:
    # Each name is held on its own, so the failure names the one that came back.
    assert name not in django_service_specs.__all__
    assert not hasattr(django_service_specs, name)


def test_every_moved_name_is_served_here() -> None:
    # The absence check above passes for any name the kernel never had, so a
    # misspelt entry would hold nothing. Each must be a name this package serves.
    assert set(_MOVED_NAMES) <= set(django_spec_html.__all__)
