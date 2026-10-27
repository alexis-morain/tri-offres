from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.db import transaction
from django.template.response import TemplateResponse

from jobs.models import JobOffer, Source
from onboarding import rome


class SourceAdminForm(forms.ModelForm):
    """The counter reads `domains`: a hand edit must hold known codes only, saved in the order of the list."""

    class Meta:
        model = Source
        fields = "__all__"

    def clean_domains(self):
        try:
            return rome.normalize_domains(self.cleaned_data["domains"], cap=None)
        except ValueError as error:
            raise forms.ValidationError("Une liste de codes de domaines ROME, par exemple [\"E\", \"K\"], ou [] "
                                        "pour toutes les personnes.") from error


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    form = SourceAdminForm
    list_display = ["name", "kind", "actor_source", "served_domains", "state", "empty_runs", "last_run_at", "owner",
                    "created_at"]
    list_filter = ["kind", "state"]
    search_fields = ["name", "url", "actor_source", "owner__email"]
    raw_id_fields = ["owner"]
    # Kept by the import.
    readonly_fields = ["empty_runs", "last_run_at"]
    actions = ["cut_and_delete_offers"]

    def save_model(self, request, source, form, change):
        # A source turned back to Active by hand starts its count of empty nights again.
        if source.state == Source.State.ACTIVE and "state" in form.changed_data:
            source.empty_runs = 0
        super().save_model(request, source, form, change)

    @admin.display(description="Domaines servis")
    def served_domains(self, source):
        return ", ".join(source.domains) or "Tous"

    def has_superuser_permission(self, request):
        return request.user.is_superuser

    @admin.action(description="Couper et supprimer ses offres", permissions=["superuser"])
    def cut_and_delete_offers(self, request, queryset):
        """A site asked to be removed (docs/retrait-source.md): the request outweighs the learning, so the offers
        go with their feedback."""
        offers = JobOffer.objects.filter(source__in=queryset)
        if request.POST.get("post") != "yes":
            context = {**self.admin_site.each_context(request), "title": "Couper et supprimer ses offres",
                       "opts": self.model._meta, "queryset": queryset, "offer_count": offers.count(),
                       "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME}
            return TemplateResponse(request, "admin/jobs/source/cut_confirmation.html", context)
        with transaction.atomic():
            count = offers.count()
            offers.delete()
            queryset.update(state=Source.State.DISABLED)
        self.message_user(request, f"{len(queryset)} source(s) coupée(s), {count} offre(s) supprimée(s).",
                          messages.SUCCESS)
        return None


@admin.register(JobOffer)
class JobOfferAdmin(admin.ModelAdmin):
    list_display = ["title", "employer", "city", "contract_type", "rome_code", "source", "published_at",
                    "collected_at"]
    list_filter = ["source", "contract_type", "remote_type", "country", "rome_by_claude"]
    search_fields = ["title", "employer", "city", "external_id", "rome_code"]
    # Written by the pre-pass of step 5.
    readonly_fields = ["summary_written_at", "rome_by_claude"]
    list_select_related = ["source"]
    date_hierarchy = "collected_at"
