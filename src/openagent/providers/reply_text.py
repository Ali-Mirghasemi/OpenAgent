"""Conservative guards for visible assistant text extracted from a web UI.

Do not mistake an accessibility role heading for the assistant's answer.
"""
from __future__ import annotations


ROLE_ONLY_LABELS = frozenset({
    "chatgpt said:", "chatgpt says:", "assistant said:", "assistant says:",
})


def is_role_label_only(value: str) -> bool:
    return " ".join(value.casefold().split()) in ROLE_ONLY_LABELS


def is_trustworthy_answer(value: str) -> bool:
    text = value.strip()
    return bool(text and not is_role_label_only(text) and
                not any(text.casefold().startswith(prefix + "\n")
                        for prefix in ROLE_ONLY_LABELS) and
                len(text) <= 262144)
