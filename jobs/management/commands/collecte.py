"""Each night (step 10), before the scoring: run the actor locally on the active sources, import its dataset into
JobOffer, read its RUN_STATS record and print the report (the design plan)."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from jobs import collect, scraper
from supervision.redaction import redact_text


class Command(BaseCommand):
    help = "Lance l’actor en local sur les sources actives, importe les offres de son run et écrit le rapport."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Calcule et rapporte sans rien écrire (l’actor est quand même lancé).")
        parser.add_argument("--posted-within-days", type=int, metavar="N",
                            help="Offres publiées depuis N jours seulement (run de mesure).")
        parser.add_argument("--all-jobs", action="store_true",
                            help="Mode « all » de l’actor au lieu de « new-only » (run de mesure).")

    def handle(self, *args, dry_run=False, posted_within_days=None, all_jobs=False, **options):
        if settings.SCRAPER_PROXY_REQUIRED and not settings.SCRAPER_PROXY_URL.strip():
            raise CommandError("SCRAPER_PROXY_URL est vide alors que SCRAPER_PROXY_REQUIRED est posé : la collecte "
                               "ne sort jamais par l’IP de la box.")
        try:
            scraper.require_installed()
        except scraper.ScraperError as error:
            raise CommandError(str(error)) from None
        now = timezone.now()
        sources = collect.actor_sources()
        requested = collect.requested_sources(sources)
        if not requested:
            raise CommandError("Aucune source de l’actor active : rien à lui demander.")
        actor_input = scraper.build_input([source.actor_source for source in requested], posted_within_days,
                                          mode="all" if all_jobs else "new-only")
        self.stdout.write(f"Actor lancé sur {len(requested)} sources, attente de sa fin.")
        try:
            run = scraper.run(actor_input)
        except scraper.ScraperError as error:
            raise CommandError(str(error)) from None

        result = collect.import_items(scraper.iter_items(), sources, dry_run=dry_run)
        # The lines of the actor by source come from its RUN_STATS record; without it the sources stay as they were.
        # A failed read only costs those lines, never the import.
        stats, stats_error = None, ""
        try:
            stats = scraper.run_stats()
        except scraper.ScraperError as error:
            stats_error = str(error)
        failing = collect.track_sources(requested, run, result, now, stats=stats)
        report = collect.build_report(result, now, sources, run, requested=requested, failing=failing, stats=stats,
                                      stats_error=stats_error)
        # Text from the actor's side reaches the console, the TaskRun and the alert mails: one last mask (S18).
        self.stdout.write(redact_text("\n".join(collect.format_report(report))))
        errors = collect.failures(result, run)
        if errors:
            raise CommandError(redact_text("Collecte incomplète : " + " ; ".join(errors)))
