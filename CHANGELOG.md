# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.2] — 2026-10-21

### Fixed
- **The documentation and the package summary no longer claim htmx support
  the page does not have.** `SpecFormView` reads no htmx header, so an htmx
  post is answered as any other: a success is the redirect, which the request
  follows itself, and a refusal is the whole page at its 4xx status, which htmx
  does not swap by default. The PyPI summary called the page htmx-capable and
  listed htmx among its keywords, the README and the documentation site named
  htmx as a client of the JSON views, and the page's documentation and the
  view's docstring named htmx as a client that reads a refused post's status.
  They now say what the page does, and that a fragment answer for htmx is
  planned for 0.2.0. The JSON views are described as serving a script or a
  `fetch()`, since htmx swaps HTML. Two tests hold the page's answer to an htmx
  post. No behaviour changed.

## [0.1.1] — 2026-10-21

### Changed
- **The kernel floor is now `django-service-specs>=0.12.0`, the first release
  without its own copy of these views.** Until then the kernel's root still
  exported the ten JSON names as classes and functions of its own, so
  `from django_service_specs import SpecView` resolved to a second, different
  class beside this package's, and an `isinstance` check against either missed
  the other. From 0.12.0 those names are imported from `django_spec_html`
  alone.

## [0.1.0] — 2026-10-21

### Added
- **`django-spec-html`: a `django-service-specs` spec served over plain Django,
  as a page and as JSON, the kernel's two HTTP halves moved out.** The JSON
  views are the kernel's `http/` subpackage as it shipped in
  `django-service-specs` 0.11.0, and `SpecFormView` is the page the kernel last
  shipped in 0.5.1, each moved with its history, tests and documentation so
  the kernel can carry no transport. `SpecFormView` serves a spec whose
  Validator is a `FormValidator` as the page that form is: GET renders it,
  showing the row an update targets; a valid POST dispatches and redirects;
  a refused one re-renders the form with the refusal placed on it. `SpecView`,
  `AsyncSpecView`, `dispatch_request` and `adispatch_request` serve a spec as
  JSON, and `error_response`, `request_arguments`, `route_arguments`,
  `request_principal`, `add_argument_errors` and `UnsupportedMediaType` come
  with them. The behaviour is unchanged but for the one change below; the
  import paths are this package's,
  `django_spec_html` at the root, with `django_spec_html.http` and
  `django_spec_html.views` where each name is defined. `UnsupportedMediaType`
  is this package's own refusal, answered 415 by its own `error_response`,
  and the array cap keeps the kernel's `SERVICE_SPECS_MAX_ARRAY_LENGTH` key.
  Until the kernel removes its own copy of the JSON views, the kernel's root
  still exports those ten names as the kernel's own classes and functions,
  not this package's: import them from `django_spec_html`. Neither
  package needs an entry in `INSTALLED_APPS`. Floors: Python 3.10, Django 4.2
  and `django-service-specs>=0.10.0`, the first kernel release with every
  name these views read at its root.

### Changed
- **`SpecFormView` answers an `UnsupportedMediaType` an operation raises at
  415**, re-rendering the form, where the kernel's page answered it 400. The
  page and the JSON views now read one map from each refusal to its status,
  so a refused post answers at the status a JSON client reads for the same
  refusal.

[Unreleased]: https://github.com/Artui/django-spec-html/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/Artui/django-spec-html/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Artui/django-spec-html/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Artui/django-spec-html/compare/v0.0.0...v0.1.0
