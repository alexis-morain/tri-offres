"""Each night, after `collecte`: WTTJ alone, on the job categories of the domains of the active profiles (plan of
06/11). Read whole, WTTJ stopped at 1000 offers or 30 minutes and never read the rest."""
from jobs import collect, scraper
from jobs.management.commands import collecte
from matching.selection import active_profiles


class Command(collecte.Command):
    help = "Lance l’actor sur WTTJ seule, sur les catégories de métier des domaines des profils actifs."

    def plan(self, requested, posted_within_days, mode):
        requested = [source for source in requested if source.actor_source == collect.WTTJ]
        if not requested:
            self.stdout.write("WTTJ désactivée : pas de run ciblé.")
            return None
        profiles = list(active_profiles())
        professions = collect.wttj_professions(profiles)
        if not professions:
            self.stdout.write("WTTJ : aucune catégorie dans les profils actifs, pas de run ciblé.")
            return None
        countries, languages = collect.wttj_countries_and_languages(profiles)
        self.stdout.write(f"Catégories WTTJ ({len(professions)}) : {', '.join(professions)}.")
        self.stdout.write(f"Pays : {', '.join(countries)} ; langues : {', '.join(languages)}.")
        return requested, scraper.build_input([collect.WTTJ], posted_within_days, mode=mode,
                                              wttj_professions=professions, countries=countries, languages=languages)
