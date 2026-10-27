import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from config.testing import PLAIN_STATIC, assert_french_copy, assert_unique_ids, visible_text
from ui.context_processors import beta
from ui.contrast import contrast_ratio, read_tokens
from ui.templatetags.ui import keep_inversions
from ui.views import SpecimenForm
from ui.widgets import TONES, ChipsWidget

STYLESHEET = Path(settings.BASE_DIR) / "ui" / "static" / "ui" / "css" / "tri.css"

# (text token, background token, minimum ratio). 4.5 for text, 3 for UI component boundaries.
CONTRAST_PAIRS = [
    ("ink", "ground", 4.5),
    ("ink-muted", "ground", 4.5),
    ("ink", "surface", 4.5),
    ("ink-muted", "surface", 4.5),
    ("action", "ground", 4.5),
    ("action", "surface", 4.5),
    ("on-action", "action", 4.5),
    ("on-action", "action-hover", 4.5),
    ("stamp", "ground", 4.5),
    ("stamp", "surface", 4.5),
    ("on-action", "stamp", 4.5),
    ("error", "ground", 4.5),
    ("error", "surface", 4.5),
    ("field-border", "surface", 3),  # also the switch's track, off; its knob is ink on surface, on-action on action
    ("field-border", "ground", 3),
    ("focus", "ground", 3),
    ("action-hover", "action-tint", 4.5),  # the secondary button on hover, or armed by a swipe
    ("action", "action-tint", 4.5),
    ("green-ink", "surface", 4.5),  # « Une piste à côté » on the sheet
]
for card in ("pink", "blue", "yellow", "green"):
    CONTRAST_PAIRS += [
        ("ink", card, 4.5),
        (f"{card}-ink", card, 4.5),
        ("action", card, 4.5),
        (f"{card}-rule", card, 3),
        (f"{card}-rule", "ground", 3),
    ]


class ContrastHelperTests(SimpleTestCase):
    def test_black_on_white_is_21(self):
        self.assertAlmostEqual(contrast_ratio("#000000", "#ffffff"), 21, places=1)

    def test_same_colour_is_1(self):
        self.assertAlmostEqual(contrast_ratio("#777777", "#777777"), 1)

    def test_short_hex_is_accepted(self):
        self.assertAlmostEqual(contrast_ratio("#000", "#fff"), 21, places=1)

    def test_read_tokens_parses_hex_custom_properties(self):
        css = ":root {\n  --ink: #1b2230;\n  --font: 'Luciole';\n  --ground:#F4F6F9; }"
        self.assertEqual(read_tokens(css), {"ink": "#1b2230", "ground": "#f4f6f9"})


class PaletteContrastTests(SimpleTestCase):
    """Every text and control pairing of the palette meets RGAA AA."""

    def test_every_pair_meets_its_minimum(self):
        tokens = read_tokens(STYLESHEET.read_text())
        for fg, bg, minimum in CONTRAST_PAIRS:
            with self.subTest(fg=fg, bg=bg):
                self.assertIn(fg, tokens)
                self.assertIn(bg, tokens)
                self.assertGreaterEqual(contrast_ratio(tokens[fg], tokens[bg]), minimum)


class GaugeTests(SimpleTestCase):
    def test_ten_notches_with_the_score_filled(self):
        from django.template.loader import render_to_string
        html = render_to_string("ui/_gauge.html", {"score": 7})
        self.assertEqual(len(re.findall(r'class="gauge__notch[ "]', html)), 10)
        self.assertEqual(html.count("gauge__notch--on"), 7)
        self.assertIn("7 sur 10", html)


class StaticAssetsTests(SimpleTestCase):
    def test_fonts_are_served_from_the_project(self):
        for name in ("Luciole-Regular", "Luciole-Bold", "CourierPrime-Regular"):
            with self.subTest(font=name):
                self.assertIsNotNone(finders.find(f"ui/fonts/{name}.woff2"))

    def test_font_licences_ship_with_the_fonts(self):
        self.assertIsNotNone(finders.find("ui/fonts/LICENCES.md"))

    def test_stylesheet_never_loads_a_hosted_font(self):
        css = STYLESHEET.read_text()
        self.assertNotIn("googleapis", css)
        self.assertNotIn("@import", css)

    def test_motion_is_cut_by_reduced_motion(self):
        self.assertIn("prefers-reduced-motion", STYLESHEET.read_text())


class WidgetRenderingTests(SimpleTestCase):
    def test_chips_render_checkboxes_in_a_fieldset_with_legend_and_limit(self):
        html = SpecimenForm().as_div()
        self.assertIn("<fieldset", html)
        self.assertIn("<legend", html)
        self.assertIn('type="checkbox"', html)
        self.assertIn('data-max-choices="3"', html)
        self.assertIn("Le sens du travail", html)

    def test_options_render_radios(self):
        html = SpecimenForm().as_div()
        self.assertIn('type="radio"', html)
        self.assertIn("Sur place possible", html)

    def test_switch_renders_a_checkbox_with_switch_role(self):
        html = str(SpecimenForm()["preference"])
        self.assertIn('type="checkbox"', html)
        self.assertIn('role="switch"', html)

    def test_errors_are_linked_to_the_field(self):
        form = SpecimenForm(data={})
        form.is_valid()
        html = form.as_div()
        self.assertIn('aria-invalid="true"', str(form["email"]))
        self.assertIn("field-error", html)

    def test_chip_cap_is_enforced_by_the_form_without_javascript(self):
        form = SpecimenForm(data={"email": "nora@example.fr", "city": "Lyon", "remote": "any",
                                  "values": ["sens", "apprendre", "equipe", "autonomie"]})
        self.assertFalse(form.is_valid())
        self.assertIn("values", form.errors)
        form = SpecimenForm(data={"email": "nora@example.fr", "city": "Lyon", "remote": "any",
                                  "values": ["sens", "apprendre", "equipe"]})
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_choice_sent_twice_counts_once(self):
        form = SpecimenForm(data={"email": "nora@example.fr", "city": "Lyon", "remote": "any",
                                  "values": ["sens", "sens", "apprendre", "sens"]})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["values"], ["sens", "apprendre"])

    def test_unknown_chip_says_what_to_do(self):
        form = SpecimenForm(data={"values": ["bogus"]})
        form.is_valid()
        self.assertEqual(form.errors["values"], ["Choisissez seulement dans la liste."])

    def test_grouped_chips_are_labelled_groups_in_one_capped_container(self):
        html = str(SpecimenForm()["jobs"])
        self.assertEqual(html.count('class="chips"'), 1)
        self.assertRegex(html, r'<div class="chips" data-tone="blue" id="id_jobs" data-max-choices="2" data-grouped>')
        groups = re.findall(r'<div class="chips__group" role="group" aria-labelledby="([^"]+)">\s*'
                            r'<p class="chips__legend" id="([^"]+)">([^<]+)</p>\s*<div class="chips__list">', html)
        self.assertEqual([(label, legend) for label, _, legend in groups],
                         [("id_jobs_group_0", "Communication / Multimédia"),
                          ("id_jobs_group_1", "Plus précis, pour la culture et l’ESS")])
        self.assertTrue(all(label == legend_id for label, legend_id, _ in groups))
        self.assertEqual(html.count('type="checkbox"'), 3)
        assert_unique_ids(self, html)

    def test_chips_without_groups_render_as_before(self):
        html = str(SpecimenForm()["values"])
        self.assertNotIn("chips__group", html)
        self.assertNotIn("data-grouped", html)
        self.assertRegex(html, r'<div class="chips" data-tone="blue" id="id_values" data-max-choices="3">\s*'
                               r'<label class="chip"')

    def test_a_grouped_choice_is_validated_like_the_others(self):
        data = {"email": "nora@example.fr", "city": "Lyon", "remote": "any", "values": ["sens"]}
        form = SpecimenForm(data={**data, "jobs": ["mediation", "E11"]})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn("jobs", SpecimenForm(data={**data, "jobs": ["E11", "E14", "mediation"]}).errors)
        self.assertIn("jobs", SpecimenForm(data={**data, "jobs": ["E12"]}).errors)

    def test_unknown_tone_is_refused(self):
        with self.assertRaises(ValueError):
            ChipsWidget(tone="violet")

    def test_help_text_is_described_by(self):
        html = str(SpecimenForm()["values"].as_field_group())
        self.assertIn("aria-describedby", html)


@override_settings(STORAGES=PLAIN_STATIC)
class StylePageTests(TestCase):
    url = "/interne/style/"

    def test_hidden_from_anonymous_visitors(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_hidden_from_ordinary_users(self):
        self.client.force_login(User.objects.create_user("nora@example.fr"))
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_shown_to_staff_with_every_component(self):
        self.client.force_login(User.objects.create_user("alexis@example.fr", is_staff=True))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        for marker in ('class="button', 'class="button button--secondary', 'role="switch"', 'type="radio"',
                       'type="checkbox"', 'class="card card--pink', 'class="card card--blue',
                       'class="card card--yellow', 'class="card card--green', 'class="stamp',
                       'class="error-summary', 'class="gauge'):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)

    @override_settings(DEBUG=True)
    def test_shown_to_anyone_in_debug(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)


@override_settings(STORAGES=PLAIN_STATIC)
class BaseTemplateTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user("alexis@example.fr", is_staff=True))
        self.html = self.client.get("/interne/style/").content.decode()

    def test_page_is_french_with_viewport_and_skip_link(self):
        self.assertIn('<html lang="fr"', self.html)
        self.assertIn('name="viewport"', self.html)
        self.assertIn('href="#contenu"', self.html)
        self.assertIn('id="contenu"', self.html)

    def test_no_third_party_requests(self):
        for host in ("googleapis", "gstatic", "cdn.", "unpkg", "jsdelivr"):
            self.assertNotIn(host, self.html)

    def test_french_copy(self):
        self.assertNotIn("—", self.html)
        assert_french_copy(self, self.html)

    def test_ids_are_unique(self):
        assert_unique_ids(self, self.html)

    def test_error_summary_links_point_at_existing_controls(self):
        summary = self.html[self.html.index("error-summary"):]
        summary = summary[:summary.index("</ul>")]
        targets = re.findall(r'href="#([^"]+)"', summary)
        self.assertTrue(targets)
        for target in targets:
            self.assertRegex(self.html, rf'<(input|textarea|select)[^>]*\sid="{re.escape(target)}"')

    def test_reverse_name(self):
        self.assertEqual(reverse("ui:style"), "/interne/style/")


class KeepInversionsTests(SimpleTestCase):
    def test_inverted_verbs_never_split(self):
        self.assertEqual(keep_inversions("Où voulez-vous travailler ?"),
                         'Où <span class="nowrap">voulez-vous</span> travailler ?')
        self.assertEqual(keep_inversions("Combien d’années d’expérience avez-vous ?"),
                         'Combien d’années d’expérience <span class="nowrap">avez-vous</span> ?')
        self.assertEqual(keep_inversions("Que va-t-il faire ?"), 'Que <span class="nowrap">va-t-il</span> faire ?')
        self.assertEqual(keep_inversions("Ce qui compte <b>"), "Ce qui compte &lt;b&gt;")
        self.assertEqual(keep_inversions("Le sous-titre"), "Le sous-titre")
        self.assertEqual(keep_inversions("Est-ce clair ?"), '<span class="nowrap">Est-ce</span> clair ?')


class SwitchKnobTests(SimpleTestCase):
    def test_off_knob_is_ink_not_grey(self):
        css = STYLESHEET.read_text()
        knob = css[css.index(".switch__input::before {"):]
        self.assertIn("background: var(--ink);", knob[:knob.index("}")])


@override_settings(STORAGES=PLAIN_STATIC)
class BetaWordingTests(TestCase):
    """The words « bêta gratuite » are shown during the beta only (sub-step 8.2, decision 10)."""

    FOOTER = "tri-offres, bêta gratuite"

    def footer(self, path="/confidentialite/"):
        html = self.client.get(path).content.decode()
        inside = html.split("<footer", 1)[1].split(">", 1)[1]
        return " ".join(visible_text(inside).split())

    def test_the_context_processor_reads_the_setting(self):
        for value in (True, False):
            with self.subTest(beta=value), override_settings(BETA=value):
                self.assertEqual(beta(None), {"beta": value})

    def test_the_processor_is_registered(self):
        processors = settings.TEMPLATES[0]["OPTIONS"]["context_processors"]
        self.assertIn("ui.context_processors.beta", processors)

    @override_settings(BETA=True)
    def test_in_beta_the_footer_and_the_home_note_say_it(self):
        self.assertTrue(self.footer().startswith(self.FOOTER))
        page = " ".join(visible_text(self.client.get("/").content.decode()).split())
        self.assertIn(f"Bêta gratuite, {settings.BETA_CAPACITY} places. Environ 4 minutes, 3 étapes.", page)

    @override_settings(BETA=False)
    def test_outside_the_beta_nothing_says_free(self):
        self.assertTrue(self.footer().startswith("tri-offres Confidentialité"))
        for path in ("/", "/confidentialite/", "/mentions-legales/"):
            with self.subTest(path=path):
                html = self.client.get(path).content.decode()
                self.assertNotIn("gratuit", html.lower())
        page = " ".join(visible_text(self.client.get("/").content.decode()).split())
        self.assertIn("Environ 4 minutes, 3 étapes.", page)
        self.assertNotIn("Bêta", page)

    @override_settings(BETA=False)
    def test_the_texts_about_a_full_beta_stay(self):
        with mock.patch("accounts.views.auth.beta_is_full", return_value=True):
            page = " ".join(visible_text(self.client.get("/").content.decode()).split())
        self.assertIn("La bêta est complète.", page)


class NoPaymentWordsTests(SimpleTestCase):
    """No template talks about money (the French labour code forbids charging people who look for a job). Templates only:
    the team's style page is left out, and neither « € » nor « euro » is refused (a salary is a legitimate figure)."""

    WORDS = re.compile(r"\b(prix|tarifs?|payants?|paiements?|payer|carte bancaire|stripe|abonnements?)\b",
                       re.IGNORECASE)
    EXCLUDED = {Path("ui/templates/ui/style.html")}

    def templates(self):
        base = Path(settings.BASE_DIR)
        found = [path for path in base.glob("*/templates/**/*") if path.suffix in (".html", ".txt")]
        return [path for path in found if path.relative_to(base) not in self.EXCLUDED]

    def test_templates_are_found(self):
        names = {path.name for path in self.templates()}
        self.assertTrue({"base.html", "home.html", "welcome.txt", "morning.html", "sort.html"} <= names)
        self.assertNotIn("style.html", names)

    def test_no_template_has_a_payment_word(self):
        hits = [f"{path.relative_to(settings.BASE_DIR)}: {match.group(0)}" for path in self.templates()
                for match in self.WORDS.finditer(path.read_text(encoding="utf-8"))]
        self.assertEqual(hits, [])

    def test_the_words_catch_what_they_should(self):
        for sample in ("Le Prix", "nos tarifs", "Abonnement", "un abonnement payant", "paiement", "à payer",
                       "Carte bancaire", "via STRIPE"):
            with self.subTest(sample=sample):
                self.assertTrue(self.WORDS.search(sample))
        self.assertFalse(self.WORDS.search("Salaire : 32 000 € ou 30 euros par heure, surprise"))
