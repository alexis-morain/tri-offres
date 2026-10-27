from django import forms

from matching.learned import SUMMARY_TEXT_MAX
from matching.models import Application, Feedback


class LearnedForm(forms.Form):
    """The person's own version of « Ce que j’ai appris de vos choix », kept as written (escaped when shown and
    defused in the prompts). Blank clears it."""

    text = forms.CharField(
        label="Votre version", required=False, max_length=SUMMARY_TEXT_MAX, widget=forms.Textarea(attrs={"rows": 6}),
        help_text=f"{SUMMARY_TEXT_MAX} caractères au plus. Le tri de la nuit lira votre texte.",
        error_messages={"max_length": f"Faites plus court : {SUMMARY_TEXT_MAX} caractères au plus."})


class FollowForm(forms.Form):
    """One offer of « Mes offres »: the verdict, and for a yes the status and the note (absent from the other tabs,
    hence not required)."""

    verdict = forms.ChoiceField(label="Mon avis", choices=Feedback.Verdict.choices)
    status = forms.ChoiceField(label="Où j’en suis", choices=Application.Status.choices, required=False)
    note = forms.CharField(label="Note", required=False, max_length=Application.NOTE_MAX,
                           widget=forms.Textarea(attrs={"rows": 2}))
