#!/usr/bin/env python3
"""Validate bot content files before deployment."""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {
    "*": "asterisk formatting marker",
    "\u2013": "en dash",
    "\u2014": "em dash",
}
QUIZ_FILES = {
    "questions.json": {"topic"},
    "amir_ru.json": {"topic"},
    "nejm_cases.json": {"image", "image_caption"},
}


def walk_strings(value: Any, prefix: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield prefix, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_strings(item, f"{prefix}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            key_path = f"{prefix}.{key}" if prefix else str(key)
            yield key_path, str(key)
            yield from walk_strings(item, key_path)


def normalized(text: str) -> str:
    return " ".join(text.lower().split())


def load_json(name: str, errors: list[str]) -> Any:
    try:
        return json.loads((ROOT / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{name}: cannot read valid JSON: {exc}")
        return []


def validate_text(name: str, data: Any, errors: list[str]) -> None:
    for location, text in walk_strings(data):
        for character, label in FORBIDDEN.items():
            if character in text:
                errors.append(f"{name}:{location}: contains {label}")


def image_exists(source: str) -> bool:
    if source.startswith(("http://", "https://")):
        return True
    path = ROOT / source
    if path.exists():
        return True
    return any(path.with_suffix(ext).exists() for ext in (".jpg", ".jpeg", ".png"))


def validate_quiz(
    name: str,
    extra_fields: set[str],
    errors: list[str],
    warnings: list[str],
) -> int:
    data = load_json(name, errors)
    if not isinstance(data, list):
        errors.append(f"{name}: root must be a list")
        return 0

    ids: list[int] = []
    full_records: Counter[str] = Counter()
    questions: defaultdict[str, list[int]] = defaultdict(list)
    required = {"id", "question", "options", "correct_index", "explanation"} | extra_fields

    for index, item in enumerate(data):
        location = f"{name}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{location}: record must be an object")
            continue

        missing = sorted(required - item.keys())
        if missing:
            errors.append(f"{location}: missing fields: {', '.join(missing)}")
            continue

        qid = item["id"]
        if not isinstance(qid, int):
            errors.append(f"{location}.id: must be an integer")
        else:
            ids.append(qid)

        options = item["options"]
        correct = item["correct_index"]
        if not isinstance(options, list) or len(options) < 2:
            errors.append(f"{location}.options: must contain at least two options")
        elif not isinstance(correct, int) or not 0 <= correct < len(options):
            errors.append(f"{location}.correct_index: outside options range")

        if not str(item["question"]).strip():
            errors.append(f"{location}.question: empty")
        if not str(item["explanation"]).strip():
            errors.append(f"{location}.explanation: empty")

        questions[normalized(str(item["question"]))].append(qid)
        full_records[json.dumps(item, ensure_ascii=False, sort_keys=True)] += 1

        if name == "nejm_cases.json" and item.get("enabled", True):
            if not image_exists(str(item.get("image", ""))):
                errors.append(f"{location}.image: enabled case image is missing")

    duplicate_ids = sorted(qid for qid, count in Counter(ids).items() if count > 1)
    if duplicate_ids:
        errors.append(f"{name}: duplicate IDs: {duplicate_ids[:20]}")

    exact_duplicates = sum(count - 1 for count in full_records.values() if count > 1)
    if exact_duplicates:
        errors.append(f"{name}: {exact_duplicates} exact duplicate records")

    repeated_questions = sum(1 for values in questions.values() if len(values) > 1)
    if repeated_questions:
        warnings.append(f"{name}: {repeated_questions} repeated question texts require editorial review")

    validate_text(name, data, errors)
    return len(data)


def validate_practicum(errors: list[str]) -> int:
    name = "practicum.json"
    data = load_json(name, errors)
    if not isinstance(data, list):
        errors.append(f"{name}: root must be a list")
        return 0

    titles: list[str] = []
    for index, card in enumerate(data):
        location = f"{name}[{index}]"
        if not isinstance(card, dict):
            errors.append(f"{location}: card must be an object")
            continue
        title = str(card.get("title", "")).strip()
        sections = card.get("data")
        if not title:
            errors.append(f"{location}.title: empty")
        else:
            titles.append(normalized(title))
        if not isinstance(sections, dict) or not sections:
            errors.append(f"{location}.data: must be a non-empty object")
        elif any(not str(key).strip() or not str(value).strip() for key, value in sections.items()):
            errors.append(f"{location}.data: empty section name or content")

    duplicate_titles = [title for title, count in Counter(titles).items() if count > 1]
    if duplicate_titles:
        errors.append(f"{name}: duplicate titles: {duplicate_titles}")

    validate_text(name, data, errors)
    return len(data)


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    counts = {
        name: validate_quiz(name, fields, errors, warnings)
        for name, fields in QUIZ_FILES.items()
    }
    counts["practicum.json"] = validate_practicum(errors)

    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)

    summary = ", ".join(f"{name}={count}" for name, count in counts.items())
    print(f"Validated content: {summary}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
