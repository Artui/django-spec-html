# API reference

Everything below is exported from `django_spec_html` directly, and from the
subpackage it is listed under. Import from the package root in application
code; the subpackage paths are where each name is defined. Kernel names these
views read - `ServiceSpec`, `dispatch`, the refusals - are documented on
[the kernel's site](https://artui.github.io/django-service-specs/reference/).

## Settings

This package defines none. The argument reader reads one of the kernel's:

- **`SERVICE_SPECS_MAX_ARRAY_LENGTH`**: the longest list an array parameter
  with no `max_length` of its own takes. It is the kernel's
  key, read by the kernel, and keeps its name here. See
  [how long a list may be](https://artui.github.io/django-service-specs/arguments/#how-long-a-list-may-be).

## Pages

::: django_spec_html.views.spec_form_view.SpecFormView
    options:
      inherited_members:
        - as_view
        - dispatch
        - get
        - post
        - get_context_data
        - get_success_url
        - get_success_message
        - get_pool_seeds
        - get_server_arguments
        - wants_fragment
        - commits
        - get_fragment_template_names
        - fragment_success
        - served_spec
::: django_spec_html.types.outcome.Outcome

## JSON views

::: django_spec_html.http.spec_view.SpecView
::: django_spec_html.http.async_spec_view.AsyncSpecView
::: django_spec_html.http.dispatch_request.dispatch_request
::: django_spec_html.http.adispatch_request.adispatch_request
::: django_spec_html.http.error_response.error_response

## Reading a request

::: django_spec_html.http.request_arguments.request_arguments
::: django_spec_html.http.route_arguments.route_arguments
::: django_spec_html.http.request_principal.request_principal
::: django_spec_html.http.add_argument_errors.add_argument_errors

## Refusals

::: django_spec_html.http.unsupported_media_type.UnsupportedMediaType
