import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

from openai import OpenAI
from PyPDF2 import PdfReader


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
CORRECT_HINT_PATTERN = re.compile(r"(correcta|soluci[oó]n|correct\s*answer)[:\s]+([A-H])", re.I)


def extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)
    return "\n".join(pages)


def split_question_blocks(text: str) -> Sequence[re.Match]:
    normalized = re.sub(r"\r\n?", "\n", text)
    return list(QUESTION_PATTERN.finditer(normalized))


def clean_text_segment(lines: Sequence[str]) -> str:
    return " ".join([l.strip() for l in lines if l.strip()]).strip()


def parse_question_block(block: re.Match) -> ParsedQuestion:
    number = int(block.group("number"))
    body = block.group("body").strip()
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    question_lines: List[str] = []
    option_lines: List[str] = []
    explanation_lines: List[str] = []

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
        raise ValueError(f"No answer options detected for question {number}")

    question_text = clean_text_segment(question_lines)
    options: List[str] = []
    correct_index = None

    for idx, option_line in enumerate(option_lines):
        option_match = OPTION_PATTERN.match(option_line)
        option_body = option_match.group(1).strip()
        is_correct = False

        if option_line[0] in {"*", "✓", "✔"}:
            is_correct = True
        if any(marker in option_body for marker in ["*", "✓", "✔"]):
            is_correct = True
            for marker in ["*", "✓", "✔"]:
                option_body = option_body.replace(marker, "")

        lowered = option_body.lower()
        for token in ["correcta", "correct answer", "solucion", "solución", "verdadera", "respuesta correcta"]:
            if token in lowered:
                is_correct = True
                option_body = re.sub(token, "", option_body, flags=re.I)

        option_body = option_body.strip(" :-*✓✔().")
        options.append(option_body)
        if is_correct and correct_index is None:
            correct_index = idx

    explicit_correct = None
    for line in list(explanation_lines):
        correct_match = CORRECT_HINT_PATTERN.search(line)
        if correct_match:
            explicit_correct = ord(correct_match.group(2).upper()) - ord("A")
            explanation_lines.remove(line)
            break

    if correct_index is None:
        correct_index = explicit_correct

    if correct_index is None:
        raise ValueError(f"Could not determine correct answer for question {number}")

    explanation = clean_text_segment(explanation_lines)

    return ParsedQuestion(
        id=number,
        question=question_text,
        options=options,
        correct_index=correct_index,
        explanation=explanation,
    )


def translate_question(client: OpenAI, model: str, item: ParsedQuestion) -> ParsedQuestion:
    payload = {
        "id": item.id,
        "question": item.question,
        "options": item.options,
        "correct_index": item.correct_index,
        "explanation": item.explanation,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You are a professional medical translator. Translate the provided question, "
                "answer options, and explanation into Russian with academic precision and strict medical terminology. "
                "Preserve numbering and structure. Return JSON with keys: question, options, explanation. "
                "Do not shorten or omit information."
            ),
        },
        {
            "role": "user",
            "content": (
                "Translate the following payload into Russian. Keep options order and return JSON only.\n" + json.dumps(payload, ensure_ascii=False)
            ),
        },
    ]
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content
    translated = json.loads(content)
    return ParsedQuestion(
        id=item.id,
        question=translated.get("question", item.question).strip(),
        options=[opt.strip() for opt in translated.get("options", item.options)],
        correct_index=item.correct_index,
        explanation=translated.get("explanation", item.explanation).strip(),
    )


def translate_questions(items: List[ParsedQuestion], model: str) -> List[ParsedQuestion]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is required for translation")
    client = OpenAI(api_key=api_key)
    translated_items = []
    for item in items:
        translated_items.append(translate_question(client, model, item))
    return translated_items


def save_questions(items: List[ParsedQuestion], output_path: Path) -> None:
    ordered = sorted(items, key=lambda q: q.id)
    for idx, question in enumerate(ordered, start=1):
        question.id = idx
    payload = [
        {
            "id": q.id,
            "question": q.question,
            "options": q.options,
            "correct_index": q.correct_index,
            "explanation": q.explanation,
        }
        for q in ordered
    ]
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_questions(pdf_path: Path) -> List[ParsedQuestion]:
    raw_text = extract_pdf_text(pdf_path)
    blocks = split_question_blocks(raw_text)
    if not blocks:
        raise RuntimeError("No questions detected in the PDF")
    parsed = [parse_question_block(block) for block in blocks]
    parsed.sort(key=lambda q: q.id)
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract and translate AMIR questions from PDF to JSON")
    parser.add_argument("--pdf", type=Path, default=Path("Libro Gordo.pdf"), help="Path to the AMIR PDF file")
    parser.add_argument("--output", type=Path, default=Path("amir_ru.json"), help="Output JSON path")
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-4o-mini",
        help="OpenAI model to use for translation",
    )
    parser.add_argument(
        "--skip-translation",
        action="store_true",
        help="Skip OpenAI translation and keep source language",
    )

    args = parser.parse_args()

    pdf_path: Path = args.pdf
    output_path: Path = args.output

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    print(f"Extracting questions from {pdf_path} ...")
    questions = extract_questions(pdf_path)
    print(f"Detected {len(questions)} questions. Translating...")

    if args.skip_translation:
        translated_questions = questions
    else:
        translated_questions = translate_questions(questions, args.model)

    save_questions(translated_questions, output_path)
    print(f"Saved {len(translated_questions)} questions to {output_path}")


if __name__ == "__main__":
    main()
