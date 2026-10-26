"""Settings for the test suite.

SQLite, and no DRF anywhere: this package serves a spec with no API framework,
and a suite that could import DRF could not notice the package needing it. The
two contrib apps are the ones a spec's permission check and principal
resolution reach, since a principal is a Django user.

No kernel app is listed. Neither this package nor the kernel needs one
installed to serve a spec, and a suite that listed it could not show that.

The test database is a **file**, not ``:memory:``. An in-memory SQLite database
is per connection, and Django's connections are per thread, so the first
executor hop the async path makes lands on a connection with no tables and the
failure reads as a missing migration. The name is built at import so it holds
no machine's path, and carries the process id so two runs never share a file.

Each test app belongs to the tests of one concern, so the models one set of
tests needs never grow into another's.

One template directory, and no app templates: ``SpecFormView`` renders a page,
and the templates its tests and the page examples render are the whole of
what the suite needs. ``spec_form.html`` holds a form tag, the CSRF token, the
form and a named submit button - the page a person posts - so what those tests
render is what a project's own template would send back. ``spec_page.html``
wraps the same form in a layout and ``spec_fragment.html`` is the form alone,
so a test can tell the page from the fragment on any Django; and
``spec_form_htmx.html`` carries its form as a ``partialdef``, which only
Django 6.0 and later parse, so only the tests skipped below it load it. Each
other template is one feature's page with its form fragment beside it, named
for that feature (``spec_target``, ``spec_confirm``, ``spec_edition``), so a
docs example renders the page its section shows. The path is built from this
file's location, so it holds no machine's path.
"""

from __future__ import annotations

import os
import tempfile

SECRET_KEY = "not-a-secret-this-is-the-test-suite"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "tests.adapter_app",
    "tests.dispatch_app",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {
            "NAME": os.path.join(
                tempfile.gettempdir(), f"django_spec_html_tests_{os.getpid()}.sqlite3"
            ),
        },
    }
}

USE_TZ = True

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(os.path.dirname(__file__), "templates")],
    }
]
