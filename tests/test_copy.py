"""NFR-09: every error shown to the user is plain, short and says what to do."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "WeFlow"
ERROR_CALLS = {"UploadError", "CopyError", "ChecksumMismatch", "ProcessError", "SecretsError"}
JARGON = re.compile(r"Traceback|Exception|errno|\bNone\b|stack|\bnull\b|HTTP \d", re.I)


def literal(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):  # f-string: keep the fixed parts
        return "".join(
            part.value if isinstance(part, ast.Constant) else "X" for part in node.values
        )
    return None


def user_messages() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name in ERROR_CALLS or name in ("show_message", "_fail"):
                text = literal(node.args[-1] if name == "show_message" else node.args[0])
                if text:
                    found.append((path.name, text))
    return found


def test_nfr09_error_copy_is_short_and_plain() -> None:
    messages = user_messages()
    assert len(messages) > 25  # the scan really found them
    for where, text in messages:
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
        assert len(sentences) <= 2, (where, text)
        assert len(text) <= 110, (where, text)
        assert not JARGON.search(text), (where, text)
        assert text[0].isupper() or text.startswith("X"), (where, text)


def test_nfr09_permanent_errors_say_what_to_do() -> None:
    actions = re.compile(
        r"Connect|Reconnect|Check|Press|Choose|Add|Use|Unlock|Enter|Free|Re-insert|try again", re.I
    )
    text = (ROOT / "core" / "uploaders.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "UploadError":
            permanent = any(
                isinstance(a, ast.Constant) and a.value is True for a in node.args[1:]
            ) or any(k.arg == "permanent" for k in node.keywords)
            message = literal(node.args[0]) or ""
            if permanent and "Fake" not in message:
                assert actions.search(message), message
