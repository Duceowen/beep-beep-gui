"""Translation files match the English catalog and EDMC's .strings parser.

Run from the repository root:  python -m unittest discover -s tests
"""
import ast
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
L10N = os.path.join(ROOT, "L10n")
# Same line pattern as EDMC l10n.Translations.TRANS_RE.
TRANS_RE = re.compile(r'\s*"((?:[^"]|\")+)"\s*=\s*"((?:[^"]|\")+)"\s*;\s*$')
COMMENT_RE = re.compile(r'\s*/\*.*\*/\s*$')
PLACEHOLDER_RE = re.compile(r"\{[A-Z][A-Z0-9_]*\}|\{CR\}")
LANG_FILES = (
    "ru", "uk", "it", "fr", "de", "ja", "zh-Hans", "ko",
    "pt-BR", "pt-PT", "es", "cs", "pl", "tr",
)
# File and folder names, and the sound id stored in seen_commanders.json.
KEEP_TOKENS = (
    "seen_commanders.json",
    "beep_marks.jsonl",
    "recordings",
    "neutral",
    "Tishtech",
    "TishTech",
)


def _literal(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _module(name):
    path = os.path.join(ROOT, name)
    with open(path, encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename=name)


def catalog_keys():
    """English strings the UI asks EDMC to translate."""
    keys = set()
    for filename in ("gui.py", "commander_history.py", "overlay_announcer.py", "load.py", "i18n.py"):
        tree = _module(filename)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Name) or func.id != "tl" or not node.args:
                continue
            text = _literal(node.args[0])
            if text is not None:
                keys.add(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            target = node.targets[0]
            name = target.id if isinstance(target, ast.Name) else None
            if name == "_SHOWN_ERRORS":
                for elt in node.value.elts:
                    keys.add(elt.value)
            elif name == "COLUMN_LABELS":
                for pair in node.value.elts:
                    keys.add(pair.elts[1].value)
            elif name == "_HIDE_LABELS":
                for key, value in zip(node.value.keys, node.value.values):
                    keys.add(value.value)
            elif name == "_BEEP_MARK_ITEMS":
                for pair in node.value.elts:
                    keys.add(pair.elts[1].value)
        if filename == "commander_history.py":
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "backup_list_kind":
                    for child in ast.walk(node):
                        if isinstance(child, ast.Return):
                            text = _literal(child.value)
                            if text:
                                keys.add(text)
                if isinstance(node, ast.FunctionDef) and node.name == "import_plan_lines":
                    for child in ast.walk(node):
                        text = _literal(child)
                        if text and "{ID}" in text:
                            keys.add(text)
    return keys


def parse_strings(path):
    """Parse one EDMC .strings file. Returns (entries, bad_lines)."""
    entries = {}
    bad = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            match = TRANS_RE.match(line)
            if match:
                key = match.group(1).replace(r"\"", '"')
                value = match.group(2).replace(r"\"", '"')
                entries[key] = value
            elif not COMMENT_RE.match(line):
                bad.append((number, line.rstrip("\n")))
    return entries, bad


class LocalizationFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template, cls.template_bad = parse_strings(os.path.join(L10N, "en.template"))
        cls.catalog = catalog_keys()

    def test_template_parses(self):
        self.assertEqual(self.template_bad, [])
        self.assertIn("!Language", self.template)
        self.assertEqual(self.template["!Language"], "English")

    def test_template_has_every_ui_string(self):
        missing = sorted(self.catalog - set(self.template))
        self.assertEqual(missing, [])

    def test_language_files_match_template(self):
        self.assertEqual(
            sorted(name for name in os.listdir(L10N) if name.endswith(".strings")),
            sorted(f"{code}.strings" for code in LANG_FILES),
        )
        template_keys = set(self.template)
        for code in LANG_FILES:
            path = os.path.join(L10N, f"{code}.strings")
            entries, bad = parse_strings(path)
            self.assertEqual(bad, [], code)
            self.assertEqual(sorted(entries), sorted(template_keys), code)
            self.assertNotEqual(entries.get("!Language"), "English", code)
            for key, value in entries.items():
                if key == "!Language":
                    continue
                self.assertEqual(
                    sorted(PLACEHOLDER_RE.findall(key)),
                    sorted(PLACEHOLDER_RE.findall(value)),
                    f"{code}: {key}",
                )
                for token in KEEP_TOKENS:
                    if token in key:
                        self.assertIn(token, value, f"{code} dropped {token} from {key}")
                fields = [
                    name[1:-1]
                    for name in PLACEHOLDER_RE.findall(key)
                    if name != "{CR}"
                ]
                sample = {name: "1" for name in fields}
                try:
                    value.replace("{CR}", "\n").format(**sample)
                except (KeyError, IndexError, ValueError) as err:
                    self.fail(f"{code} cannot format {key!r}: {err}")


if __name__ == "__main__":
    unittest.main()
