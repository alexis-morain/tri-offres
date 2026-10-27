from django import forms
from django.core.exceptions import ValidationError

import mail
from accounts.models import normalize_email

INVALID_EMAIL = "Vérifiez l’adresse : elle doit ressembler à nom@exemple.fr."


class EmailForm(forms.Form):
    email = forms.EmailField(
        label="Votre adresse mail", max_length=mail.MAX_ADDRESS_LENGTH,
        help_text="Pas de mot de passe : un code à six chiffres vous attend dans votre boîte.",
        widget=forms.EmailInput(attrs={"autocomplete": "email", "spellcheck": "false"}),
        error_messages={"required": "Indiquez votre adresse mail.", "invalid": INVALID_EMAIL,
                        "max_length": f"Cette adresse est trop longue : {mail.MAX_ADDRESS_LENGTH} caractères "
                                      "au plus."})

    def clean_email(self):
        email = normalize_email(self.cleaned_data["email"])
        if not mail.is_valid_address(email):
            raise ValidationError(INVALID_EMAIL, code="invalid")
        return email


class CodeForm(forms.Form):
    code = forms.CharField(
        label="Code reçu", strip=True,
        widget=forms.TextInput(attrs={"class": "input input--code", "data-code-input": True, "inputmode": "numeric",
                                      "autocomplete": "one-time-code", "pattern": "[0-9 ]*", "spellcheck": "false"}),
        error_messages={"required": "Tapez le code à six chiffres reçu par mail."})

    def clean_code(self):
        code = "".join(self.cleaned_data["code"].split())
        if len(code) != 6 or not code.isascii() or not code.isdigit():
            raise ValidationError("Le code compte six chiffres : recopiez-le depuis le mail.", code="format")
        return code
