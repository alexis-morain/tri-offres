"""One form per questionnaire screen. Each reads its initial answers from the profile and writes them back.

Block 1 screens answer `hard_criteria`: their `apply()` returns the criteria with this screen's answer merged in,
without saving, and the live counter uses it to preview a screen before it is validated. Block 2 screens answer
`weighted_preferences`, the words screen the two free texts.
"""
import re
from typing import NamedTuple

from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.choices import flatten_choices

from jobs import rome_titles
from jobs.text import fold
from onboarding import choices, criteria, cv, preferences, rome, summary
from ui.widgets import CappedMultipleChoiceField, CheckOptionsWidget, OptionsWidget, SwitchWidget

YES_NO = [("yes", "Oui"), ("no", "Non")]
CITY_LABELS = ["Ville", "Deuxième ville (facultatif)", "Troisième ville (facultatif)"]
JOB_LABELS = ["Poste", "Deuxième poste (facultatif)", "Troisième poste (facultatif)"]
JOB_FIELDS = [f"job_{index}" for index in range(1, len(JOB_LABELS) + 1)]
# Codes from geo.api.gouv.fr. Anything else (hand-edited hidden field) is dropped, the city name is kept.
CODE_PATTERNS = {"insee": r"[0-9][0-9AB][0-9]{3}", "department": r"[0-9][0-9AB][0-9]?", "region": r"[0-9]{2}"}
PAY_BOUNDS = {
    "year": (5000, 200000, "Indiquez un salaire brut annuel entre 5\u202f000 et 200\u202f000\u00a0€, "
                           "par exemple 24000."),
    "month": (500, 20000, "Indiquez un salaire brut mensuel entre 500 et 20\u202f000\u00a0€, par exemple 2000."),
    "day": (50, 2000, "Indiquez un tarif journalier entre 50 et 2\u202f000\u00a0€, par exemple 400."),
}


def choice(label, choices, required_message, **kwargs):
    return forms.ChoiceField(label=label, choices=choices, widget=OptionsWidget,
                             error_messages={"required": required_message,
                                             "invalid_choice": required_message}, **kwargs)


def preference(help_text):
    return forms.BooleanField(label="Plutôt une préférence", required=False, widget=SwitchWidget,
                              help_text=help_text)


class Screen:
    """What the flow and the file read from a screen (flow.Step.form_class), whatever its views."""

    @classmethod
    def answered(cls, profile):
        """Whether the saved profile holds this screen's answer (an earlier screen may have dropped it)."""
        raise NotImplementedError

    @classmethod
    def applies(cls, profile):
        """Whether the screen is asked to this person at all."""
        return True

    @classmethod
    def card_keys(cls):
        """Keys of the rows this screen fills on its block's card."""
        raise NotImplementedError

    @classmethod
    def live_card_keys(cls):
        """Keys shown while the screen is still being answered: only a screen that saves before it is validated."""
        return set()

    @classmethod
    def edit_keys(cls):
        """Ids of the answers (summary.SOURCES) this screen edits: where the summary's "Modifier" links lead."""
        return cls.card_keys()


class StepForm(Screen, forms.Form):
    """A questionnaire screen answered through the generic screen view. Each family below says where its answer
    lives and how it is written."""

    def __init__(self, data=None, *, profile, prefix=None):
        self.profile = profile
        self.domains = profile.domains
        # prefix: several screens on one page, the block pages of « Ma recherche ».
        super().__init__(data, initial=self.initial_answers(), prefix=prefix)

    def initial_answers(self):
        return {}

    def store(self):
        """Write the validated answer onto the profile, without saving it."""
        raise NotImplementedError

    def save(self):
        self.store()
        self.profile.save()


class CriteriaForm(StepForm):
    """A screen answering `hard_criteria`: the search screen and block 1."""

    # Key of hard_criteria this screen answers; the file shows that row once the screen is validated.
    criteria_key = None
    # Name of the field under which the consequence sentence is shown, if the screen has one.
    consequence_after = None

    def __init__(self, *args, **kwargs):
        self.saved = criteria.normalize(kwargs["profile"].hard_criteria)
        super().__init__(*args, **kwargs)

    @classmethod
    def answered(cls, profile):
        return cls.criteria_key in (profile.hard_criteria or {})

    @classmethod
    def card_keys(cls):
        return {cls.criteria_key}

    def apply(self, saved):
        raise NotImplementedError

    def consequence(self, answers):
        return None

    def store(self):
        # Merged into the criteria as they are now, not as they were read: on a block page, the screens before this
        # one have just stored theirs.
        self.profile.hard_criteria = criteria.normalize(self.apply(criteria.normalize(self.profile.hard_criteria)))


class StrictAnswerForm(CriteriaForm):
    """A screen with one radio group stored as {"value", "strict"}."""

    def initial_answers(self):
        saved = self.saved.get(self.criteria_key)
        if saved is None:
            return {}
        return {self.criteria_key: saved["value"], "preference": not saved["strict"]}

    def apply(self, saved):
        return {**saved, self.criteria_key: {"value": self.cleaned_data[self.criteria_key],
                                             "strict": not self.cleaned_data["preference"]}}


def _poste_domains(postes):
    """The domains of the postes that are a ROME title of a single fiche (jobs.rome_titles.code_for_appellation)."""
    return {rome.domain_for_rome(rome_titles.code_for_appellation(poste)) for poste in postes} - {None}


class SearchForm(CriteriaForm):
    """1 to 3 postes typed, with the ROME titles suggested; their domains are the person's. A poste the titles do not
    know needs a family of the fallback, folded under « Je ne trouve pas mon poste » (onboarding/step.html)."""

    criteria_key = "offer_kind"
    fallback_field = "domains"

    @classmethod
    def answered(cls, profile):
        # Domains without a poste still answer it: the accounts from before the postes (plan of 26/10, 3.4).
        return super().answered(profile) and bool(profile.domains)

    @classmethod
    def edit_keys(cls):
        return {"postes", "domains", "offer_kind", "career_change"}

    domains = CappedMultipleChoiceField(
        label="Famille de métiers", max_choices=rome.MAX_DOMAINS, tone="pink", choices=rome.DOMAINS, required=False,
        help_text="C’est votre métier, pas le secteur de l’employeur : le marketing dans l’immobilier, c’est "
                  "Marketing. Deux au plus.")
    offer_kind = choice(
        "Plutôt un poste salarié, des missions, ou les deux ?", list(criteria.OFFER_KINDS.items()),
        "Choisissez un poste salarié, des missions, ou les deux.",
        help_text="Les missions se font en freelance, payées à la journée.")
    career_change = choice("Êtes-vous en reconversion ?", YES_NO, "Répondez oui ou non.",
                           help_text="Vous recevrez une ou deux offres d’exploration par mail, "
                                     "hors de votre métier actuel.")
    previous_job = forms.CharField(
        label="Votre métier actuel ou le dernier", required=False, max_length=120,
        widget=forms.TextInput(attrs={"autocomplete": "organization-title"}),
        error_messages={"max_length": "Faites plus court : 120 caractères au plus."})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The browser stops at the maximum; the server counts the characters once the spaces are collapsed.
        attrs = {"autocomplete": "off", "maxlength": preferences.CUSTOM_JOB_MAX,
                 "data-suggestions-url": reverse("onboarding:job_suggestions")}
        postes = {name: forms.CharField(label=label, required=False, widget=forms.TextInput(attrs=attrs))
                  for name, label in zip(JOB_FIELDS, JOB_LABELS)}
        self.fields = {**postes, **self.fields}
        # Without JavaScript the question stays visible; the script hides it until it applies.
        self.fields["previous_job"].show_when = "career_change=yes"

    def initial_answers(self):
        if not self.profile.onboarding_step and "offer_kind" not in self.saved:
            # First visit: nothing preselected, the defaults of the account are not answers. A person sent back here
            # (damaged domains read as empty) finds the answers still saved.
            return {}
        postes = preferences.postes(self.profile.weighted_preferences)
        given = _poste_domains(postes)
        return {**dict(zip(JOB_FIELDS, postes)),
                "domains": [domain for domain in self.domains if domain not in given],
                "offer_kind": self.saved.get("offer_kind"),
                "career_change": "yes" if self.profile.career_change else "no",
                "previous_job": self.profile.previous_job}

    @property
    def fallback_open(self):
        """The families stay unfolded once one is chosen, or when they are asked for."""
        return bool(self["domains"].value()) or "domains" in self.errors

    def _postes(self, data):
        """The postes typed, spaces collapsed, the same one twice kept once; an error on each that does not fit."""
        postes = []
        for name in JOB_FIELDS:
            typed = data.get(name, "")
            poste = preferences.clean_custom_job(typed)
            if not typed:
                continue
            if poste is None:
                self.add_error(name, "Écrivez un nom de poste en lettres, ou laissez le champ vide.")
            elif len(poste) > preferences.CUSTOM_JOB_MAX:
                self.add_error(name, f"Faites plus court : {preferences.CUSTOM_JOB_MAX} caractères au plus.")
            elif fold(poste) not in {fold(kept) for kept in postes}:
                postes.append(poste)
        return postes

    def clean(self):
        data = super().clean()
        postes = self._postes(data)
        if not postes and not any(name in self.errors for name in JOB_FIELDS):
            self.add_error("job_1", "Indiquez le poste que vous cherchez, par exemple comptable.")
        elif postes and "domains" not in self.errors:
            domains = _poste_domains(postes) | set(data["domains"])
            if not domains:
                self.add_error("domains", "Je ne reconnais pas {}. Choisissez la famille de votre métier ci-dessous."
                               .format("ce poste" if len(postes) == 1 else "ces postes"))
            elif len(domains) > rome.MAX_STORED:
                self.add_error("domains", "Vos postes et ces familles font plus de trois familles de métiers : "
                                          "retirez-en.")
            else:
                data["all_domains"] = rome.normalize_domains(list(domains))  # in the order of the list
        data["postes"] = postes
        # A field that already failed its own check keeps that one message.
        if (data.get("career_change") == "yes" and not data.get("previous_job", "").strip()
                and "previous_job" not in self.errors):
            self.add_error("previous_job", "Indiquez votre métier actuel ou le dernier, par exemple "
                                           "chargée de communication.")
        return data

    def apply(self, saved):
        result = {**saved, "offer_kind": self.cleaned_data["offer_kind"]}
        pay = result.get("pay")
        if pay and pay["unit"] == "day" and result["offer_kind"] not in criteria.DAY_RATE_KINDS:
            del result["pay"]
        return result

    def save(self):
        domains = self.cleaned_data["all_domains"]
        self.profile.weighted_preferences = preferences.with_postes(
            self.profile.weighted_preferences, self.cleaned_data["postes"], domains)
        self.profile.domains = domains
        self.profile.career_change = self.cleaned_data["career_change"] == "yes"
        self.profile.previous_job = self.cleaned_data["previous_job"].strip() if self.profile.career_change else ""
        super().save()


class LocationForm(CriteriaForm):
    criteria_key = "location"
    consequence_after = "distance"

    distance = choice("Distance", list(criteria.DISTANCES.items()), "Choisissez une distance.", initial="30")
    remote = choice("Télétravail", list(criteria.REMOTE.items()), "Choisissez une réponse pour le télétravail.",
                    initial="any")
    preference = preference("Le lieu n’écarte plus d’offres, il compte seulement dans la note.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cities = {}
        for index, label in enumerate(CITY_LABELS, start=1):
            cities[f"city_{index}"] = forms.CharField(
                label=label, required=False, max_length=100,
                widget=forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false", "data-city": True}),
                error_messages={"max_length": "Ce nom de ville est trop long : 100 caractères au plus."})
            for code in CODE_PATTERNS:
                cities[f"city_{index}_{code}"] = forms.CharField(required=False, widget=forms.HiddenInput)
        self.fields = {**cities, **self.fields}

    def initial_answers(self):
        saved = self.saved.get("location")
        if saved is None:
            return {}
        initial = {"distance": saved["distance"], "remote": saved["remote"], "preference": not saved["strict"]}
        for index, city in enumerate(saved["cities"], start=1):
            initial[f"city_{index}"] = city["name"]
            for code in CODE_PATTERNS:
                initial[f"city_{index}_{code}"] = city[code]
        return initial

    def cities(self):
        result = []
        for index in range(1, len(CITY_LABELS) + 1):
            name = self.cleaned_data.get(f"city_{index}", "").strip()
            if not name:
                continue
            city = {"name": name}
            for code, pattern in CODE_PATTERNS.items():
                value = self.cleaned_data.get(f"city_{index}_{code}", "")
                city[code] = value if re.fullmatch(pattern, value) else ""
            result.append(city)
        return result

    def clean(self):
        data = super().clean()
        for index in range(1, len(CITY_LABELS) + 1):
            name = data.get(f"city_{index}", "").strip()
            if name and not re.search(r"[^\W\d_]", name):
                self.add_error(f"city_{index}", "Indiquez le nom d’une ville, par exemple Lyon.")
        if not self.cities() and data.get("remote") != "full_remote" and "city_1" not in self.errors:
            self.add_error("city_1", "Indiquez au moins une ville, ou choisissez « Télétravail complet "
                                     "seulement ».")
        return data

    def apply(self, saved):
        return {**saved, "location": {"cities": self.cities(), "distance": self.cleaned_data["distance"],
                                      "remote": self.cleaned_data["remote"],
                                      "strict": not self.cleaned_data["preference"]}}

    def consequence(self, answers):
        location = answers.get("location")
        # Nothing saved yet: the sentence of the preselected distance.
        return criteria.location_consequence(location["distance"] if location else self.fields["distance"].initial)


class ContractsForm(CriteriaForm):
    criteria_key = "contracts"

    contracts = forms.MultipleChoiceField(
        label="Contrats acceptés", widget=CheckOptionsWidget, choices=list(criteria.CONTRACTS.items()),
        error_messages={"required": "Cochez au moins un contrat.",
                        "invalid_choice": "Cochez seulement des contrats de la liste."})

    def initial_answers(self):
        saved = self.saved.get("contracts")
        if saved is None:
            return {"contracts": criteria.default_contracts(self.domains, self.saved.get("offer_kind"))}
        return {"contracts": saved["values"]}

    def apply(self, saved):
        # Kept in the list's order, whatever order the browser sent.
        chosen = set(self.cleaned_data["contracts"])
        values = [code for code in criteria.CONTRACTS if code in chosen]
        return {**saved, "contracts": {"values": values, "strict": True}}


def keywords_field(label, help_text):
    return forms.CharField(
        label=label, required=False, max_length=criteria.MAX_KEYWORDS * (criteria.MAX_KEYWORD_LENGTH + 2),
        help_text=help_text, widget=forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"}),
        error_messages={"max_length": f"Faites plus court : {criteria.MAX_KEYWORDS} mots au plus."})


class KeywordsForm(CriteriaForm):
    """Words to require and to exclude in the title, separated by commas. Optional: validating the screen is the
    answer, empty lists included."""

    criteria_key = "keywords"

    include = keywords_field(
        "Mots à exiger", "Séparés par des virgules. Une offre doit avoir au moins un de ces mots dans son titre.")
    exclude = keywords_field(
        "Mots à écarter", "Séparés par des virgules. Un mot est cherché en entier : «\u00a0stage\u00a0» "
                          "n’écarte pas «\u00a0stagiaire\u00a0», écrivez aussi stagiaire si besoin.")

    def initial_answers(self):
        saved = self.saved.get("keywords")
        return {} if saved is None else {name: ", ".join(saved[name]) for name in ("include", "exclude")}

    def _words(self, name, text):
        """The words of one field, spaces collapsed, the same word twice (by fold) kept once; an error when a word
        or the list does not fit."""
        words = []
        for part in text.split(","):
            word = " ".join(part.split())
            if not word:
                continue
            if not re.search(r"[^\W\d_]", word):
                self.add_error(name, f"«\u00a0{word}\u00a0» ne contient aucune lettre : écrivez un mot.")
            elif len(word) > criteria.MAX_KEYWORD_LENGTH:
                self.add_error(name, f"Un mot est trop long : {criteria.MAX_KEYWORD_LENGTH} caractères au plus.")
            elif fold(word) not in {fold(kept) for kept in words}:
                words.append(word)
        if len(words) > criteria.MAX_KEYWORDS:
            self.add_error(name, f"Indiquez {criteria.MAX_KEYWORDS} mots au plus.")
        return words

    def clean(self):
        data = super().clean()
        for name in ("include", "exclude"):
            if name not in self.errors:
                data[name] = self._words(name, data.get(name, ""))
        return data

    def apply(self, saved):
        return {**saved, "keywords": {"include": self.cleaned_data["include"],
                                      "exclude": self.cleaned_data["exclude"], "strict": True}}


class PayForm(CriteriaForm):
    criteria_key = "pay"
    consequence_after = "unit"

    floor = forms.IntegerField(
        label="Montant minimal", required=False,
        widget=forms.NumberInput(attrs={"class": "input input--short", "inputmode": "numeric"}),
        error_messages={"invalid": "Indiquez un nombre entier, sans espace ni symbole, par exemple 24000."})
    unit = choice("Unité", [], "Choisissez une unité dans la liste.")
    no_floor = forms.BooleanField(label="Je préfère ne pas fixer de plancher", required=False,
                                  widget=SwitchWidget)
    preference = preference("Le salaire n’écarte plus d’offres, il compte seulement dans la note.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        units = ["year", "month"]
        if self.saved.get("offer_kind") in criteria.DAY_RATE_KINDS:
            units.append("day")
        self.fields["unit"].choices = [(unit, criteria.PAY_UNITS[unit]) for unit in units]
        self.fields["unit"].initial = "day" if self.saved.get("offer_kind") == "mission" else "year"

    def initial_answers(self):
        saved = self.saved.get("pay")
        if saved is None:
            return {}
        return {"floor": saved["floor"], "unit": saved["unit"], "no_floor": saved["floor"] is None,
                "preference": not saved["strict"]}

    def clean(self):
        data = super().clean()
        if data.get("no_floor"):
            data["floor"] = None
            self.errors.pop("floor", None)
            return data
        floor, unit = data.get("floor"), data.get("unit")
        if "floor" in self.errors or unit is None:
            return data
        if floor is None:
            self.add_error("floor", "Indiquez un montant, ou activez « Je préfère ne pas fixer de plancher ».")
            return data
        low, high, message = PAY_BOUNDS[unit]
        if not low <= floor <= high:
            self.add_error("floor", ValidationError(message, code="out_of_bounds"))
        return data

    def apply(self, saved):
        return {**saved, "pay": {"floor": self.cleaned_data["floor"], "unit": self.cleaned_data["unit"],
                                 "strict": not self.cleaned_data["preference"]}}

    def consequence(self, answers):
        return criteria.pay_consequence(answers.get("pay"))


class HoursForm(StrictAnswerForm):
    criteria_key = "hours"

    hours = choice("Temps de travail", list(criteria.HOURS.items()), "Choisissez un temps de travail.")
    preference = preference("Le temps de travail n’écarte plus d’offres, il compte seulement dans la note.")


class ExperienceForm(StrictAnswerForm):
    criteria_key = "experience"

    experience = choice("Années d’expérience", list(criteria.EXPERIENCE.items()),
                        "Choisissez votre nombre d’années d’expérience.")
    preference = preference("L’expérience n’écarte plus d’offres, elle compte seulement dans la note.")


def chips(label, max_choices, required_message=None, **kwargs):
    """Capped chips in the blue of block 2, their choices set by the form. Optional without a required message."""
    return CappedMultipleChoiceField(label=label, max_choices=max_choices, tone="blue",
                                     required=required_message is not None,
                                     error_messages={"required": required_message}, **kwargs)


class PreferenceForm(StepForm):
    """A block 2 screen: one list of `weighted_preferences`, picked among chips."""

    preference_key = None

    def __init__(self, *args, **kwargs):
        self.preferences = preferences.normalize(kwargs["profile"].weighted_preferences)
        super().__init__(*args, **kwargs)
        chips = self.chip_choices()
        self.fields[self.preference_key].choices = chips
        self.codes = [code for code, _ in flatten_choices(chips)]

    def chip_choices(self):
        """The choices of the chips as the field takes them: (code, label) pairs, or groups of them."""
        raise NotImplementedError

    @classmethod
    def answered(cls, profile):
        return cls.preference_key in (profile.weighted_preferences or {})

    @classmethod
    def card_keys(cls):
        return {cls.preference_key}

    def initial_answers(self):
        saved = self.preferences.get(self.preference_key)
        return {} if saved is None else {self.preference_key: saved}

    def answer(self):
        """The chips picked, in the list's order whatever order the browser sent."""
        picked = set(self.cleaned_data[self.preference_key])
        return [code for code in self.codes if code in picked]

    def store(self):
        # As for criteria: merged into the preferences as they are now.
        self.profile.weighted_preferences = preferences.normalize(
            {**preferences.normalize(self.profile.weighted_preferences), self.preference_key: self.answer()})


class ValuesForm(PreferenceForm):
    preference_key = "values"

    values = chips("Ce qui compte", preferences.MAX_VALUES, "Choisissez au moins une chose qui compte pour vous.",
                   help_text="Jusqu’à 3.")

    def chip_choices(self):
        return choices.shown(choices.VALUES)


class JobsForm(PreferenceForm):
    """The job chips picked before the postes were typed (plan of 26/10, 3.1): asked only to the accounts that hold
    some, its answer keeps their postes first."""

    preference_key = "jobs"

    jobs = chips("Métiers", preferences.MAX_JOBS, help_text="Jusqu’à 5.")

    @classmethod
    def applies(cls, profile):
        weighted = profile.weighted_preferences
        jobs = weighted.get("jobs") if isinstance(weighted, dict) else None
        return isinstance(jobs, list) and not all(map(preferences.is_custom, jobs))

    def chip_choices(self):
        # The ROME professional domains of the person's domains, then the niche lists, one group each.
        return choices.job_groups(self.domains)

    def initial_answers(self):
        return {"jobs": [job for job in self.preferences.get("jobs", []) if not preferences.is_custom(job)]}

    def clean(self):
        data = super().clean()
        if not self.errors and not data["jobs"] and not preferences.postes(self.preferences):
            self.add_error("jobs", "Choisissez au moins un métier.")
        return data

    def answer(self):
        return [{"custom": poste} for poste in preferences.postes(self.preferences)] + super().answer()


class StructuresForm(PreferenceForm):
    """Two optional lists on one screen: the structures (the form's preference_key) and the employer's sector."""

    preference_key = "structures"

    structures = chips("Structures", preferences.MAX_STRUCTURES,
                       help_text="Jusqu’à 5, ou aucune si cela vous est égal.")
    sectors = chips("Secteur de l’employeur", preferences.MAX_SECTORS,
                    help_text="Jusqu’à 3, ou aucun si cela vous est égal.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sectors"].choices = choices.shown(choices.SECTORS)

    def chip_choices(self):
        return choices.shown(choices.STRUCTURES)

    @classmethod
    def card_keys(cls):
        return super().card_keys() | {"sectors"}

    def initial_answers(self):
        sectors = self.preferences.get("sectors")
        return {**super().initial_answers(), **({} if sectors is None else {"sectors": sectors})}

    def store(self):
        super().store()
        picked = set(self.cleaned_data["sectors"])
        self.profile.weighted_preferences = preferences.normalize(
            {**self.profile.weighted_preferences, "sectors": [code for code, _ in choices.SECTORS if code in picked]})


def words_field(label, examples):
    return forms.CharField(
        label=label, required=False, max_length=preferences.WORDS_MAX,
        # No-break spaces inside the quotes, so a guillemet never ends up alone at a line's end.
        help_text="Par exemple : «\u00a0{}\u00a0» ou «\u00a0{}\u00a0»".format(*examples),
        widget=forms.Textarea(attrs={"rows": 4}),
        error_messages={"max_length": "Faites plus court : 1\u202f000 caractères au plus."})


class WordsForm(StepForm):
    """Two free texts, both optional: validating the screen is the answer."""

    @classmethod
    def answered(cls, profile):
        return True

    @classmethod
    def card_keys(cls):
        return {"seek_text", "refuse_text"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["seek_text"] = words_field("Ce que vous cherchez", choices.EXAMPLES["seek"])
        self.fields["refuse_text"] = words_field("Ce que vous refusez", choices.EXAMPLES["refuse"])

    def initial_answers(self):
        return {"seek_text": self.profile.seek_text, "refuse_text": self.profile.refuse_text}

    def store(self):
        self.profile.seek_text = self.cleaned_data["seek_text"]
        self.profile.refuse_text = self.cleaned_data["refuse_text"]


class CVScreen(Screen):
    """The CV screen, optional, with views of its own (upload, list, deletion). The list is saved as soon as the CV
    is read, so the yellow card shows it before "Continuer"."""

    @classmethod
    def answered(cls, profile):
        return True

    @classmethod
    def card_keys(cls):
        return {"cv"}

    @classmethod
    def live_card_keys(cls):
        return {"cv"}


class CVUploadForm(forms.Form):
    """The file only. Its name is never used, stored or shown; its type is decided by its first bytes."""

    cv = forms.FileField(
        label="Votre fichier", help_text="PDF ou Word (.docx), 5 Mo au plus.",
        # Hidden but focusable: the French label of cv.html opens it (ui/static/ui/css/tri.css .file-field).
        widget=forms.FileInput(attrs={"accept": ".pdf,.docx", "class": "file-field__input"}),
        error_messages={"required": "Choisissez votre CV en PDF ou Word (.docx), ou passez cette étape.",
                        "invalid": "Choisissez votre CV en PDF ou Word (.docx), ou passez cette étape.",
                        "empty": "Ce fichier est vide. Choisissez votre CV en PDF ou Word (.docx)."})

    def refuse(self, code, has_list):
        """Show the refusal `code` (a key of cv.MESSAGES) as the file's only error."""
        self.errors.pop("cv", None)
        self.add_error("cv", cv.message(code, has_list))


def cv_text_field(label, initial):
    return forms.CharField(
        label=label, required=False, max_length=cv.TEXT_MAX, initial=initial,
        widget=forms.TextInput(attrs={"class": "input", "autocomplete": "off"}),
        error_messages={"max_length": f"Faites plus court : {cv.TEXT_MAX} caractères au plus."})


class CVList(NamedTuple):
    key: str  # in cv_profile
    prefix: str  # of the field names
    legend: str
    parts: tuple  # (part, label shown) of each line; part None for a list of plain strings
    line: str  # what names a line for screen readers: "du poste", "Compétence"
    delete: str  # what "Supprimer" removes, for screen readers: "le poste"


class CVListForm(forms.Form):
    """The list read from the CV, one line per item with its field and a "Supprimer" box.

    Only the items of the saved list have fields: whatever else is posted is ignored, so the counts can only go
    down. The result is revalidated against cv.CV_SCHEMA before it is saved.
    """

    LISTS = (
        CVList("positions", "position", "Postes", (("title", "Intitulé"), ("organisation", "Structure"),
                                                   ("years", "Années")), "du poste", "le poste"),
        CVList("skills", "skill", "Compétences", ((None, ""),), "Compétence", "la compétence"),
        CVList("education", "education", "Formation", ((None, ""),), "Formation", "la formation"),
    )
    TITLE_MISSING = "Indiquez l’intitulé du poste, ou cochez « Supprimer »."

    def __init__(self, data=None, *, cv_profile):
        super().__init__(data)
        self.saved = cv_profile
        for name, group, index, part in self._text_fields():
            saved = cv_profile[group.key][index]
            label = dict(group.parts)[part]
            self.fields[name] = cv_text_field(f"{label} {group.line} {index + 1}".strip(),
                                              saved[part] if part else saved)
            if part == group.parts[-1][0]:
                self.fields[self._row(group, index) + "_delete"] = forms.BooleanField(
                    label=f"Supprimer {group.delete} {index + 1}", required=False)

    def _text_fields(self):
        """(field name, list, index, part) of every text field of the saved list, in page order."""
        for group in self.LISTS:
            for index in range(len(self.saved.get(group.key, []))):
                for part, _ in group.parts:
                    yield (f"{self._row(group, index)}_{part}" if part else self._row(group, index)), group, index, part

    @staticmethod
    def _row(group, index):
        return f"{group.prefix}_{index}"

    def _deleted(self, group, index):
        return bool(self.cleaned_data.get(self._row(group, index) + "_delete"))

    def groups(self):
        """The fieldsets of the page: [{"legend", "rows"}], each row {"position", "fields", "delete",
        "delete_hidden"}, each field {"field", "visible", "hidden"}: the label shown, and the words only screen
        readers get ("du poste 2"), so that every label is unique."""
        groups = {}
        for name, group, index, part in self._text_fields():
            rows = groups.setdefault(group, {})
            row = rows.setdefault(index, {"position": part is not None, "fields": [],
                                          "delete": self[self._row(group, index) + "_delete"],
                                          "delete_hidden": f"{group.delete} {index + 1}"})
            row["fields"].append({"field": self[name], "visible": dict(group.parts)[part],
                                  "hidden": f"{group.line} {index + 1}"})
        return [{"legend": group.legend, "rows": list(rows.values())} for group, rows in groups.items()]

    def clean(self):
        data = super().clean()
        for name, group, index, part in self._text_fields():
            if self._deleted(group, index):
                self.errors.pop(name, None)  # a line being deleted needs no correction
            elif (part == "title" and not data.get(name) and name not in self.errors
                  and any(data.get(f"{self._row(group, index)}_{other}") for other, _ in group.parts[1:])):
                self.add_error(name, self.TITLE_MISSING)
        return data

    def cv_profile(self):
        """The edited list, revalidated: {} when every line is gone. Call after is_valid()."""
        result = {group.key: [] for group in self.LISTS}
        lines = {}
        for name, group, index, part in self._text_fields():
            if self._deleted(group, index):
                continue
            value = self.cleaned_data.get(name, "")
            if part is None:
                result[group.key].append(value)
            else:
                if (group, index) not in lines:
                    lines[group, index] = {}
                    result[group.key].append(lines[group, index])
                lines[group, index][part] = value
        return cv.normalize(result)


class SummaryScreen(Screen):
    """The summary, after the three blocks, with views of its own. Nothing to answer: "Ça me va" validates it."""

    @classmethod
    def answered(cls, profile):
        return True

    @classmethod
    def card_keys(cls):
        return set()


class CorrectionForm(forms.Form):
    """"Corriger": a one-sentence correction and a short reply to each of Claude's questions, one of them at least."""

    correction = forms.CharField(
        label="Corriger en une phrase", required=False, max_length=summary.CORRECTION_MAX,
        # No-break spaces inside the quotes, so a guillemet never ends up alone at a line's end.
        help_text="Par exemple : «\u00a0En fait, l’enseignement ne m’intéresse plus.\u00a0»",
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
        error_messages={"max_length": f"Faites plus court : {summary.CORRECTION_MAX} caractères au plus."})

    def __init__(self, data=None, *, questions, initial=None):
        super().__init__(data, initial=initial)
        self.questions = questions
        for index, question in enumerate(questions):
            self.fields[f"answer_{index}"] = forms.CharField(
                label=question["text"], required=False, max_length=summary.REPLY_MAX, initial=question["answer"],
                widget=forms.TextInput(attrs={"autocomplete": "off"}),
                error_messages={"max_length": f"Faites plus court : {summary.REPLY_MAX} caractères au plus."})

    def answers(self):
        """The replies, in the order of the questions. Call after is_valid()."""
        return [self.cleaned_data[f"answer_{index}"] for index in range(len(self.questions))]

    def clean(self):
        data = super().clean()
        if not self.errors and not data["correction"] and not any(self.answers()):
            self.add_error("correction", "Écrivez votre correction en une phrase, ou répondez à une question."
                           if self.questions else "Écrivez votre correction en une phrase.")
        return data


class SummaryTextForm(forms.Form):
    """"Modifier le texte moi-même": the person's own version, saved as is."""

    text = forms.CharField(
        label="Votre version de la synthèse", max_length=summary.TEXT_MAX, widget=forms.Textarea(attrs={"rows": 6}),
        error_messages={"required": "Écrivez au moins une phrase, ou refermez cette partie sans enregistrer.",
                        "max_length": f"Faites plus court : {criteria.format_number(summary.TEXT_MAX)} caractères "
                                      "au plus."})
