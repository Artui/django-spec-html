from __future__ import annotations

from django.urls import path, reverse_lazy

from django_spec_html import SpecFormView
from docs.examples.spec_form import add_book_spec


# --8<-- [start:target]
class AddBookByTarget(SpecFormView):
    spec = add_book_spec
    success_url = reverse_lazy("books")

    def get_fragment_template_names(self) -> list[str]:
        if self.request.headers.get("HX-Target") == "dialog":
            return ["spec_form_dialog_fragment.html"]
        return super().get_fragment_template_names()


# --8<-- [end:target]

urlpatterns = [path("books/new/", AddBookByTarget.as_view(), name="add-book")]
