# Translations

What this package says to a person reads in the language active where it is
said: the pages' own text (`Save`, `Confirm`, `Search`, `Previous`, `Next`, the
page counts), and the words of the refusals it raises or answers, such as
`UnsupportedMediaType`'s default message and `The request body is not valid
JSON.`. Each is a msgid, looked up in the active language when it is shown, so
nothing is fixed at import. A message the caller passes,
`UnsupportedMediaType("Send JSON.")`, is shown as given.

## The source catalog

The package ships its source catalog,
`django_spec_html/locale/en/LC_MESSAGES/django.po`: every msgid it marks, the
Python `gettext` calls and the templates' `{% translate %}` and
`{% blocktranslate %}` text alike, untranslated. It is the file a contributed
language starts from, and it is generated (`make messages`), never edited by
hand. **No translated language ships.**

Django loads a catalog only from an app in `INSTALLED_APPS` or from a directory
in `LOCALE_PATHS`. The pages' templates are found only when `django_spec_html`
is listed in `INSTALLED_APPS` ([installing](index.md#installing)), so a project using
them has this package's catalog loading already, and a language compiled beside
the source catalog is used with nothing more. A project that gives every page a
`template_name` of its own, or uses only the JSON views, and does not list the
app loads none of it; list the app, or add a catalog to `LOCALE_PATHS`.

## The kernel's refusals

The refusals this package maps onto a response are the kernel's (`NotPermitted`,
`InvalidArguments`, `ServiceNotFound` and the rest), and their messages are the
kernel's catalog. It loads only when `django_service_specs` is listed too:

```python
INSTALLED_APPS = [
    # ...
    "django_service_specs",
    "django_spec_html",
]
```

The [translations section of the kernel's home page](https://artui.github.io/django-service-specs/#translations)
says which of its msgids are DRF's own, so that a project with DRF installed
keeps DRF's translation of them.

## Keeping one catalog

A project that keeps every translation in its own catalog, under
`LOCALE_PATHS`, cannot have `makemessages` collect these msgids: it reads the
project's source and never `site-packages`, and it marks each msgid it did not
find there obsolete. So combine the template it writes with each package's
source catalog, and merge that into each language, after every `makemessages`
run and every upgrade of this package:

```bash
HTML_PO="$(python -c 'import importlib.util, pathlib; print(pathlib.Path(importlib.util.find_spec("django_spec_html").origin).parent / "locale/en/LC_MESSAGES/django.po")')"
django-admin makemessages --locale de --keep-pot
msgcat --use-first locale/django.pot "$HTML_PO" --output-file locale/django.pot
msgmerge --update --backup=none locale/de/LC_MESSAGES/django.po locale/django.pot
django-admin compilemessages --locale de
```

`msgmerge` brings back each translation the `makemessages` run had marked
obsolete, so a msgid translated once stays translated. `--use-first` keeps the
project's own header and, for a msgid both catalogs hold, the project's entry.
Add the kernel's source catalog to the `msgcat` line the same way to carry its
msgids into the one catalog.
