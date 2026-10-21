# django-spec-html

[![CI](https://github.com/Artui/django-spec-html/workflows/tests/badge.svg)](https://github.com/Artui/django-spec-html/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/django-spec-html.svg)](https://pypi.org/project/django-spec-html/)
[![Python versions](https://img.shields.io/pypi/pyversions/django-spec-html.svg)](https://pypi.org/project/django-spec-html/)
[![Django versions](https://img.shields.io/pypi/djversions/django-spec-html.svg)](https://pypi.org/project/django-spec-html/)
[![Docs](https://img.shields.io/badge/docs-artui.github.io-blue.svg)](https://artui.github.io/django-spec-html/)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Artui/django-spec-html/gh-pages/coverage.json)](https://github.com/Artui/django-spec-html/actions/workflows/tests.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/pypi/l/django-spec-html.svg)](LICENSE)


A [`django-service-specs`](https://github.com/Artui/django-service-specs) spec as a
server-rendered page: the form is the validator, every page works as a plain form
post, and the same view answers htmx with a fragment. The plain-Django JSON views
ride along beside the pages, for a client that wants JSON without an API framework.

## License

MIT. See [LICENSE](LICENSE).
