from django.core.management.base import BaseCommand
from django.db import transaction

from jobs import geo
from jobs.models import JobOffer
from onboarding.criteria import format_number

BATCH_SIZE = 500


# Fields the command reads again, with their name in its report.
FIELDS = {"insee": "commune", "zone_postal_code": "code postal", "department_code": "département",
          "region_code": "région"}


class Command(BaseCommand):
    """Reads again the commune, the postal code of the zone, the department and the region of the offers already in
    the database, by the rule of the import (jobs.geo.place_for), without a new collection
    (the design plan, decision 3; the design plan, decision 4). The
    match key stays as it is."""

    help = ("Relit la commune, le code postal de la zone, le département et la région des offres en base depuis "
            "leur lieu, sans nouvelle collecte.")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Compte sans rien écrire.")

    def handle(self, *args, dry_run=False, **options):
        read = 0
        counts = dict.fromkeys(FIELDS, 0)
        changed = []
        rows = JobOffer.objects.values_list("pk", "location", "city", "postal_code", "department_code", "region",
                                            *FIELDS)
        for pk, location, city, postal_code, department_code, region, *before in rows.iterator(chunk_size=2000):
            read += 1
            place = geo.place_for(location, city, postal_code, department_code, region)
            after = dict(zip(FIELDS, place))
            if list(after.values()) == before:
                continue
            for name, old in zip(FIELDS, before):
                counts[name] += after[name] != old
            changed.append(JobOffer(pk=pk, **after))
        if not dry_run:
            with transaction.atomic():
                JobOffer.objects.bulk_update(changed, list(FIELDS), batch_size=BATCH_SIZE)
        label = "relire_lieux (--dry-run, rien d’écrit)" if dry_run else "relire_lieux"
        verb = "changeraient" if dry_run else "changent"
        detail = ", ".join(f"{FIELDS[name]} : {format_number(count)}" for name, count in counts.items())
        self.stdout.write(f"{label} : {format_number(read)} offres relues, {format_number(len(changed))} {verb} "
                          f"({detail})")
