"""No source in this repository reaches the kernel's copy of this package by name.

Until the kernel release that removes it, the kernel ships the JSON half this
package was moved out of, and its root re-exports every moving name, so
``django_service_specs.SpecView`` resolves, to the kernel's class, and a test
reading it passes while testing the wrong code. ``tests/conftest.py``'s
session-finish guard sees only module globals the run loaded; a function-local
import, an attribute read through the kernel's root, a python block on a page
and a docs cross-reference are invisible to it. This reads the source instead,
with ``ast``, so an import at any depth, an aliased module and an attribute
chain are all one shape, and reads the text for cross-references, which the
kernel's own docs inventory still resolves.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import django_service_specs
import pytest

import django_spec_html

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL = "django_service_specs"
_COPY = f"{KERNEL}.http"
# ``__version__`` is in both roots and is each package's own, not a moved name.
FORBIDDEN = (set(django_spec_html.__all__) & set(django_service_specs.__all__)) - {
    "__version__"
} | {
    "http",
    "SpecViewBase",
    "answered_methods",
    "check_asked_input",
    "not_found_response",
    "success_response",
}
"""Names that, read through the kernel, are the kernel's copy of this package."""

EXEMPT = {
    # The guard names the kernel's copy to look for it.
    "tests/conftest.py",
    # The guard's own tests hand it the kernel's objects to find.
    "tests/test_conftest.py",
    # This file, whose fixtures below are the shapes it rejects.
    "tests/test_kernel_copy_references.py",
}
"""Files that name the kernel's copy on purpose, each for the reason beside it."""

_CROSS_REFERENCE = re.compile(rf"\]\[({KERNEL}(?:\.\w+)*)\]|^\s*:::\s*({KERNEL}(?:\.\w+)*)")
"""A docs cross-reference or ``:::`` directive naming a kernel path, captured whole.

The kernel's inventory lists every moved name at its root as well as under
``http``, so ``[SpecView][django_service_specs.SpecView]`` resolves to the
kernel's copy as surely as the module path does; the target is judged by
``_forbidden_path``, the rule the import check uses.
"""


def _forbidden_path(path: str) -> bool:
    if path == _COPY or path.startswith(f"{_COPY}."):
        return True
    head, _, rest = path.partition(".")
    return head == KERNEL and rest.split(".")[0] in FORBIDDEN


def _dotted(node: ast.expr) -> list[str] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return [node.id, *reversed(parts)]


def kernel_copy_references(source: str) -> list[str]:
    """Every reference in ``source`` that reaches the kernel's copy of this package."""
    tree = ast.parse(source)
    # Every name the kernel's root is bound to, ``import django_service_specs``
    # and ``import django_service_specs.x`` included, wherever it is bound.
    aliases = {
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == KERNEL or (alias.name.startswith(f"{KERNEL}.") and not alias.asname)
    }
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [
                f"{node.lineno}: import {alias.name}"
                for alias in node.names
                if _forbidden_path(alias.name)
            ]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == KERNEL:
            module = node.module or ""
            found += [
                f"{node.lineno}: from {module} import {alias.name}"
                for alias in node.names
                if _forbidden_path(module) or alias.name in FORBIDDEN
            ]
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            parts = _dotted(node)
            if parts and parts[0] in aliases:
                path = [KERNEL, *parts[1:]]
                # The shortest forbidden prefix of a chain only, so one read
                # is one finding: ``k.SpecView.as_view`` reports ``k.SpecView``.
                if _forbidden_path(".".join(path)) and not _forbidden_path(".".join(path[:-1])):
                    found.append(f"{node.lineno}: {'.'.join(parts)}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            # A dotted path handed to ``monkeypatch.setattr`` or ``import_module``.
            and re.fullmatch(rf"{KERNEL}(\.\w+)+", node.value)
            and _forbidden_path(node.value)
        ):
            found.append(f"{node.lineno}: {node.value!r}")
    return found


def cross_references(text: str) -> list[str]:
    """Every docs cross-reference in ``text`` into the kernel's copy, by line."""
    return [
        f"{number}: {line.strip()}"
        for number, line in enumerate(text.splitlines(), start=1)
        for match in _CROSS_REFERENCE.finditer(line)
        if _forbidden_path(match.group(1) or match.group(2))
    ]


def _python_sources() -> list[Path]:
    return sorted(
        path
        for directory in ("django_spec_html", "tests", "docs")
        for path in (REPO_ROOT / directory).rglob("*.py")
        if path.relative_to(REPO_ROOT).as_posix() not in EXEMPT
    )


def _pages() -> list[Path]:
    return sorted([*(REPO_ROOT / "docs").glob("*.md"), REPO_ROOT / "README.md"])


def _page_blocks() -> list[tuple[str, int, str]]:
    blocks = []
    for page in _pages():
        for index, block in enumerate(re.findall(r"```python\n(.*?)```", page.read_text(), re.S)):
            # A snippet include names a file under docs/examples, read above.
            if "--8<--" not in block:
                blocks.append((page.relative_to(REPO_ROOT).as_posix(), index, block))
    return blocks


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def test_the_sources_read_are_the_package_the_suite_and_the_pages() -> None:
    # An empty walk would pass every test below with nothing read.
    read = {_relative(path) for path in _python_sources()}
    assert {
        "django_spec_html/http/request_arguments.py",
        "django_spec_html/views/spec_form_view.py",
        "docs/examples/http.py",
        "docs/examples/spec_form.py",
    } <= read
    assert {"tests/http/test_request_arguments.py", "tests/views/test_spec_form_view.py"} <= read
    assert {"README.md", "docs/http.md", "docs/index.md", "docs/pages.md"} <= {
        _relative(page) for page in _pages()
    }
    assert {page for page, _, _ in _page_blocks()} >= {"README.md"}
    assert {"SpecView", "error_response", "dispatch_request", "http", "SpecViewBase"} <= FORBIDDEN


@pytest.mark.parametrize("path", _python_sources(), ids=_relative)
def test_no_module_reaches_the_kernel_s_copy(path: Path) -> None:
    assert kernel_copy_references(path.read_text()) == []


@pytest.mark.parametrize("path", [*_python_sources(), *_pages()], ids=_relative)
def test_no_docs_cross_reference_names_the_kernel_s_copy(path: Path) -> None:
    assert cross_references(path.read_text()) == []


@pytest.mark.parametrize(
    ("page", "index", "block"), _page_blocks(), ids=lambda value: str(value)[:20]
)
def test_no_python_block_on_a_page_reaches_the_kernel_s_copy(
    page: str, index: int, block: str
) -> None:
    assert kernel_copy_references(block) == [], (page, index)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "import django_service_specs\ndjango_service_specs.SpecView\n",
            ["2: django_service_specs.SpecView"],
        ),
        (
            "import django_service_specs as k\nk.error_response(exc).status_code\n",
            ["2: k.error_response"],
        ),
        (
            "import django_service_specs.registry\ndjango_service_specs.dispatch_request(s, r)\n",
            ["2: django_service_specs.dispatch_request"],
        ),
        (
            "def f():\n    from django_service_specs import UnsupportedMediaType\n",
            ["2: from django_service_specs import UnsupportedMediaType"],
        ),
        (
            "from django_service_specs.http.utils import SpecViewBase\n",
            ["1: from django_service_specs.http.utils import SpecViewBase"],
        ),
        (
            "from django_service_specs.http import request_arguments\n",
            ["1: from django_service_specs.http import request_arguments"],
        ),
        (
            "import django_service_specs.http.error_response\n",
            ["1: import django_service_specs.http.error_response"],
        ),
        (
            "import django_service_specs\ndjango_service_specs.http.spec_view.SpecView\n",
            ["2: django_service_specs.http"],
        ),
        (
            # Not a moved root name, so only the module path answers: ``ANSWERABLE``
            # is a tuple, which the session guard cannot attribute to a module.
            "from django_service_specs.http.utils import ANSWERABLE\n",
            ["1: from django_service_specs.http.utils import ANSWERABLE"],
        ),
        (
            "importlib.import_module('django_service_specs.http.request_arguments')\n",
            ["1: 'django_service_specs.http.request_arguments'"],
        ),
    ],
)
def test_each_route_to_the_kernel_s_copy_is_found(source: str, expected: list[str]) -> None:
    assert kernel_copy_references(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        "from django_service_specs import ServiceSpec, dispatch, unanswered_refusals\n",
        "import django_service_specs\ndjango_service_specs.REFUSALS\n",
        "from django_service_specs import NON_FIELD_ERRORS\n",
        "from django_spec_html import SpecView, error_response\n",
        "from django_spec_html.http.utils import SpecViewBase\n",
        "settings = {'SERVICE_SPECS_MAX_ARRAY_LENGTH': 2, 'app': 'django_service_specs'}\n",
        "import other as django_service_specs\nother.SpecView\n",
    ],
)
def test_a_kernel_name_that_stays_the_kernel_s_is_not_found(source: str) -> None:
    assert kernel_copy_references(source) == []


def test_a_cross_reference_into_the_kernel_s_copy_is_found() -> None:
    text = (
        "See [`SpecView`][django_service_specs.http.spec_view.SpecView].\n"
        "And [`http`][django_service_specs.http].\n"
        "Or [`SpecView`][django_service_specs.SpecView], through the kernel's root.\n"
        "::: django_service_specs.http.spec_view.SpecView\n"
    )
    assert cross_references(text) == [
        "1: See [`SpecView`][django_service_specs.http.spec_view.SpecView].",
        "2: And [`http`][django_service_specs.http].",
        "3: Or [`SpecView`][django_service_specs.SpecView], through the kernel's root.",
        "4: ::: django_service_specs.http.spec_view.SpecView",
    ]


def test_a_cross_reference_the_kernel_keeps_is_not_found() -> None:
    text = (
        "[`dispatch`][django_service_specs.dispatch.dispatch.dispatch], "
        "[`SpecView`][django_spec_html.http.spec_view.SpecView], "
        "[a page](https://artui.github.io/django-service-specs/http/)\n"
        "::: django_service_specs.dispatch.dispatch.dispatch\n"
    )
    assert cross_references(text) == []
