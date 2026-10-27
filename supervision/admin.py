from django.contrib import admin

from supervision.models import AlertSent, TaskRun

# The home of the admin: the original page behind a link to the dashboard (the template extends admin/index.html).
admin.site.index_template = "supervision/admin_index.html"


@admin.register(TaskRun)
class TaskRunAdmin(admin.ModelAdmin):
    """Read-only: the journal is written by the night command only."""

    list_display = ["slot", "day", "status", "duration_display"]
    list_filter = ["slot", "status"]
    date_hierarchy = "day"

    @admin.display(description="durée")
    def duration_display(self, obj):
        if obj.duration is None:
            return "-"
        minutes, seconds = divmod(int(obj.duration.total_seconds()), 60)
        return f"{minutes} min {seconds:02d} s"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AlertSent)
class AlertSentAdmin(admin.ModelAdmin):
    """Read-only: written by the alerts only."""

    list_display = ["slot", "day", "reason", "sent_at"]
    list_filter = ["slot"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
