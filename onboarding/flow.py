"""Order of the questionnaire screens, and where a person resumes.

Blocks are described here only. A screen joins its block by its place in STEPS. The CV and summary screens have views
of their own (onboarding/views.py); the others go through the generic screen view.
"""
import math
from collections.abc import Callable
from dataclasses import dataclass

from django.urls import reverse

from onboarding import criteria, cv, forms, preferences


@dataclass(frozen=True)
class Block:
    slug: str
    name: str
    tone: str  # card colour, see ui/widgets.py TONES
    # Every row (key, label, text) its card can show, read from a profile whose answers fit their schemas.
    rows: Callable
    # Whether its screens filter offers: they show the live counter of yesterday's offers.
    has_counter: bool = False


@dataclass(frozen=True)
class Step:
    slug: str
    block: Block | None  # None: outside the blocks, the search screen before them and the summary after
    title: str
    why: str
    form_class: type
    seconds: int  # estimated time to answer
    # Wording for a person in career change (Profile.career_change), when the screen has one.
    career_change_title: str = ""
    career_change_why: str = ""
    # Name of the screen's own URL, when it is not served by the generic screen view at /questionnaire/<slug>/.
    url_name: str = ""


def _exclude_rows(profile):
    return criteria.describe(criteria.normalize(profile.hard_criteria))


def _prefer_rows(profile):
    return preferences.describe(preferences.normalize(profile.weighted_preferences))


def _words_rows(profile):
    return preferences.describe_words(profile.seek_text, profile.refuse_text) + cv.describe(profile.cv_profile)


EXCLUDE = Block("exclude", "Ce que vous excluez", "pink", _exclude_rows, has_counter=True)
PREFER = Block("prefer", "Ce que vous préférez", "blue", _prefer_rows)
WORDS = Block("words", "En vos mots", "yellow", _words_rows)
BLOCKS = [EXCLUDE, PREFER, WORDS]

STEPS = [
    Step("recherche", None, "Quel poste cherchez-vous ?",
         "Oriente tout le reste : les offres lues chaque nuit et les questions suivantes.", forms.SearchForm, 45),
    Step("lieu", EXCLUDE, "Où voulez-vous travailler ?",
         "Sert à écarter les offres trop loin de chez vous.", forms.LocationForm, 45),
    Step("contrats", EXCLUDE, "Quels contrats acceptez-vous ?",
         "Les autres contrats ne vous seront pas montrés.", forms.ContractsForm, 20),
    Step("mots-cles", EXCLUDE, "Des mots à exiger ou à écarter dans le titre ?",
         "Facultatif. Ce filtre lit le titre des offres seulement, sans tenir compte des accents ni des majuscules.",
         forms.KeywordsForm, 20),
    Step("remuneration", EXCLUDE, "Quelle rémunération minimale ?",
         "Sert à écarter les offres trop peu payées. Celles qui n’affichent pas de salaire restent visibles.",
         forms.PayForm, 30),
    Step("temps", EXCLUDE, "Quel temps de travail ?",
         "Sert à écarter les temps partiels, ou au contraire les temps pleins.", forms.HoursForm, 15),
    Step("experience", EXCLUDE, "Combien d’années d’expérience avez-vous ?",
         "Sert à écarter les postes de direction ou ceux qui demandent bien plus d’expérience "
         "que vous n’en avez.", forms.ExperienceForm, 15),
    Step("valeurs", PREFER, "Ce qui compte le plus dans votre prochain poste",
         "Règle le ton de la notation. Ne retire aucune offre.", forms.ValuesForm, 20),
    # Only for the accounts that picked job chips before the postes were typed (forms.JobsForm.applies).
    Step("metiers", PREFER, "Les métiers que vous visez",
         "Les offres de ces métiers remontent en tête.", forms.JobsForm, 30,
         career_change_title="Les métiers qui vous attirent, même sans expérience directe",
         career_change_why="Je garderai aussi une ou deux offres d’exploration par mail."),
    # `answered` reads the structures only: an account from before the sectors keeps its place ("Non renseigné").
    Step("structures", PREFER, "Où aimeriez-vous travailler ?",
         "Ces préférences font monter la note des offres qui y correspondent. Elles n’en écartent aucune.",
         forms.StructuresForm, 30),
    Step("mots", WORDS, "En vos mots",
         "Facultatif, mais c’est ce qui affine le plus la notation. Écrivez comme vous le diriez à quelqu’un.",
         forms.WordsForm, 60),
    Step("cv", WORDS, "Votre CV (facultatif)",
         "Aide à repérer les compétences transférables, utile en reconversion.", forms.CVScreen, 60),
    # After the three blocks, outside them.
    Step("synthese", None, "Voici ce que j’ai compris",
         "Relisez avant le premier tri : vos filtres écartent des offres, la synthèse oriente leur note.",
         forms.SummaryScreen, 30, url_name="onboarding:summary"),
]
SUMMARY = "synthese"
# Where a screen opened with ?retour=<value> goes back once saved, and the wording of its way back: an allowlist,
# never a URL from the request.
RETURNS = {"synthese": ("onboarding:summary", "Retour à la synthèse"),
           "ma-recherche": ("onboarding:my_search", "Retour à «\u00a0Ma recherche\u00a0»")}
MY_SEARCH = "ma-recherche"
_INDEX = {step.slug: index for index, step in enumerate(STEPS)}


def get_step(slug):
    return STEPS[_INDEX[slug]] if slug in _INDEX else None


def _last_index(profile):
    """Index of the furthest validated screen, -1 when none is."""
    return _INDEX.get(profile.onboarding_step, -1)


def _applies(step, profile):
    return step.form_class.applies(profile)


def has_counter(step):
    """Whether the screen filters offers, and so shows the live counter."""
    return step.block is not None and step.block.has_counter


def wording(step, profile):
    """(title, why) of a screen for this person."""
    if profile.career_change and step.career_change_title:
        return step.career_change_title, step.career_change_why
    return step.title, step.why


def validated_steps(profile):
    return [step for step in STEPS[:_last_index(profile) + 1] if _applies(step, profile)]


def first_missing(profile):
    """The earliest validated screen whose answer is gone, or None.

    An earlier screen can drop later answers (a domain switch removes the jobs no longer offered, leaving missions
    removes a day rate), and a damaged stored value is read as empty (the domains too): that screen has to be
    answered again.
    """
    for step in validated_steps(profile):
        if not step.form_class.answered(profile):
            return step
    return None


def rewind(profile):
    """Move onboarding_step back before the first screen whose answer is missing. True when it moved."""
    missing = first_missing(profile)
    if missing is None:
        return False
    previous = previous_step(missing.slug, profile)
    profile.onboarding_step = previous.slug if previous else ""
    return True


def next_step(profile):
    """The first screen after the furthest validated one, None once every screen is validated.
    A screen that drops later answers calls rewind() first, so this is where the person resumes."""
    return next((step for step in STEPS[_last_index(profile) + 1:] if _applies(step, profile)), None)


def previous_step(slug, profile):
    return next((step for step in reversed(STEPS[:_INDEX[slug]]) if _applies(step, profile)), None)


def following_step(slug, profile=None):
    """The screen after `slug` for this person; without a profile, the next one in STEPS."""
    return next((step for step in STEPS[_INDEX[slug] + 1:] if profile is None or _applies(step, profile)), None)


def can_open(profile, slug):
    """Validated screens and the next one; never a jump ahead."""
    following = next_step(profile)
    return _applies(STEPS[_INDEX[slug]], profile) and (
        _INDEX[slug] <= _last_index(profile) or following is not None and following.slug == slug)


def is_further(slug, profile):
    return _INDEX[slug] > _last_index(profile)


def ends_block(step):
    later = following_step(step.slug)
    return step.block is not None and (later is None or later.block != step.block)


def block_done(block, profile):
    steps = [step for step in STEPS if step.block == block]
    return bool(steps) and _INDEX[steps[-1].slug] <= _last_index(profile)


def _seconds_left(slug, profile):
    return sum(step.seconds for step in STEPS[_INDEX[slug]:] if _applies(step, profile))


def minutes_left(slug, profile):
    """Minutes for the remaining screens, the current one included, rounded up."""
    return math.ceil(_seconds_left(slug, profile) / 60)


def time_left_text(slug, profile):
    if _seconds_left(slug, profile) < 60:
        return "Moins d’une minute"
    minutes = minutes_left(slug, profile)
    return "Encore une minute environ" if minutes == 1 else f"Encore {minutes} minutes environ"


def step_url(step):
    return reverse(step.url_name) if step.url_name else reverse("onboarding:step", args=[step.slug])


def resume_url(profile):
    step = next_step(profile)
    return step_url(step) if step else reverse("onboarding:end")


def edit_screen(key):
    """The screen that edits an answer (an id of summary.SOURCES, see Screen.edit_keys())."""
    return next(step for step in STEPS if key in step.form_class.edit_keys())


def card_block(key):
    """The block whose card shows an answer: the search screen's answers sit with the exclusions."""
    return edit_screen(key).block or EXCLUDE


def return_url(value):
    """The page a screen goes back to once saved, for an allowed ?retour= value; None otherwise."""
    return reverse(RETURNS[value][0]) if value in RETURNS else None


def return_label(value):
    return RETURNS[value][1] if value in RETURNS else None


def get_block(slug):
    return next((block for block in BLOCKS if block.slug == slug), None)


def block_steps(block, profile):
    """The screens of a block answered through the generic screen view, which its page in « Ma recherche » gathers.
    The CV screen keeps its own page."""
    return [step for step in STEPS
            if step.block == block and issubclass(step.form_class, forms.StepForm) and _applies(step, profile)]


def is_finished(profile):
    """Every screen validated and the end screen reached."""
    return next_step(profile) is None and profile.onboarding_completed_at is not None
