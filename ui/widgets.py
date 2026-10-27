"""Form widgets and fields of the visual system: standard web controls, styled by ui/static/ui/css/tri.css."""

from django import forms
from django.core.exceptions import ValidationError

# Card colour of each kind of information. A tone colours selected chips with their block's card.
TONES = ("pink", "blue", "yellow", "green")


class ChipsWidget(forms.CheckboxSelectMultiple):
    """Checkboxes drawn as chips. The cap is shown to the browser; CappedMultipleChoiceField enforces it.

    Grouped choices ((group label, [(value, label), ...]), ...) give one labelled group per label, all in the one
    container whose cap counts every chip.
    """

    template_name = "ui/widgets/chips.html"
    option_template_name = "ui/widgets/chip_option.html"

    def __init__(self, attrs=None, choices=(), max_choices=None, tone="blue"):
        if tone not in TONES:
            raise ValueError(f"Unknown tone {tone!r}, expected one of {TONES}.")
        super().__init__(attrs, choices)
        self.max_choices = max_choices
        self.tone = tone

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        grouped = any(group for group, _, _ in context["widget"]["optgroups"])
        context["widget"].update(tone=self.tone, max_choices=self.max_choices, grouped=grouped)
        return context


class OptionsWidget(forms.RadioSelect):
    """One choice among a few, each a full-width row with its native radio."""

    template_name = "ui/widgets/options.html"
    option_template_name = "ui/widgets/option.html"


class CheckOptionsWidget(forms.CheckboxSelectMultiple):
    """Several choices among a few, each a full-width row with its native checkbox."""

    template_name = OptionsWidget.template_name
    option_template_name = OptionsWidget.option_template_name


class SwitchWidget(forms.CheckboxInput):
    """An on/off checkbox announced as a switch."""

    template_name = "ui/widgets/switch.html"

    def get_context(self, name, value, attrs):
        return super().get_context(name, value, {**(attrs or {}), "role": "switch"})


class CappedMultipleChoiceField(forms.MultipleChoiceField):
    """Chips with an upper bound, enforced on the server whether or not the browser ran the script.

    A choice sent twice counts once. `check_cap()` lets a form apply the same cap, and message, to a larger count.
    """

    default_error_messages = {
        "invalid_choice": "Choisissez seulement dans la liste.",
        "too_many": "Choisissez-en %(max)s au plus : retirez-en %(extra)s.",
    }

    def __init__(self, *, max_choices, tone="blue", **kwargs):
        self.max_choices = max_choices
        kwargs.setdefault("widget", ChipsWidget(max_choices=max_choices, tone=tone))
        super().__init__(**kwargs)

    def to_python(self, value):
        return list(dict.fromkeys(super().to_python(value)))

    def validate(self, value):
        super().validate(value)
        self.check_cap(len(value))

    def check_cap(self, count, message=None):
        """Raises the too-many error when `count` choices exceed the cap; `message` replaces its wording."""
        if count > self.max_choices:
            raise ValidationError(message or self.error_messages["too_many"], code="too_many",
                                  params={"max": self.max_choices, "extra": count - self.max_choices})
