from django.contrib import admin

from matching.models import Delivery, Feedback, Score, ScoringRun


@admin.register(Score)
class ScoreAdmin(admin.ModelAdmin):
    list_display = ["offer", "user", "score", "category", "is_exploration", "sent_on"]
    list_filter = ["category", "is_exploration", "sent_on"]
    search_fields = ["user__email", "offer__title", "offer__employer"]
    list_select_related = ["user", "offer"]
    raw_id_fields = ["user", "offer"]


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ["offer", "user", "verdict", "cancelled", "created_at"]
    list_filter = ["verdict", "cancelled"]
    search_fields = ["user__email", "offer__title", "offer__employer"]
    list_select_related = ["user", "offer"]
    raw_id_fields = ["user", "offer"]


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ["date", "user", "offer_count", "resend_id"]
    list_filter = ["date"]
    search_fields = ["user__email", "resend_id"]
    list_select_related = ["user"]
    raw_id_fields = ["user"]
    date_hierarchy = "date"


@admin.register(ScoringRun)
class ScoringRunAdmin(admin.ModelAdmin):
    """Read only: the runs are written by the scoring code. Deleting stays allowed: deleting an account cascades to
    its runs."""

    list_display = ["user", "kind", "state", "offers", "tokens", "started_at", "duration", "error"]
    list_filter = ["kind", "state"]
    search_fields = ["user__email"]
    list_select_related = ["user"]
    date_hierarchy = "started_at"

    @admin.display(description="offres notées / demandées")
    def offers(self, run):
        return f"{run.offers_scored} / {run.offers_requested}"

    @admin.display(description="jetons (entrée, sortie, cache lu, cache écrit)")
    def tokens(self, run):
        return ", ".join(str(getattr(run, field)) for field in ScoringRun.USAGE_FIELDS)

    @admin.display(description="durée")
    def duration(self, run):
        if run.finished_at is None:
            return "-"
        return f"{round((run.finished_at - run.started_at).total_seconds())} s"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
