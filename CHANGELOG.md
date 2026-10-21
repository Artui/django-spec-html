# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  with them. The behaviour is unchanged; the import paths are this package's,
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

[Unreleased]: https://github.com/Artui/django-spec-html/compare/v0.0.0...HEAD
