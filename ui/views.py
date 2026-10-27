from django import forms
from django.conf import settings
from django.http import Http404
from django.shortcuts import render

from ui.widgets import CappedMultipleChoiceField, CheckOptionsWidget, OptionsWidget, SwitchWidget

PALETTE = [
    ("ground", "Fond"), ("surface", "Champ"), ("ink", "Encre"), ("ink-muted", "Encre secondaire"),
    ("action", "Encre d’action"), ("stamp", "Tampon"), ("error", "Erreur"), ("pink", "Fiche rose"),
    ("blue", "Fiche bleue"), ("yellow", "Fiche jaune"), ("green", "Fiche verte"),
]


class SpecimenForm(forms.Form):
    """Every control of the visual system, for the internal style page."""

    email = forms.EmailField(
        label="Votre adresse mail", help_text="Un code de connexion y sera envoyé.",
        error_messages={"required": "Indiquez votre adresse mail.",
                        "invalid": "Vérifiez l’adresse : elle doit ressembler à nom@exemple.fr."})
    city = forms.CharField(label="Ville", error_messages={"required": "Indiquez une ville, par exemple Lyon."})
    remote = forms.ChoiceField(
        label="Télétravail",
        choices=[("onsite", "Sur place possible"), ("hybrid", "Hybride"), ("full", "Télétravail complet seulement"),
                 ("any", "Peu importe")],
        widget=OptionsWidget, initial="hybrid")
    contracts = forms.MultipleChoiceField(
        label="Contrats acceptés", choices=[("cdi", "CDI"), ("cdd", "CDD"), ("civique", "Service civique")],
        widget=CheckOptionsWidget, initial=["cdi"], required=False, help_text="Les autres contrats ne vous seront pas montrés.")
    values = CappedMultipleChoiceField(
        label="Ce qui compte le plus", help_text="Jusqu’à 3.", max_choices=3, tone="blue",
        choices=[("sens", "Le sens du travail"), ("stabilite", "La stabilité"), ("apprendre", "Apprendre"),
                 ("autonomie", "L’autonomie"), ("equipe", "L’équipe")],
        initial=["sens"])
    jobs = CappedMultipleChoiceField(
        label="Métiers", help_text="Deux au plus.", max_choices=2, tone="blue", required=False,
        choices=[("Communication / Multimédia", [("E11", "Édition et communication"), ("E14", "Publicité")]),
                 ("Plus précis, pour la culture et l’ESS", [("mediation", "Médiation")])])
    preference = forms.BooleanField(
        label="Plutôt une préférence", required=False, widget=SwitchWidget,
        help_text="Le critère n’écarte plus d’offres, il compte seulement dans la note.")
    seek = forms.CharField(label="Ce que vous cherchez", widget=forms.Textarea, required=False)


def style_page(request):
    if not (settings.DEBUG or request.user.is_staff):
        raise Http404
    invalid_form = SpecimenForm(prefix="invalid", data={"invalid-email": "nora@", "invalid-remote": "hybrid",
                                                        "invalid-values": ["sens"]})
    invalid_form.is_valid()
    return render(request, "ui/style.html", {
        "form": SpecimenForm(), "question_form": SpecimenForm(prefix="question"), "invalid_form": invalid_form, "palette": PALETTE,
        "score": 7,
    })
