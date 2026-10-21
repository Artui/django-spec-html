"""FilterSets the filter tests share: real django-filter classes, never stand-ins.

The reading is checked against what django-filter itself reads, so each one is a
real ``FilterSet`` over a model the suite migrates.
"""

from __future__ import annotations

from typing import Any

import django_filters
from django.contrib.auth.models import User

from tests.dispatch_app.models import Note


class NumberInFilter(django_filters.BaseInFilter, django_filters.NumberFilter):
    """``?pk__in=1,2``: a CSV filter takes its element type from its second base."""


class NumberCsvRangeFilter(django_filters.BaseRangeFilter, django_filters.NumberFilter):
    """``?pk__range=1,5``: two values in one argument, unlike ``RangeFilter``."""


class NoteFilters(django_filters.FilterSet):
    """Every shape a note can be filtered by, with the lookup each one names."""

    title = django_filters.CharFilter()
    title_contains = django_filters.CharFilter(field_name="title", lookup_expr="icontains")
    archived = django_filters.BooleanFilter()
    min_pk = django_filters.NumberFilter(field_name="pk", lookup_expr="gte")
    pks = NumberInFilter(field_name="pk", lookup_expr="in")
    pk_between = NumberCsvRangeFilter(field_name="pk", lookup_expr="range")
    pk_range = django_filters.RangeFilter(field_name="pk")
    kind = django_filters.ChoiceFilter(
        field_name="title", choices=[("draft", "Draft"), ("final", "Final")]
    )
    kinds = django_filters.MultipleChoiceFilter(
        field_name="title", choices=[("draft", "Draft"), ("final", "Final")]
    )
    owner = django_filters.ModelChoiceFilter(queryset=User.objects.all())
    owners = django_filters.ModelMultipleChoiceFilter(
        field_name="owner", queryset=User.objects.all()
    )
    explained = django_filters.CharFilter(
        field_name="title", lookup_expr="startswith", help_text="Titles starting with this."
    )


def notes() -> Any:
    return Note.objects.all()
