"""The page base: what a page class owes it before ``as_view()`` builds a view.

The rest of ``views/utils.py`` is served through ``SpecFormView``, whose tests
hold it, every one of them building a page that supplies every hook. What is
held here is what no concrete page can show: a page class that left one out.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django_service_specs import ServiceSpec

from django_spec_html.views.utils import Served, SpecPageBase
from tests.http.utils import note_spec


class _NoPageRender(SpecPageBase):
    """A page supplying every hook but ``_render_page``, as one that forgot it would be."""

    @classmethod
    def _check_spec(cls, label: str, spec: ServiceSpec) -> None: ...

    @classmethod
    def _check_page(cls, label: str, spec: ServiceSpec, initkwargs: Mapping[str, Any]) -> None: ...

    def _form(
        self, spec: ServiceSpec, principal: Any, target: Any, served: Served, **bound: Any
    ) -> Any: ...

    def _arguments(
        self, form: Any, spec: ServiceSpec, url_kwargs: Mapping[str, Any], served: Served
    ) -> Mapping[str, Any]:
        return {}

    def _render_refused(
        self, form: Any, refused: Any, target: Any, /, **context: Any
    ) -> HttpResponse:
        return HttpResponse()


class TestTheHooks:
    def test_a_page_missing_a_hook_is_refused_at_as_view(self) -> None:
        with pytest.raises(ImproperlyConfigured, match=r"^_NoPageRender .*: _render_page\.$"):
            _NoPageRender.as_view(spec=note_spec(), success_url="/done/")

    def test_the_base_itself_is_refused_naming_every_hook(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecPageBase.as_view(spec=note_spec(), success_url="/done/")
        hooks = "_arguments, _check_page, _check_spec, _form, _render_page, _render_refused"
        assert str(refused.value) == f"SpecPageBase does not supply the page's hooks: {hooks}."
