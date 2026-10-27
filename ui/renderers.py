from django.forms.renderers import TemplatesSetting


class TriFormRenderer(TemplatesSetting):
    """Every form renders its fields through the visual system's field template."""

    form_template_name = "ui/form.html"
    field_template_name = "ui/field.html"
