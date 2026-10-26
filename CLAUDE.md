# django-spec-html

Guidance for working in this repository.

## What this package is

A `django-service-specs` spec served as a server-rendered page. `SpecFormView`
renders a spec's form, validates a post through it, runs the kernel's
`dispatch`, and answers as a plain form post: a redirect on a success, and the
page again with the refusal on its form. An htmx request decides the same
outcome and is answered with a fragment of the page instead: the form at 200 for
a refusal the page re-renders, `HX-Redirect` or a result fragment for a success,
and a denial raised in both modes.
Beside the pages, `http/` serves a spec as plain-Django JSON (`SpecView`,
`AsyncSpecView`, `dispatch_request`, `error_response`, `request_arguments`), for
a client that wants JSON with no API framework.

Both halves came out of the kernel with their history so that the kernel can
carry no transport: `http/` as the kernel shipped it, and `SpecFormView` as it
last shipped in kernel 0.5.1. Two positions follow from the move:

- **The kernel is read through its root and nothing else.** Every kernel name
  this package uses is in `django_service_specs.__all__`, and every import of
  one goes through `from django_service_specs import ...`. A separate
  distribution that reaches a kernel module path breaks on the kernel's next
  internal move, and nothing here would fail first.
- **The kernel's behaviour is not restated here.** Dispatch, authorization,
  argument coercion and the refusal types are the kernel's; this package maps
  them onto a request and a response.

## Commands

| Command | Does |
| --- | --- |
| `make init` | Sync all dependency groups and install the pre-commit hooks |
| `make test` | pytest with the 100% line+branch coverage gate |
| `make lint` | `ruff check` plus `ty check` |
| `make format-check` | `ruff format --check --diff` (CI runs this; `make lint` does not) |
| `make docs-build` | `mkdocs build --strict` |
| `make release-bump VERSION=X.Y.Z` | Rewrite the version and promote the changelog section |

## Structural rules

Non-negotiable. They keep the package navigable, and they are the kernel's.

1. **One exported class or function per file.** The file is named after the
   exported symbol in `snake_case`.
2. **Private helpers used only in one file** stay in that file with a `_name`
   prefix.
3. **Non-exported helpers shared across files** go in that package's `utils.py`.
4. **Top-level imports only.** Lazy or function-local imports are forbidden
   unless the import targets a declared optional dependency gated behind an
   opt-in. Document the reason inline. A circular import is never the reason
   (rule 9).
5. **Full type annotations on every function and method signature.** `Any` is
   allowed only at the Django boundary where the type genuinely is `Any`.
6. **`__init__.py` is the only re-export point.** Each `__init__.py` lists the
   public surface in `__all__`. Internal modules import from leaf paths, never
   from the package's `__init__`.
7. **The package root is a table of contents, not a drawer.** It holds
   `__init__.py` and `version.py`. Everything else lives in a subpackage named
   for a **concern**: `http/` (a spec as plain-Django JSON, and the request
   readers both halves use) and `views/` (a spec as a page). `types/` is the
   one standing subpackage, for value types, when there are any.
8. **`types/` imports nothing else in the package.** A subpackage's `__init__`
   re-exports its whole surface, so a module every subpackage imports must sit
   in a package whose `__init__` pulls in nothing.
9. **No `if TYPE_CHECKING:` guard.** A guard hides an import cycle from the
   interpreter while the type checker still sees it. The ban is ruff's
   `TID251`, so `make lint` and CI run it. Break a cycle by structure, never by
   a function-local import (rule 4).

## Layout

| Subpackage | Holds |
| --- | --- |
| `views/` | `SpecFormView`, a spec served as the page its form is, with what only that page needs (the fields it holds back, the question it asks); `SpecFormSetView`, a `many` spec served as a formset of its form; `SpecConfirmView`, a spec with no Validator served as a target and a button; `utils.py` for `SpecPageBase`, the private base every page view subclasses (admission, the target and its display, dispatch, the refusal, the success and its message, the fragment wrapper, the commit rule), whose hooks a subclass supplies and `as_view()` refuses a class missing, and the form readers both form pages share (`form_values`, `without_errors`) |
| `types/` | `Outcome`, the ways a page's post ends; it imports nothing else in the package (rule 8) |
| `http/` | the JSON views (`SpecView`, `AsyncSpecView`, `dispatch_request`, `adispatch_request`), `error_response`, and the request readers both halves use: `request_arguments`, `route_arguments`, `request_principal`, `add_argument_errors`; `UnsupportedMediaType`, beside its raiser; `utils.py` for `SpecViewBase`, the helpers the entry points share, and `REFUSAL_STATUSES`, the status map the page reads too, with each refusal's `Outcome` |

`views/` imports `http/`, never the reverse. The root re-exports both
subpackages' `__all__` and `__version__`, and `tests/test_init.py` holds that.

## Naming the concepts

**One spelling each, so a rename is a replace.** No aliases, no
backwards-compatible second name, no re-export under an old spelling.
`django-spec-html` and `django_spec_html` appear verbatim and nowhere derived.

## Constraints that look like tidy-ups

Each of these reads as an oversight and is not.

- **`SERVICE_SPECS_MAX_ARRAY_LENGTH` is the kernel's setting key.** The argument
  reader names it, and the kernel reads it. It is not this package's to rename.
- **The test settings list no kernel app.** The package needs nothing of the
  kernel switched on, and the suite shows it.
- **The kernel no longer ships these names.** `django_service_specs.http` and
  its ten root names are gone from kernel 0.12.0, below the floor, so an
  import left pointing at the kernel fails at import rather than resolving to
  a second copy. `tests/test_init.py` holds that absence, and would fail on a
  floor lowered past the removal.
- **There are two `NON_FIELD_ERRORS`.** The kernel's, `"non_field_errors"`, is
  the key a refusal's tree uses, read from the kernel's root; Django's,
  `"__all__"`, is the key a bound form uses, and `add_argument_errors` imports
  it as `FORM_NON_FIELD_ERRORS`. A rewrite by name swaps one for the other.
- **`UnsupportedMediaType` is this package's refusal, not the kernel's.** The
  kernel cannot import a transport, so no map in the kernel names it, and
  `error_response` here answers it 415. `tests/http/test_unsupported_media_type.py`
  raises this package's class through each JSON entry point, since a row
  that missed it would answer it 400 and every other test would pass.
- **The test project keeps the kernel's names** (`tests.adapter_app`,
  `tests.dispatch_app`, `tests.dispatch.utils`), so a test read beside
  the kernel's own tests names the same models and doubles. A helper only one test
  module uses lives in that module; a `tests/<x>/utils.py` holds what several
  share. The docs examples declare their own specs and form
  rather than importing the kernel's examples, so the pages show what they
  serve.
- **The page and the JSON views read one status map**, `REFUSAL_STATUSES` in
  `http/utils.py`, with a row for every refusal even where its status is its
  base's, so the kernel's `unanswered_refusals` reads the whole truth.
  `error_response` and the page choose only the body by kind. Each half is
  held by a docs test reading its page's table
  (`tests/docs/test_spec_form_examples.py`, `tests/docs/test_http_examples.py`).
- **`SpecFormView.spec` takes a `ServiceSpec` only**, where the JSON views take
  a `SpecSource` too. That is the page as it shipped, not an omission to
  widen in passing.

## Tests

`tests/` mirrors the source tree, one file per source file with the same name.
`pytest-asyncio` runs in auto mode. The coverage gate is **100% line and
branch**.

Never `# pragma: no cover`. If a branch cannot be reached, that is a signal to
restructure the code, not to exempt it.

- **No DRF in any environment the suite runs in.** The kernel exists for
  dispatch without it, and the `floor` job's install-alone step asserts the
  same thing from outside.
- **Where several checks can answer one input, size the test so the one it
  names is the one that answers**, and assert which message came back.
- **A multi-condition guard is one branch arc.** Name, beside the guard, the
  test that fails without each condition.

## Type checking

`ty`, scoped to `django_spec_html` via `[tool.ty.environment]`. The package
ships `py.typed`, so consumers get the annotations.

Never a mypy-style `# type: ignore` in the package - a pre-commit hook rejects
it.

## Linting and formatting

`ruff` is the source of truth for both. Use `...` rather than `pass` for empty
bodies.

`make lint` does **not** run `ruff format --check`, and CI does. Run
`uv run ruff format --check` before pushing.

## Imports inside the package

Absolute and fully qualified, never relative - `ban-relative-imports = "all"` is
configured and enforced. isort order is stdlib, third party, first party.

`from __future__ import annotations` goes at the top of every annotated file;
`required-imports` enforces it, and re-export `__init__.py` files are exempt.

## Compatibility floor

| | Minimum | Tested against |
| --- | --- | --- |
| Python | 3.10 | 3.10 through 3.14 |
| Django | 4.2 | 4.2, 5.0, 5.1, 5.2, 6.0, 6.1 |
| django-service-specs | 0.17.0 | the newest release, and the floor in the `floor` job |

The Python and Django floors are the kernel's, and cannot sit below them.

## CI and pre-commit

Seven jobs in `tests.yml`: `lint`, `docs`, `floor` (resolves every declared
dependency at the bottom of its window and runs the suite there), `test` (the
Python x Django matrix), `coverage-badge`, `secrets`, and the `tests-passed`
gate that branch protection points at. `upstream-drift.yml` resolves the newest
versions `pyproject.toml` allows on a schedule, which is how a kernel release
that breaks this package is found.

Pre-commit runs gitleaks, the standard hygiene hooks, ruff, ty, four convention
guards (no local filesystem paths, no internal plan-step labels, no mypy-style
type-ignore, no emoji or marker glyphs in any committed file) and
`check-changelog`, which refuses a changelog a merge or rebase has silently
rearranged.

## Releasing

```
make release-bump VERSION=X.Y.Z   # rewrites version.py, promotes the changelog
# edit CHANGELOG.md to fill in the new section, review the diff
# open a PR, get it reviewed, merge to main
```

Releases are **main-triggered**. The release job on `main` short-circuits to a
no-op when a `vX.Y.Z` tag for the version in source already exists on origin,
so an ordinary merge costs nothing. That guard is why the tag list starts at
`v0.0.0`, a version never published: a repository with no tags reads its
version as unreleased on the first push to `main` and runs a real release
attempt. `0.1.0` is the first version on PyPI.

Publishing goes through a PyPI Trusted Publisher that names this repo, the
workflow `release.yml` and the environment `pypi`. It lives on PyPI rather than
in a checkout, so renaming that workflow file or that environment breaks the
next release and nothing here fails first.
