"""Template tags of the visual system: authored inline icons, one stroke weight, decorative only."""

import re

from django import template
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe

register = template.Library()

# 20x20 grid, 2px stroke, square caps: the same drawing hand as the 2px card corners.
ICON_PATHS = {
    "check": '<path d="M4 10.5 8 14.5 16 5.5"/>',
    "alert": '<path d="M10 3 18 17H2Z"/><path d="M10 8v4"/><path d="M10 14.5v.5"/>',
    "back": '<path d="M12.5 4 6.5 10l6 6"/>',
    "next": '<path d="M7.5 4l6 6-6 6"/>',
    "close": '<path d="M5 5l10 10M15 5 5 15"/>',
    "file": '<path d="M5 2.5h6.5L15 6v11.5H5Z"/><path d="M11 2.5V6.5h4"/>',
    "mail": '<path d="M2.5 4.5h15v11h-15Z"/><path d="m2.5 5 7.5 6 7.5-6"/>',
    "edit": '<path d="M3 17h4L16.5 7.5l-4-4L3 13Z"/>',
}


# The score gauge: always ten notches, one per point.
GAUGE_NOTCHES = 10


@register.simple_tag
def gauge_notches():
    """The notch numbers of the score gauge, 1 to 10."""
    return range(1, GAUGE_NOTCHES + 1)


@register.simple_tag
def icon(name, css_class=""):
    classes = f"icon {css_class}".strip()
    return format_html(
        '<svg class="{}" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="square" stroke-linejoin="miter" aria-hidden="true" focusable="false">'
        "{}</svg>",
        classes, mark_safe(ICON_PATHS[name]))


# A verb and its inverted pronoun ("voulez-vous", "a-t-il") never split at the hyphen.
INVERSION = re.compile(r"\b[^\W\d_]+(?:-t)?-(?:je|tu|il|elle|on|ce|nous|vous|ils|elles)\b")


@register.filter
def keep_inversions(text):
    """`text`, escaped, with each inverted verb wrapped in <span class="nowrap">."""
    return mark_safe(INVERSION.sub(lambda match: f'<span class="nowrap">{match.group(0)}</span>',
                                   conditional_escape(text)))
