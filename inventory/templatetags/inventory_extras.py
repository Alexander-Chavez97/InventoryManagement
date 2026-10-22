from django import template

register = template.Library()


@register.filter
def avatar_initials(value):
    """First two characters of a model name, for the small avatar chip in
    the inventory table -- e.g. "Axis Q6135-LE" -> "AX"."""
    if not value:
        return "--"
    return value.strip()[:2].upper()
