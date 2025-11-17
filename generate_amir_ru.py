import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence


@dataclass
class ParsedQuestion:
    id: int
    question: str
    options: List[str]
    correct_index: int
    explanation: str


QUESTION_PATTERN = re.compile(
    r"(?m)^(?P<number>\d{1,4})\s*[).:-]\s*(?P<body>.*?)(?=^\d{1,4}\s*[).:-]\s|\Z)",
    re.S | re.M,
)

OPTION_PATTERN = re.compile(r"^[A-H][).:-]\s*(.+)$", re.I)


def read_text_source(text_path: Path) -> str:
    try:
        return text_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return text_path.read_text(encoding="latin-1")


def split_question_blocks(text: str) -> Sequence[re.Match]:
    normalized = re.sub(r"\r\n?", "\n", text)
    return list(QUESTION_PATTERN.finditer(normalized))


def clean_text_segment(lines: Sequence[str]) -> str:
    return " ".join([l.strip() for l in lines if l.strip()]).strip()


def parse_question_block(block: re.Match) -> ParsedQuestion:
    number = int(block.group("number"))
    body = block.group("body").strip()

    lines = [line.strip() for line in body.splitlines() if line.strip()]
    question_lines = []
    option_lines = []
    explanation_lines = []

    in_options = False
    for line in lines:
        if OPTION_PATTERN.match(line):
            in_options = True
            option_lines.append(line)
        elif in_options:
            explanation_lines.append(line)
        else:
            question_lines.append(line)

    if not option_lines:
        return ParsedQuestion(number, "EMPTY", ["A"], 0, "No data")

    question_text = clean_text_segment(question_lines)

    options = []
    correct_index = 0  # default A

    for idx, option_line in enumerate(option_lines):
        m = OPTION_PATTERN.match(option_line)
        option_body = m.group(1).strip()
        options.append(option_body)

    explanation = clean_text_segment(explanation_lines)

    return ParsedQuestion(
        id=number,
        question=question_text,
        options=options,
        correct_index=correct_index,
        explanation=explanation,
    )


def extract_questions(text_path: Path) -> List[ParsedQuestion]:
    raw = read_text_source(text_path)
    blocks = split_question_blocks(raw)
    parsed = [parse_question_block(b) for b in blocks]
    parsed.sort(key=lambda q: q.id)
    return parsed


def save_questions(items: List[ParsedQuestion], output_path: Path) -> None:
    for idx, q in enumerate(items, start=1):
        q.id = idx

    payload = [
        {
            "id": q.id,
            "question": q.question,
            "options": q.options,
            "correct_index": q.correct_index,
            "explanation": q.explanation,
        }
        for q in items
    ]

    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract AMIR questions from text")
    parser.add_argument("--text", type=Path, default=Path("Libro Gordo.txt"))
    parser.add_argument("--output", type=Path, default=Path("amir_ru.json"))
    parser.add_argument("--skip-translation", action="store_true")

    args = parser.parse_args()

    text_path = args.text
    output_path = args.output

    if not text_path.exists():
        raise FileNotFoundError(f"Text file not found: {text_path}")

    print("Extracting...")
    questions = extract_questions(text_path)
    print(f"Detected {len(questions)} questions.")

    save_questions(questions, output_path)
    print(f"Saved {len(questions)} questions to {output_path}")


if __name__ == "__main__":
    main()
