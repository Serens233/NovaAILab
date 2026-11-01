"""Fixed, synthetic benchmark cases.

Cases are data files, not chat history.  Keeping them in a checked-in JSON file
makes the exact prompts, system message, and token budget reproducible.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

_CASE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{1,63}\Z")
_MAX_PROMPT_CHARS = 6000


@dataclass(frozen=True)
class TestCase:
    id: str
    category: str
    language: str
    system: str
    prompt: str
    max_tokens: int | None
    notes: str = ""

    def messages(self) -> list[dict[str, str]]:
        messages = []
        if self.system.strip():
            messages.append({"role": "system", "content": self.system})
        messages.append({"role": "user", "content": self.prompt})
        return messages


def _validate_case(raw: object, index: int) -> TestCase:
    if not isinstance(raw, dict):
        raise ValueError(f"case #{index} must be an object")
    case_id = raw.get("id")
    if not isinstance(case_id, str) or not _CASE_ID.fullmatch(case_id):
        raise ValueError(f"case #{index} has an invalid id")
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"case {case_id!r} must have a non-empty prompt")
    if len(prompt) > _MAX_PROMPT_CHARS:
        raise ValueError(f"case {case_id!r} prompt exceeds {_MAX_PROMPT_CHARS} characters")
    system = raw.get("system", "")
    if not isinstance(system, str) or len(system) > 2000:
        raise ValueError(f"case {case_id!r} has an invalid system prompt")
    language = raw.get("language", "zh-CN")
    if not isinstance(language, str) or len(language) > 32:
        raise ValueError(f"case {case_id!r} has an invalid language")
    category = raw.get("category", "unknown")
    if not isinstance(category, str) or len(category) > 64:
        raise ValueError(f"case {case_id!r} has an invalid category")
    notes = raw.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 500:
        raise ValueError(f"case {case_id!r} has invalid notes")
    max_tokens = raw.get("max_tokens")
    if max_tokens is not None and (not isinstance(max_tokens, int) or not 1 <= max_tokens <= 2048):
        raise ValueError(f"case {case_id!r} max_tokens must be between 1 and 2048")
    return TestCase(
        id=case_id,
        category=category,
        language=language,
        system=system,
        prompt=prompt,
        max_tokens=max_tokens,
        notes=notes,
    )


def load_cases(path: Path, case_ids: tuple[str, ...] = ()) -> list[TestCase]:
    raw_text = path.read_text(encoding="utf-8")
    document = json.loads(raw_text)
    if isinstance(document, dict):
        raw_cases = document.get("cases")
    else:
        raw_cases = document
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError(f"no cases found in {path}")
    cases = [_validate_case(item, index) for index, item in enumerate(raw_cases)]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case ids must be unique")
    if case_ids:
        wanted = set(case_ids)
        missing = wanted - set(ids)
        if missing:
            raise ValueError(f"unknown case ids: {sorted(missing)}")
        cases = [case for case in cases if case.id in wanted]
    return cases


def cases_sha256(cases: list[TestCase]) -> str:
    canonical = json.dumps(
        [
            {
                "id": case.id,
                "category": case.category,
                "language": case.language,
                "system": case.system,
                "prompt": case.prompt,
                "max_tokens": case.max_tokens,
                "notes": case.notes,
            }
            for case in cases
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cases_as_dict(cases: list[TestCase]) -> list[dict[str, object]]:
    return [
        {
            "id": case.id,
            "category": case.category,
            "language": case.language,
            "system": case.system,
            "prompt": case.prompt,
            "max_tokens": case.max_tokens,
            "notes": case.notes,
        }
        for case in cases
    ]
