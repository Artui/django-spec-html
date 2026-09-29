"""Settings for the test suite.

SQLite, and no DRF anywhere: the kernel's reason to exist is dispatch without
it, and a suite that could import it could not notice the kernel doing so. The
two contrib apps are the ones a spec's permission check and principal
resolution reach, since a principal is a Django user.

The test database is a **file**, not ``:memory:``. An in-memory SQLite database
is per connection, and Django's connections are per thread, so the first
executor hop the async path makes lands on a connection with no tables and the
failure reads as a missing migration. The name is built at import so it holds
no machine's path, and carries the process id so two runs never share a file.

Each test app belongs to the tests of one concern, so the models one set of
tests needs never grow into another's.

One template directory, and no app templates: ``SpecFormView`` renders a page,
and the one template its tests and the forms example on the HTTP page render
is the whole of what the suite needs. It holds a form tag, the CSRF token, the
form and a named submit button - the page a person posts - so what those tests
render is what a project's own template would send back. The path is built
from this file's location, so it holds no machine's path.
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
    "tests.relations_app",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {
            "NAME": os.path.join(
                tempfile.gettempdir(), f"django_service_specs_tests_{os.getpid()}.sqlite3"
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
