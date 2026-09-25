"""Settings for the test suite.

SQLite in memory, and no DRF anywhere: the kernel's reason to exist is dispatch
without it, and a suite that could import it could not notice the kernel doing
so. The two contrib apps are the ones a spec's permission check and principal
resolution reach, since a principal is a Django user.
"""

from __future__ import annotations

SECRET_KEY = "not-a-secret-this-is-the-test-suite"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

USE_TZ = True
