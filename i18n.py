"""User-visible strings for the EDMC language the commander selected.

EDMC plugins translate with ``l10n`` and files in ``L10n``. The lookup key is
the English text. ``context`` is this file so EDMC reads this plugin's
``L10n`` folder rather than its own. Tests and a copy running without EDMC
keep the English text.
"""

try:
    from l10n import translations as tr
except Exception:
    tr = None


def _english(text):
    return str(text).replace("{CR}", "\n")


# Exact sentences raised for the import and restore dialogs. The log keeps the
# English text; the dialog looks the sentence up.
_SHOWN_ERRORS = (
    "That file is larger than 5 MB, so it was not read.",
    "That file is nested too deeply to read.",
    "That file is not valid JSON.",
    "That file could not be read as text.",
    "That file is too large to read.",
    "That file is not a seen commanders list.",
    "That file has more than 20,000 entries, so it was not imported.",
    "That file has no valid commander entries.",
    "That import plan is not valid.",
    "Could not back up the current list, so nothing was restored.",
    "Could not back up the current list, so nothing was imported.",
)


def tl(text, **kwargs):
    """Translate one string. Named placeholders are filled when given."""
    translated = _english(text)
    if tr is not None:
        try:
            found = tr.tl(text, context=__file__)
        except Exception:
            found = None
        if found:
            translated = str(found).replace("{CR}", "\n")
    if not kwargs:
        return translated
    try:
        return translated.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        try:
            return _english(text).format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return _english(text)


def shown_error(err):
    """Translate a fixed English error. A system message is left as it is.

    ``Not a known backup file:`` is followed by the file name, which stays
    untranslated.
    """
    text = str(err)
    prefix = "Not a known backup file: "
    if text.startswith(prefix):
        return tl("Not a known backup file: {NAME}").format(NAME=text[len(prefix):])
    if text in _SHOWN_ERRORS:
        return tl(text)
    return text
