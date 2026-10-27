"""WCAG contrast helpers, used by the tests to hold the palette to RGAA AA."""

import re

TOKEN_RE = re.compile(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,6})\s*;")


def read_tokens(css):
    """Hex colour custom properties of a stylesheet, name without the leading dashes, value lowercased."""
    return {name: value.lower() for name, value in TOKEN_RE.findall(css)}


def _channels(hex_colour):
    value = hex_colour.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def relative_luminance(hex_colour):
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in _channels(hex_colour)]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(fg, bg):
    lighter, darker = sorted((relative_luminance(fg), relative_luminance(bg)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)
