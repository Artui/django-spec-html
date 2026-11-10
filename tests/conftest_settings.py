from __future__ import annotations

import os
import tempfile

SECRET_KEY = "not-a-secret-this-is-the-test-suite"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django_spec_html",
    "tests.adapter_app",
    "tests.dispatch_app",
]

# The admin's own stack, as a project serving the admin lists it, so the
# admin face's tests sign in, are refused by ``admin_view`` and read the
# messages a success sends. The messages app is not listed: a page needs none,
# and the pages' tests of a success's message add it, so the claim that a page
# sends one without it is still made where it is tested. The middleware puts
# no message store on a request built by a factory, which never passes through it.
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

# The admin's templates link their stylesheets through ``{% static %}``.
STATIC_URL = "/static/"

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
        "APP_DIRS": True,
        # The three the admin's templates read: the signed-in user, the
        # request, and the messages a success sent.
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
