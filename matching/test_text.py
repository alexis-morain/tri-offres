from django.test import SimpleTestCase

from matching.text import clean_text, no_em_dash

GOOD = "Production de spectacles pour une compagnie de théâtre. CDI à Lyon, 2 400 € brut par mois."


class CleanTextTests(SimpleTestCase):
    """The checks of a text written by Claude before it is stored (contract of step 5, E14)."""

    def test_plain_text_kept(self):
        self.assertEqual(clean_text(GOOD, 400), GOOD)

    def test_spaces_normalized(self):
        self.assertEqual(clean_text("  Régie   générale\n\nd’un festival.\t", 400),
                         "Régie générale d’un festival.")

    def test_em_dash_replaced(self):
        self.assertEqual(clean_text("Régie générale — CDI à Nantes.", 400), "Régie générale, CDI à Nantes.")
        self.assertNotIn("—", clean_text("Régie—CDI", 400))

    def test_cut_at_the_last_space(self):
        text = "mot " * 200
        cut = clean_text(text, 400)
        self.assertLessEqual(len(cut), 400)
        self.assertTrue(cut.endswith("mot…"))
        self.assertEqual(clean_text("a" * 500, 400), "a" * 399 + "…")

    def test_cut_after_the_last_whole_sentence(self):
        first, second, third = "Régie générale d’un festival.", "CDI à Lyon, 2 400 € brut par mois !", "Temps plein ?"
        text = f"{first} {second} {third} Oui, du lundi au jeudi."
        two = f"{first} {second}"
        self.assertEqual(clean_text(text, len(two), sentences=True), two)
        self.assertEqual(clean_text(text, len(two) + 5, sentences=True), two)
        self.assertEqual(clean_text(text, len(two) + 1 + len(third), sentences=True), f"{two} {third}")
        self.assertEqual(clean_text(text, 400, sentences=True), text)
        self.assertEqual(clean_text("Version 2.5 du logiciel, sur 3 sites. Fin.", 40, sentences=True),
                         "Version 2.5 du logiciel, sur 3 sites.")

    def test_no_whole_sentence_within_the_limit_is_blank(self):
        self.assertEqual(clean_text("mot " * 200, 400, sentences=True), "")
        self.assertEqual(clean_text("Une phrase bien trop longue pour tenir.", 20, sentences=True), "")

    def test_short_text_not_cut(self):
        self.assertEqual(clean_text("Poste de régisseur.", 19), "Poste de régisseur.")

    def test_hostile_texts_refused(self):
        for text in [
            "Postulez sur https://exemple.fr/offre maintenant.",
            "Postulez sur www.exemple.fr maintenant.",
            "Postulez sur exemple.fr/postuler maintenant.",
            "Plus d’infos sur site.com.",
            "Voir emploi.re pour postuler.",
            "Postulez sur site.com",
            "Les détails sur jobs.careers aujourd’hui.",
            "Les détails sur www.x.immo aujourd’hui.",
            "Les détails sur offres.exemple-site.fr/ici.",
            "Profil : fr.linkedin.com/in/quelquun",
            "Écrire à recrutement@exemple.fr pour postuler.",
            "Écrire à recrutement [at] exemple [dot] fr pour postuler.",
            "Écrire à recrutement (arobase) exemple.fr pour postuler.",
            "Écrire à recrutement at exemple dot fr pour postuler.",
            "Écrire à recrutement ＠ exemple.fr pour postuler.",
            "Appeler le 06 12 34 56 78 pour postuler.",
            "Appeler le +33 6 12 34 56 78 pour postuler.",
            "Un poste <b>urgent</b> à pourvoir.",
            "Un poste &lt;b&gt; à pourvoir.",
            "Un poste ＜b＞ à pourvoir.",
            "Un poste ‹b› ou > à pourvoir.",
            "Voir exemple\u00ad.fr pour postuler.",
            "Voir https\u2063://x.fr pour postuler.",
            "Écrire à a\u2063@b.fr pour postuler.",
            "Appeler le tel 06\u206312 34 56 78.",
            "Voir exemple.\u2064fr pour postuler.",
            "Voir exemple.\u034ffr pour postuler.",
            "Un poste &\u2063lt;b à pourvoir.",
            "Écrire sur t.me/recruteur aujourd’hui.",
            "Écrire sur x.com/rh_exemple aujourd’hui.",
            "Voir t.co/abc aujourd’hui.",
            "Voir g.co/x aujourd’hui.",
            "Postulez sur node.js/postuler aujourd’hui.",
            "Postulez sur asp.net/apply aujourd’hui.",
            "— … !",
            "",
            "   ",
        ]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), "")

    def test_hostile_text_beyond_the_limit_refused(self):
        self.assertEqual(clean_text("mot " * 150 + "https://exemple.fr", 400), "")

    def test_technical_names_kept(self):
        for text in ["Développeur ASP.NET et VB.NET.", "Stack Node.js, Vue.js et socket.io.",
                     "Maintenance d’une application asp.net existante.", "Graphiques en d3.js et three.js."]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), text)

    def test_ordinary_abbreviations_kept(self):
        for text in ["Mission de 3,5 jours.", "Contact : M. Dupont.", "Société S.A.R.L. de spectacle.",
                     "Outils, e.g. tableurs, etc. Bac+5 exigé.", "Stack C# .NET et Vue.js.", "Version 2.0 du logiciel.",
                     "Développeur ASP.NET confirmé."]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), text)

    def test_invisible_characters_not_stored(self):
        self.assertEqual(clean_text("Ré\u00adgie gé\u2063né\u034frale.", 400), "Régie générale.")

    def test_original_form_stored(self):
        """Stored as written once spaces and em dashes are handled: the ellipsis and the typographic marks stay."""
        for text in ["Régie d’un festival… à Nantes.", "Cœur de métier : la régie « son » d’un théâtre, 50 %."]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), text)

    def test_french_texts_accepted(self):
        for text in [
            "Régisseur général pour un théâtre à Besançon : accueil des compagnies, sécurité, budget.",
            "Garçon de café, maîtrise de l’œuvre, « esprit d’équipe » exigé.",
            "CDI, 35 000 € brut par an, 39 h/semaine, Bac+5, 3,5 jours de télétravail.",
            "Développeur C# .NET et Vue.js, 1er étage, 2e poste ouvert, 50 % de déplacements.",
            "Contact par M. Dupont, S.A.R.L. de spectacle [anciennement], 12 € + tickets = 15 €.",
            "Missions : régie, son, lumière ; horaires variables ! Pourquoi pas vous ? #culture • 1 · 2 *",
            "Prix £ 30 ou $ 40, 20 °C, « citation » “guillemets” ‘simples’ 'droits' \"doubles\" (parenthèses).",
            "Post‐doctorat, non‑cadre, 2020–2024, réf/2.",
        ]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), text)

    def test_unicode_spaces_turned_into_spaces(self):
        self.assertEqual(clean_text("Missions\u202f: régie.\nPourquoi pas vous\u00a0? Lieu\u2003Nantes\u3000ici.",
                                    400), "Missions : régie. Pourquoi pas vous ? Lieu Nantes ici.")

    def test_characters_outside_the_allowlist_refused(self):
        for text in [
            "Poste de régie \U0001f3ad à Nantes.",  # emoji
            "Poste de régie \u2764\ufe0f à Nantes.",  # variation selector
            "Poste de régie\U000e0100 à Nantes.",  # variation selector supplement
            "Poste\u3164de régie.", "Poste\u115fde régie.", "Poste\u1160de régie.", "Poste\uffa0de régie.",  # fillers
            "Voir \u0435\u0445\u0435mple.fr pour postuler.",  # Cyrillic look-alikes
            "Voir exemple.f\u0433 pour postuler.",
            "Écrire à rh@ex\u3002fr pour postuler.", "Voir exemple\uff61fr pour postuler.",  # ideographic full stops
            "Appeler le \u0660\u0666 \u0661\u0662 \u0663\u0664 \u0665\u0666 \u0667\u0668.",  # Arabic-Indic digits
            "Poste de régie\x07 à Nantes.",  # control character
            "Re\u0301gie x\u0301.",  # a combining mark that composes into no allowed letter
            "Conﬁrmé en régie.",  # ligature
            "Poste à pourvoir, réf_42.", "Poste @ Nantes.", "Poste ^ Nantes.",
        ]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), "")

    def test_decomposed_accents_composed_and_kept(self):
        self.assertEqual(clean_text("Re\u0301gie ge\u0301ne\u0301rale.", 400), "Régie générale.")

    def test_extended_latin_letters_refused(self):
        for text in ["voir \u01c0inkedin.com", "voir exemp\u01c0e.fr", "voir exemp\u0196e.fr", "voir f\u0131verr.com",
                     "voir exempl\u0131.fr", "voir \u0192rance.fr", "voir exemple.\u0192r", "Poste à \u0141ódź.",
                     "Poste \u0111\u00fe\u0237\u017f\u0251."]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), "")

    def test_french_and_neighbour_letters_accepted(self):
        text = "àâäáãåçéèêëíìîïñóòôöõúùûüýÿæœ ÀÂÄÁÃÅÇÉÈÊËÍÌÎÏÑÓÒÔÖÕÚÙÛÜÝŸÆŒ ß."
        self.assertEqual(clean_text(text, 400), text)

    def test_bare_domains_in_capitals_refused(self):
        for text in ["Voir EXEMPLE.FR pour postuler.", "Voir Exemple.Fr pour postuler.", "Voir ASP.NET/apply.",
                     "Voir Node.JS/postuler.", "Voir œuvre.fr pour postuler.", "Voir straße.de pour postuler.",
                     "Voir www.technicien.ne pour postuler.", "Voir technicien.ne/postuler."]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), "")

    def test_inclusive_writing_and_technical_names_in_capitals_kept(self):
        for text in ["Un ou une technicien.ne, des salarié.es, un directeur.rice, un chargé.e, un conseiller.ère.",
                     "Développeur ASP.NET, VB.NET et Node.JS."]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), text)

    def test_disguised_phone_numbers_refused(self):
        for text in ["Appeler le tel 06–12–34–56–78.", "Appeler le 06 – 12 – 34 – 56 – 78.",
                     "Appeler le tel 06/12/34/56/78.",
                     "Appeler le tel 061 234 567 8.", "Appeler le tel 06123 45678.", "Appeler le tel 33 6 12 34 56 78.",
                     "Appeler le 0033 6 12 34 56 78.", "Appeler le 06‑12‑34‑56‑78."]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), "")

    def test_phone_numbers_with_any_separator_refused(self):
        for text in ["Tél. 06–12–34–56–78 – 35 h par semaine.", "CDI 2026 - 06–12–34–56–78.",
                     "Appeler le tel 06/12/34/56/78/2026.", "Appeler le tel (06) 12 34 56 78.",
                     "Appeler le 06·12·34·56·78.",
                     "Appeler le 06,12,34,56,78.", "Appeler le 06:12:34:56:78.", "Appeler le 06+12+34+56+78.",
                     "Appeler le 06×12×34×56×78.", "Appeler le +33 (0) 6 12 34 56 78.", "Appeler le +44 20 7946 0958.",
                     "Appeler le 0044 20 7946 0958."]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), "")

    def test_numbers_that_are_not_phones_kept(self):
        for text in ["Salaire de 35 000 à 45 000 €.", "Contrat du 01/09/2025 au 30/06/2026.", "Saison 2024-2025.",
                     "Durée de 39 h.", "Offre réf. 2025-0042.", "Salaire de 30 000 - 40 000 €.",
                     "Horaires 8h-12h / 14h-18h.", "Travail en 3×8."]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), text)

    def test_real_endings_only(self):
        for text in ["Stack Angular.js, Chart.js, Ember.js et jQuery.ajax.", "Accueil et régie.Profil confirmé.",
                     "Langage TypeScript, fichier main.ts."]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), text)
        for text in ["Voir exemple.fr.", "Voir site.com.", "Voir jobs.careers.", "Voir www.x.immo.", "Voir exemple.re.",
                     "Voir Lyon.Paris.", "Voir mon-asso.asso.", "Voir boutique.store/rh."]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), "")

    def test_more_symbols_and_letters_kept(self):
        text = "Marque® et Logiciel™, ≥ 3 ans, ≤ 5 ans, nº 4, 1ª, A | B, Škoda, Žižek, šž."
        self.assertEqual(clean_text(text, 400), text)

    def test_useful_symbols_kept(self):
        for text in ["Salaire ≈ 1 900 € net.", "Expérience de 3 ans ½.", "Bureau de 20 m².", "Début le 1ᵉʳ janvier.",
                     "Horaires 9h~17h.", "Voir le § 3.", "Équipe de 50 ± 5 personnes.", "Prime de 2 ‰, ¼ ou ¾, 10³.",
                     "Écran 24 × 36, accueil → régie ✓, 2ᵈ poste."]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), text)

    def test_contacts_seen_through_accents(self):
        for text in ["Voir exemplė.fr pour postuler.", "Voir éxemple.fr/postuler.", "Voir www.exèmple.fr aujourd’hui."]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), "")

    def test_no_em_dash(self):
        self.assertEqual(no_em_dash("Régie — CDI"), "Régie, CDI")
        self.assertEqual(no_em_dash(" Régie\u2014CDI "), "Régie, CDI")

    def test_ordinary_dots_and_numbers_kept(self):
        for text in ["Salaire de 2.5 k€ par mois.", "Bac+3. Permis B exigé.", "Développeur Node.js à Lille.",
                     "Poste à Paris 15e, horaires 9 h à 17 h."]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), text)


class DateAndTimeRangesTests(SimpleTestCase):
    """Dates with a four-digit year and clock times are not phone numbers, even joined by a dash."""

    def test_ranges_kept(self):
        for text in [
            "Mission du 01/12/2026 - 31/05/2027.", "Période : 01/12/2026-31/05/2027, CDD.",
            "Dates : 05/01/2027 – 30/06/2027.", "Du 01.12.2026 - 31.05.2027.",
            "Horaires 08:30-12:00 / 13:30-17:00.", "Horaires 07:30 - 15:30 / 16:00 - 18:00.",
        ]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), text)

    def test_phones_shaped_like_dates_or_times_still_refused(self):
        for text in ["tel 06/12/34/56/78/2026", "tel 06:12:34:56:78", "tel 06.12.34.56.78", "tel 01/02/20 26 12"]:
            with self.subTest(text=text):
                self.assertEqual(clean_text(text, 400), "")
