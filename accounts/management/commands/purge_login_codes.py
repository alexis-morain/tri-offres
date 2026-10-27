from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import AdminLoginFailure, LoginCode

KEEP = timedelta(days=7)


class Command(BaseCommand):
    help = ("Delete login codes requested more than 7 days ago, and failed admin password attempts of the same age "
            "(run by the nightly task).")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Compte sans rien effacer.")

    def handle(self, *args, dry_run=False, **options):
        limit = timezone.now() - KEEP
        old = LoginCode.objects.filter(created_at__lt=limit)
        failures = AdminLoginFailure.objects.filter(created_at__lt=limit)
        if dry_run:
            self.stdout.write(f"{old.count()} login code(s) to delete (--dry-run).")
            self.stdout.write(f"{failures.count()} admin login failure(s) to delete (--dry-run).")
            return
        deleted, _ = old.delete()
        self.stdout.write(f"{deleted} login code(s) deleted.")
        deleted, _ = failures.delete()
        self.stdout.write(f"{deleted} admin login failure(s) deleted.")
