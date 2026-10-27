from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"
    verbose_name = "Comptes"

    def ready(self):
        from accounts import signals  # noqa: F401
