"""``request_principal``: the principal an HTTP request carries, refused when deactivated."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser

import django_service_specs
import django_service_specs.http
from django_service_specs.authorization.principal_unavailable import PrincipalUnavailable
from django_service_specs.http.request_principal import request_principal
from tests.http.utils import FACTORY, signed_in


class Principal:
    """A user object from a custom model with no ``is_active`` at all."""

    is_authenticated = True


class Deactivated:
    is_authenticated = True
    is_active = False


def test_a_principal_with_no_is_active_is_taken_as_active() -> None:
    # The default in ``getattr(user, "is_active", True)``: Django's own
    # ModelBackend reads a missing ``is_active`` the same way.
    principal = Principal()
    assert request_principal(signed_in(FACTORY.get("/"), principal)) is principal


def test_anonymous_is_the_principal_and_not_refused() -> None:
    assert isinstance(request_principal(signed_in(FACTORY.get("/"))), AnonymousUser)


def test_a_deactivated_principal_is_refused() -> None:
    with pytest.raises(PrincipalUnavailable):
        request_principal(signed_in(FACTORY.get("/"), Deactivated()))


def test_it_is_public_from_the_subpackage_and_the_root() -> None:
    # A host writing its own view reads the principal as the entry points do,
    # so the rule a deactivated account is refused by is the same on both.
    assert "request_principal" in django_service_specs.http.__all__
    assert "request_principal" in django_service_specs.__all__
