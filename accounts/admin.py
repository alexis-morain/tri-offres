from django import forms
from django.contrib import admin

from accounts.models import LoginCode, OnboardingEvent, Profile, User, WaitlistEntry
from onboarding import criteria, cv, preferences, rome


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """Plain admin: no username and no password form, people sign in by magic link."""

    list_display = ["email", "status", "date_joined", "trial_ends_at", "is_active", "is_staff"]
    list_filter = ["status", "is_active", "is_staff", "trial_ends_at"]
    search_fields = ["email"]
    ordering = ["-date_joined"]
    readonly_fields = ["date_joined", "last_login"]
    filter_horizontal = ["groups", "user_permissions"]
    fieldsets = [
        (None, {"fields": ["email", "status", "trial_ends_at", "is_active"]}),
        ("Administration", {"fields": ["is_staff", "is_superuser", "groups", "user_permissions"],
                            "classes": ["collapse"]}),
        ("Dates", {"fields": ["date_joined", "last_login"]}),
    ]


class ProfileAdminForm(forms.ModelForm):
    """The questionnaire reads these answers: a hand edit must fit their schema, saved normalized."""

    class Meta:
        model = Profile
        fields = "__all__"

    @staticmethod
    def _schema(value, normalize):
        try:
            return normalize(value)
        except ValueError as error:
            raise forms.ValidationError(f"Ne correspond pas au schéma : {error}.") from error

    def clean_domains(self):
        return self._schema(self.cleaned_data["domains"], rome.normalize_domains)

    def clean_hard_criteria(self):
        return self._schema(self.cleaned_data["hard_criteria"], criteria.normalize)

    def clean_weighted_preferences(self):
        return self._schema(self.cleaned_data["weighted_preferences"], preferences.normalize)

    def clean_cv_profile(self):
        return self._schema(self.cleaned_data["cv_profile"], cv.normalize)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    form = ProfileAdminForm
    list_display = ["user", "domain_codes", "updated_at"]
    search_fields = ["user__email"]
    list_select_related = ["user"]
    readonly_fields = ["updated_at"]

    @admin.display(description="Domaines")
    def domain_codes(self, profile):
        return ", ".join(profile.domains)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Records written by the application: viewable and deletable, never typed in by hand."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(LoginCode)
class LoginCodeAdmin(ReadOnlyAdmin):
    # The hashes are never shown, neither in the list nor on the detail page.
    list_display = ["email", "created_at", "sent_at", "expires_at", "attempts", "used_at", "invalidated_at",
                    "requested_ip"]
    fields = list_display
    list_filter = ["created_at"]
    search_fields = ["email"]
    ordering = ["-created_at"]


@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(admin.ModelAdmin):
    list_display = ["email", "created_at"]
    search_fields = ["email"]
    ordering = ["created_at"]
    readonly_fields = ["created_at"]


@admin.register(OnboardingEvent)
class OnboardingEventAdmin(ReadOnlyAdmin):
    list_display = ["created_at", "name", "user"]
    list_filter = ["name", "created_at"]
    search_fields = ["user__email"]
    list_select_related = ["user"]
    ordering = ["-created_at"]
