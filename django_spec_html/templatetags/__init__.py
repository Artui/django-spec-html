"""The template library a page's rows are asked about in: ``{% load django_spec_html %}``.

A subpackage at the root because Django finds a tag library only in an
installed app's ``templatetags`` package. It re-exports nothing: a template
loads it by its file's name, and Python reads ``available`` from that file.
"""

__all__: list[str] = []
