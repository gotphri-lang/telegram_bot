# -*- coding: utf-8 -*-
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InputFile
from dotenv import load_dotenv
import asyncio
import json
import logging
import os
import random
import threading
from typing import Optional, List, Tuple
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not configured. Add it to Render Environment.")

_admin_id = os.getenv("ADMIN_ID", "").strip()
if not _admin_id.isdigit():
    raise RuntimeError("ADMIN_ID must contain only the Telegram administrator's numeric ID.")
ADMIN_ID = int(_admin_id)

DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_FILE = DATA_DIR / "progress.json"
DATE_FMT = "%Y-%m-%d"
_PROGRESS_LOCK = threading.RLock()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
NEJM_FILE = BASE_DIR / "nejm_cases.json"
PRACTICUM_FILE = BASE_DIR / "practicum.json"
AMIR_FILE = BASE_DIR / "amir_ru.json"

def today_str():
    return datetime.now().strftime(DATE_FMT)

def is_due(date_str: str):
    if not date_str:
        return False
    try:
        d = datetime.strptime(date_str, DATE_FMT).date()
    except Exception:
        return False
    return datetime.now().date() >= d

def load_progress():
    if not PROGRESS_FILE.exists():
        return {}
    with _PROGRESS_LOCK:
        try:
            with PROGRESS_FILE.open(encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            logging.exception("Could not load progress data")
            return {}

def save_progress(progress):
    temp_file = PROGRESS_FILE.with_suffix(".tmp")
    payload = json.dumps(progress, ensure_ascii=False, indent=2)
    with _PROGRESS_LOCK:
        temp_file.write_text(payload, encoding="utf-8")
        os.chmod(temp_file, 0o600)
        os.replace(temp_file, PROGRESS_FILE)

def split_text(text, limit=3500):
    """Разбивает текст на части, стараясь сохранять абзацы."""
    if not text:
        return [""]

    text = text.strip()
    if len(text) <= limit:
        return [text]

    parts: List[str] = []
    current = ""

    def flush_current():
        nonlocal current
        if current:
            parts.append(current)
            current = ""

    paragraphs = [p.strip() for p in text.split("\n\n")]
    for para in paragraphs:
        if not para:
            continue
        candidate = f"{current}\n\n{para}".strip() if current else para
        if len(candidate) <= limit:
            current = candidate
            continue

        flush_current()

        if len(para) <= limit:
            current = para
            continue

        # Абзац слишком длинный — делим его по лимиту
        for i in range(0, len(para), limit):
            chunk = para[i:i + limit]
            if len(chunk) == limit:
                parts.append(chunk)
            else:
                current = chunk

    flush_current()
    return parts or [text]


def prettify_label(label: str) -> str:
    text = str(label or "").strip().replace("_", " ").replace("-", " ")
    text = " ".join(text.split())
    if text:
        text = text[0].upper() + text[1:]
    return text


PRACTICUM_SECTION_ICONS = (
    ("правило", "📏"),
    ("проблем", "⚠️"),
    ("рекомендац", "📝"),
    ("тактик", "🛠"),
    ("направлен", "➡️"),
    ("чтовидим", "👀"),
    ("чтознаем", "💡"),
    ("чтообъясняем", "💬"),
    ("чтоделаем", "🧭"),
    ("контрол", "🕒"),
    ("возраст", "🎯"),
    ("орт", "🦴"),
    ("стоп", "🦶"),
    ("колен", "🦵"),
    ("рентген", "🩻"),
)

PRACTICUM_BULLET_SIGNS = ("•", "-", "—", "▪", "▫", "►")


def _normalize_practicum_label(label: str) -> str:
    return "".join(ch for ch in label.lower() if ch.isalnum())


def pick_practicum_icon(label: str) -> str:
    normalized = _normalize_practicum_label(label)
    for keyword, icon in PRACTICUM_SECTION_ICONS:
        if keyword in normalized:
            return icon
    return "🔹"


def stylize_practicum_paragraph(paragraph: str) -> str:
    for mark in PRACTICUM_BULLET_SIGNS:
        if paragraph.startswith(mark):
            content = paragraph[len(mark):].strip()
            return f"{mark} {content}".strip()
    return f"• {paragraph}".strip()


def format_practicum_content(raw: str) -> str:
    if not raw:
        return ""

    lines = raw.replace("\r", "").split("\n")
    paragraphs: List[str] = []
    current: List[str] = []

    def flush_current():
        nonlocal current
        if current:
            paragraphs.append(" ".join(current).strip())
            current = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_current()
            continue
        if stripped.startswith(PRACTICUM_BULLET_SIGNS):
            flush_current()
            paragraphs.append(stripped)
            continue
        current.append(stripped)

    flush_current()

    formatted = [stylize_practicum_paragraph(p) for p in paragraphs if p]
    return "\n\n".join(formatted).strip()


def format_practicum_body(card: dict) -> str:
    data = card.get("data")
    if isinstance(data, dict):
        sections = []
        for key, value in data.items():
            content = format_practicum_content(str(value).strip())
            if not content:
                continue
            label = prettify_label(key)
            icon = pick_practicum_icon(label) if label else "🔹"
            header = f"{icon} {label}".strip()
            sections.append(f"{header}\n{content}")
        return "\n\n".join(sections).strip()

    content = str(card.get("content", "")).strip()
    return format_practicum_content(content)

def gather_images(obj: dict) -> List[str]:
    """
    Поддерживает:
      - "image": "path-or-url"
      - "images": ["path-or-url", {...}, ...]  (dict может содержать path/url/image/caption — подписи игнорируем)
    Возвращает список источников без подписей.
    """
    seen = set()
    out: List[str] = []

    def push(p):
        if not p: return
        s = str(p).strip()
        if not s or s in seen: return
        seen.add(s); out.append(s)

    if isinstance(obj.get("image"), str):
        push(obj["image"])

    imgs = obj.get("images")
    if isinstance(imgs, list):
        for item in imgs:
            if isinstance(item, str):
                push(item)
            elif isinstance(item, dict):
                push(item.get("path") or item.get("url") or item.get("image"))

    return out

def _find_existing_image_variant(path: Path) -> Optional[Path]:
    """Ищем файл с другой частой картинкой (например, .jpeg вместо .jpg)."""

    suffix = path.suffix.lower()
    stems = [suffix] if suffix else []
    alternatives = [".jpeg", ".jpg", ".png"]

    for alt_ext in alternatives:
        if alt_ext in stems:
            continue
        candidate = path.with_suffix(alt_ext)
        if candidate.exists():
            return candidate
    return None


def resolve_image_source(source: str):
    if not source:
        return None
    s = str(source)
    if s.startswith(("http://", "https://")):
        return s
    local_path = (BASE_DIR / s).resolve()
    if local_path.exists():
        return InputFile(str(local_path))

    fallback = _find_existing_image_variant(local_path)
    if fallback:
        return InputFile(str(fallback))

    return s  # пусть телега попробует как URL/путь

progress = load_progress()

with open(str(BASE_DIR / "questions.json"), encoding="utf-8") as f:
    questions = json.load(f)

def load_optional_json(path: Path):
    if path.exists():
        try:
            with path.open(encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as e:
            print(f"⚠️ {path.name}: {e}")
    return []

nejm_cases = load_optional_json(NEJM_FILE)
practicum_cards = load_optional_json(PRACTICUM_FILE)
amir_questions = load_optional_json(AMIR_FILE)

Q_BY_ID = {int(q["id"]): q for q in questions}
AMIR_BY_ID = {int(q["id"]): q for q in amir_questions}

TOPICS = [
    "Педиатрия",
    "Неонатология",
    "Инфекционные болезни",
    "Неврология",
    "Кардиология",
    "Эндокринология",
    "Нефрология",
    "Гастроэнтерология",
    "Пульмонология",
    "Ревматология",
]
TOPIC_MAP = {i: t for i, t in enumerate(TOPICS)}

TOTAL_QUESTIONS = len(questions)
TOTAL_NEJM = len(nejm_cases)
TOTAL_PRACTICUM = len(practicum_cards)
TOTAL_AMIR = len(amir_questions)


@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    uid = str(message.chat.id)
    uname = message.from_user.first_name or "Без имени"
    ensure_user(uid, uname)
    save_progress(progress)

    kb = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("⏭ Начать", callback_data="next")
    )
    await message.answer(
        f"👋 Привет, {uname}!\n\n"
        "Этот бот помогает учить педиатрию с интервальным повторением.\n\n"
        "💡 Ошибки - завтра, верные - через 2, 4, 8... дней.\n\n"
        f"📚 Разделы:\n🧠 PediaMed - {TOTAL_QUESTIONS}\n"
        f"🩺 NEJM - {TOTAL_NEJM}\n"
        f"📘 AMIR - {TOTAL_AMIR}\n"
        f"🛠 Practicum - {TOTAL_PRACTICUM}\n\n"
        "Смотри /help для всех команд.",
        reply_markup=kb,
    )

@dp.message_handler(commands=["help"])
async def help_cmd(message: types.Message):
    await message.answer(
        "📘 Доступные команды:\n"
        "/start – начать обучение\n"
        "/help – эта справка\n"
        "/train – выбрать тему\n"
        "/review – повторить карточки на сегодня\n"
        "/stats – посмотреть статистику\n"
        "/goal N – задать дневную цель\n"
        "/achievements – достижения\n"
        "/nejm – клинические кейсы NEJM\n"
        "/amir – вопросы AMIR\n"
        "/practicum – Practicum по педиатрии\n"
        "/reset – сбросить всё\n"
        "/reset_topic – сбросить по теме\n"
        "/top_done – топ по ответам\n"
        "/users – все пользователи (админ)"
    )


@dp.message_handler(commands=["nejm"])
async def nejm_cmd(message: types.Message):
    await send_nejm_case(message.chat.id)


@dp.message_handler(commands=["amir"])
async def amir_cmd(message: types.Message):
    await send_amir_question_srs(message.chat.id)


@dp.message_handler(commands=["practicum"])
async def practicum_cmd(message: types.Message):
    await send_practicum_card(message.chat.id)

@dp.message_handler(commands=["stats"])
async def stats(message: types.Message):
    uid = str(message.chat.id)
    u = ensure_user(uid)
    total = len(u.get("cards", {}))
    due = sum(1 for m in u.get("cards", {}).values() if is_due(m.get("next_review")))
    goal = u.get("goal_per_day", 10)
    done = u.get("done_today", 0)
    
    # ИСПОЛЬЗУЕМ НОВЫЕ КЛЮЧИ:
    current_streak = u.get("current_streak", 0)
    best_streak = u.get("best_streak", 0)
    
    total_correct = sum(t["correct"] for t in u.get("topics", {}).values()) if u.get("topics") else 0
    total_answers = sum(t["total"] for t in u.get("topics", {}).values()) if u.get("topics") else 0
    acc = round(100 * total_correct / total_answers) if total_answers else 0
    tokens = u.get("tokens", 0)

    msg = (
        f"🎯 Цель: {goal}/день\n"
        f"📊 Сегодня: {done}/{goal}\n"
        # ИЗМЕНЕННАЯ ФРАЗА:
        f"🔥 Дней подряд: {current_streak} (лучший результат: {best_streak})\n"
        f"📘 Изучено карточек: {total}\n"
        f"📅 К повтору: {due}\n"
        f"💯 Точность: {acc}%\n"
        f"🪙 Токены: {tokens}\n"
        f"🏅 Достижений: {len(u.get('achievements', []))}"
    )
    await message.answer(msg)

# Стрики (дни подряд)
STREAK_MILESTONES = [
    (1,   "Старт дан"),
    (3,   "На ходу"),
    (7,   "Неделя в строю"),
    (14,  "Две недели"),
    (30,  "Король отделения"),
    (100, "Неутомимый педиатр"),
    (365, "Железный год"),
]
# Всего отвеченных карточек
TOTAL_DONE_MILESTONES = [
    (10,   "Первые шаги"),
    (50,   "Разогрев"),
    (100,  "Стабильный темп"),
    (250,  "Сильная форма"),
    (500,  "Полтысячи"),
    (1000, "Тысяча ответов"),
]

ACH_REWARD_TOKENS = 10  # сколько токенов за каждое новое достижение

def ensure_user(uid: str, name_hint="Без имени"):
    u = progress.setdefault(uid, {
        "name": name_hint,
        "cards": {},
        "topics": {},
        # ИСПОЛЬЗУЕМ НОВЫЕ КЛЮЧИ:
        "current_streak": 0, 
        "best_streak": 0,
        "last_goal_day": None,
        "last_review": None,
        "goal_per_day": 10,
        "done_today": 0,
        "last_day": today_str(),
        "total_answered": 0,
        "tokens": 0,
        "achievements": [],  # список названий
        "nejm": {"queue": [], "answered": 0, "current": None},
        "practicum": {"index": 0}
    })
    # новый день — обнуляем done_today
    if u.get("last_day") != today_str():
        u["done_today"] = 0
        u["last_day"] = today_str()
    # поля на всякий
    # Обновляем старые ключи на новые, если они есть.
    if "Серия дней подряд" in u:
        u["current_streak"] = u.pop("Серия дней подряд")
    if "best_Серия дней подряд" in u:
        u["best_streak"] = u.pop("best_Серия дней подряд")

    u.setdefault("best_streak", 0)
    u.setdefault("current_streak", 0)
    u.setdefault("total_answered", 0)
    u.setdefault("tokens", 0)
    u.setdefault("achievements", [])
    u.setdefault("nejm", {"queue": [], "answered": 0, "current": None})
    u.setdefault("practicum", {"index": 0})
    u.setdefault("topics", {})
    u.setdefault("cards", {})
    return u

def award_achievement(u: dict, name: str) -> Optional[str]:
    if name in u.get("achievements", []):
        return None
    u["achievements"].append(name)
    u["tokens"] = u.get("tokens", 0) + ACH_REWARD_TOKENS
    return name

def check_awards_after_answer(u: dict) -> List[str]:
    gained = []
    # по общему количеству ответов
    total = u.get("total_answered", 0)
    for n, title in TOTAL_DONE_MILESTONES:
        if total >= n:
            got = award_achievement(u, title)
            if got:
                gained.append(got)
    # по стрику
    # ИСПОЛЬЗУЕМ НОВЫЙ КЛЮЧ:
    current_streak = u.get("current_streak", 0)
    for n, title in STREAK_MILESTONES:
        if current_streak >= n:
            got = award_achievement(u, title)
            if got:
                gained.append(got)
    return gained

def ensure_nejm_queue(state: dict):
    if not nejm_cases:
        return []
    q = state.get("queue") or []
    if not q:
        q = [int(item.get("id")) for item in nejm_cases if item.get("id") is not None]
        random.shuffle(q)
        state["queue"] = q
    return q

def get_nejm_case(case_id: int):
    for case in nejm_cases:
        if int(case.get("id", -1)) == int(case_id):
            return case
    return None

async def send_images(chat_id: int, sources: List[str]):
    for src in sources:
        resolved = resolve_image_source(src)
        if not resolved:
            continue
        try:
            # БЕЗ ПОДПИСЕЙ
            await bot.send_photo(chat_id, resolved)
        except Exception as exc:
            print(f"⚠️ image send failed: {src} — {exc}")


async def send_first_image(chat_id: int, sources: List[str]):
    """Отправляет первое доступное изображение из списка."""
    for src in sources:
        resolved = resolve_image_source(src)
        if not resolved:
            continue
        try:
            await bot.send_photo(chat_id, resolved)
            return True
        except Exception as exc:
            print(f"⚠️ image send failed: {src} — {exc}")
    return False

async def send_question(chat_id: int, topic_filter: Optional[str] = None):
    uid = str(chat_id)
    u = ensure_user(uid)
    cards = u.get("cards", {})

    # сначала — due
    due_ids = []
    for qid_str, meta in cards.items():
        if is_due(meta.get("next_review")):
            qid = int(qid_str)
            if topic_filter and Q_BY_ID.get(qid, {}).get("topic") != topic_filter:
                continue
            due_ids.append(qid)

    if due_ids:
        qid = random.choice(due_ids)
        return await send_question_text(chat_id, Q_BY_ID[qid])

    # потом новые
    done_ids = {int(k) for k in cards.keys()}
    pool = [q for q in questions if int(q["id"]) not in done_ids]
    if topic_filter:
        pool = [q for q in pool if q.get("topic") == topic_filter]

    if not pool:
        await bot.send_message(chat_id, "🎉 Все вопросы пройдены или запланированы на повтор.")
        return

    q = random.choice(pool)
    await send_question_text(chat_id, q)

async def send_question_text(chat_id: int, q: dict):
    qid = int(q["id"])
    topic = q.get("topic", "Вопрос")
    text = f"🧠 {topic}\n\n{q['question']}\n\n" + "\n".join(
        f"{i+1}) {opt}" for i, opt in enumerate(q["options"])
    )
    # сначала картинки (без подписей)
    images = gather_images(q)
    if images:
        await send_images(chat_id, images)

    # кнопки
    kb = types.InlineKeyboardMarkup(row_width=3)
    for i in range(len(q["options"])):
        kb.insert(types.InlineKeyboardButton(str(i + 1), callback_data=f"a:{qid}:{i+1}"))
    kb.add(types.InlineKeyboardButton("⏭ Далее", callback_data="next"))
    # текст вопроса
    parts = split_text(text, 3500) or [text]
    for idx, part in enumerate(parts):
        if idx == 0:
            await bot.send_message(chat_id, part, reply_markup=kb)
        else:
            await bot.send_message(chat_id, part)

def update_interval(card: dict, correct: bool):
    if correct:
        card["interval"] = min(max(1, card.get("interval", 1)) * 2, 60)
        next_day = datetime.now() + timedelta(days=card["interval"])
    else:
        card["interval"] = 1
        next_day = datetime.now() + timedelta(days=1)
    card["next_review"] = next_day.strftime(DATE_FMT)
    return card


@dp.message_handler(commands=["goal"])
async def set_goal(message: types.Message):
    uid = str(message.chat.id)
    u = ensure_user(uid)
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return await message.answer("Формат: /goal 15 — карточек в день.")
    goal = int(parts[1])
    u["goal_per_day"] = max(1, goal)
    save_progress(progress)
    await message.answer(f"🎯 Новая цель: {u['goal_per_day']} в день.")

@dp.message_handler(commands=["train"])
async def choose_topic(message: types.Message):
    if not TOPICS:
        return await message.answer("Пока нет тем.")
    kb = types.InlineKeyboardMarkup(row_width=2)
    for idx, t in enumerate(TOPICS):
        kb.insert(types.InlineKeyboardButton(t, callback_data=f"train_{idx}"))
    await message.answer("🎯 Выбери тему:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("train_"))
async def train_topic(callback_query: types.CallbackQuery):
    await callback_query.answer()
    try:
        idx = int(callback_query.data.replace("train_", "", 1))
        topic = TOPIC_MAP[idx]
    except Exception:
        await bot.send_message(callback_query.from_user.id, "⚠️ Ошибка выбора темы.")
        return
    await bot.send_message(callback_query.from_user.id, f"📚 Тема: {topic}")
    await send_question(callback_query.from_user.id, topic_filter=topic)

@dp.message_handler(commands=["review"])
async def review_today(message: types.Message):
    uid = str(message.chat.id)
    u = ensure_user(uid)
    due = [int(qid) for qid, meta in u.get("cards", {}).items() if is_due(meta.get("next_review"))]
    if not due:
        return await message.answer("✅ На сегодня нет карточек к повтору.")
    await message.answer(f"📘 Сегодня к повтору: {len(due)}.")
    qid = random.choice(due)
    await send_question_text(message.chat.id, Q_BY_ID[qid])

@dp.message_handler(commands=["achievements"])
async def achievements_cmd(message: types.Message):
    uid = str(message.chat.id)
    u = ensure_user(uid)
    ach = u.get("achievements", [])
    tokens = u.get("tokens", 0)
    if not ach:
        return await message.answer(f"🏅 Пока нет достижений.\n🪙 Токены: {tokens}")
    text = "🏅 Твои достижения:\n" + "\n".join(f"• {a}" for a in ach) + f"\n\n🪙 Токены: {tokens}"
    await message.answer(text)

@dp.message_handler(commands=["top_done"])
async def top_done_cmd(message: types.Message):
    # сорт по total_answered
    items = []
    for uid, u in progress.items():
        items.append((u.get("name", uid), u.get("total_answered", 0)))
    items.sort(key=lambda x: x[1], reverse=True)
    top = items[:10]
    if not top:
        return await message.answer("Топ пуст.")
    lines = [f"{i+1}. {name}: {cnt}" for i, (name, cnt) in enumerate(top)]
    await message.answer("🏆 Топ по количеству ответов:\n" + "\n".join(lines))

# УДАЛЕНА ФУНКЦИЯ top_Серия дней подряд_cmd

@dp.message_handler(commands=["users"])
async def users_count(message: types.Message):
    uid = str(message.chat.id)
    if uid != str(ADMIN_ID):
        return await message.answer("⛔ Команда только для администратора.")
    try:
        count = len(progress.keys())
        await message.answer(f"👥 Всего пользователей: {count}")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}")

@dp.message_handler(commands=["reset_topic"])
async def reset_topic(message: types.Message):
    if not TOPICS:
        return await message.answer("Пока нет тем.")
    kb = types.InlineKeyboardMarkup(row_width=2)
    for idx, t in enumerate(TOPICS):
        kb.insert(types.InlineKeyboardButton(t, callback_data=f"reset_{idx}"))
    await message.answer("Выбери тему для сброса:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("reset_"))
async def do_reset_topic(callback_query: types.CallbackQuery):
    await callback_query.answer()
    try:
        idx = int(callback_query.data.replace("reset_", "", 1))
        topic = TOPIC_MAP[idx]
    except Exception:
        await bot.send_message(callback_query.from_user.id, "⚠️ Ошибка выбора темы.")
        return
    uid = str(callback_query.from_user.id)
    u = ensure_user(uid)
    to_del = [qid for qid, obj in Q_BY_ID.items() if obj.get("topic") == topic]
    for qid in to_del:
        u["cards"].pop(str(qid), None)
    save_progress(progress)
    await bot.send_message(uid, f"♻️ Сбросили прогресс по теме «{topic}».")

@dp.message_handler(commands=["reset"])
async def reset_all(message: types.Message):
    uid = str(message.chat.id)
    uname = message.from_user.first_name or "Без имени"
    progress[uid] = {
        "name": uname,
        "cards": {},
        "topics": {},
        # ИСПОЛЬЗУЕМ НОВЫЕ КЛЮЧИ:
        "current_streak": 0,
        "best_streak": 0,
        "last_goal_day": None,
        "last_review": None,
        "goal_per_day": 10,
        "done_today": 0,
        "last_day": today_str(),
        "total_answered": 0,
        "tokens": 0,
        "achievements": [],
        "nejm": {"queue": [], "answered": 0, "current": None},
        "practicum": {"index": 0}
    }
    save_progress(progress)
    await message.answer("🔄 Полный сброс. Начинай с /start или /train.")

async def send_nejm_case(chat_id: int, *, notify_reset: bool = False):
    uid = str(chat_id)
    user = ensure_user(uid)
    state = user.setdefault("nejm", {"queue": [], "answered": 0, "current": None})
    queue = ensure_nejm_queue(state)

    if not nejm_cases:
        await bot.send_message(chat_id, "Пока нет кейсов NEJM. Добавь их в nejm_cases.json.")
        return

    if not queue:
        state["answered"] = 0
        queue = ensure_nejm_queue(state)
        notify_reset = True

    case_id = queue.pop(0)
    case = get_nejm_case(case_id)

    if not case:
        await bot.send_message(
            chat_id,
            "Не удалось получить клинический кейс. Попробуй ещё раз позже."
        )
        save_progress(progress)
        return

    state["current"] = int(case_id)
    ordinal = (state.get("answered", 0) % max(1, TOTAL_NEJM)) + 1
    header = f"🩺 NEJM Case {ordinal}/{TOTAL_NEJM}"
    text = (
        f"{header}\n\n{case['question']}\n\n"
        + "\n".join(f"{idx + 1}) {opt}" for idx, opt in enumerate(case.get("options", [])))
    )

    # Всегда показываем ровно одно изображение: берём первое из доступных.
    images = gather_images(case)
    if images:
        await send_first_image(chat_id, images)

    kb = types.InlineKeyboardMarkup(row_width=2)
    for idx in range(len(case.get("options", []))):
        kb.insert(
            types.InlineKeyboardButton(
                str(idx + 1),
                callback_data=f"nejm:answer:{case_id}:{idx + 1}"
            )
        )

    parts = split_text(text, 3500) or [text]
    for i, part in enumerate(parts):
        if i == 0:
            await bot.send_message(chat_id, part, reply_markup=kb)
        else:
            await bot.send_message(chat_id, part)

    if notify_reset:
        await bot.send_message(
            chat_id,
            "Ты прошёл все кейсы — последовательность обновлена. ✅"
        )

    save_progress(progress)


async def send_amir_question(chat_id: int, *, notify_reset: bool = False):
    return await send_amir_question_srs(chat_id)


async def send_amir_question_srs(chat_id: int):
    uid = str(chat_id)
    user = ensure_user(uid)
    cards = user.get("cards", {})

    if not amir_questions:
        await bot.send_message(chat_id, "Пока нет вопросов AMIR. Добавь их в amir_ru.json.")
        return

    due_ids = []
    for qid_str, meta in cards.items():
        try:
            qid = int(qid_str)
        except Exception:
            continue
        if qid not in AMIR_BY_ID:
            continue
        if is_due(meta.get("next_review")):
            due_ids.append(qid)

    if due_ids:
        qid = random.choice(due_ids)
        q = AMIR_BY_ID.get(qid)
        if not q:
            await bot.send_message(chat_id, "Не удалось получить вопрос AMIR. Попробуй ещё раз позже.")
            return
        return await send_amir_question_text(chat_id, q)

    done_ids = set()
    for qid_str in cards.keys():
        try:
            qid = int(qid_str)
        except Exception:
            continue
        if qid in AMIR_BY_ID:
            done_ids.add(qid)

    pool_new = [q for q in amir_questions if int(q.get("id")) not in done_ids]
    if not pool_new:
        await bot.send_message(chat_id, "🎉 Все вопросы AMIR пройдены или запланированы на повтор.")
        return

    q = random.choice(pool_new)
    ordinal = len(done_ids) + 1
    await send_amir_question_text(chat_id, q, ordinal=ordinal)


async def send_amir_question_text(chat_id: int, q: dict, ordinal: Optional[int] = None):
    question_id = int(q.get("id"))
    header = "📘 AMIR"
    if ordinal:
        header = f"{header} {ordinal}/{TOTAL_AMIR}"

    text = (
        f"{header}\n\n{q['question']}\n\n"
        + "\n".join(f"{idx + 1}) {opt}" for idx, opt in enumerate(q.get("options", [])))
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    for idx in range(len(q.get("options", []))):
        kb.insert(
            types.InlineKeyboardButton(
                str(idx + 1),
                callback_data=f"amir:answer:{question_id}:{idx + 1}"
            )
        )

    parts = split_text(text, 3500) or [text]
    for i, part in enumerate(parts):
        reply_markup = kb if i == len(parts) - 1 else None
        await bot.send_message(chat_id, part, reply_markup=reply_markup)


# ИСПРАВЛЕННАЯ ФУНКЦИЯ ДЛЯ КОРРЕКТНОЙ ОБРАБОТКИ ДЛИННОГО ТЕКСТА
async def send_practicum_card(chat_id: int, direction: str = "stay", message_obj: Optional[types.Message] = None):
    uid = str(chat_id)
    user = ensure_user(uid)
    state = user.setdefault("practicum", {"index": 0})
    if not practicum_cards:
        await bot.send_message(chat_id, "Practicum пока пуст. Добавь карточки в practicum.json.")
        return

    total = TOTAL_PRACTICUM
    idx = state.get("index", 0)
    if direction == "next":
        idx = (idx + 1) % total
    elif direction == "prev":
        idx = (idx - 1) % total
    state["index"] = idx

    card = practicum_cards[idx]
    title = card.get("title", "Practicum")

    body = format_practicum_body(card)

    footer = f"\n\n📚 Карточка {idx + 1} из {total}"
    text = f"{title}\n\n{body}{footer}".strip()

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("⬅️ Назад", callback_data="practicum:prev"),
        types.InlineKeyboardButton("⏭ Далее", callback_data="practicum:next")
    )

    parts = split_text(text, 3500)  # <--- Разбиваем текст


    # Если это короткое сообщение (одна часть) и есть что редактировать:
    if message_obj is not None and len(parts) == 1:
        try:
            # Редактируем существующее сообщение для плавной навигации
            await message_obj.edit_text(text, reply_markup=kb)
        except Exception:
            # Отправляем новое сообщение, если редактирование не удалось
            await bot.send_message(chat_id, text, reply_markup=kb)
    else:
        # Если карточка слишком длинная (более 1 части) или нет сообщения для редактирования, 
        # отправляем новое(ые) сообщение(я). 
        # Сначала пробуем удалить старую клавиатуру, если есть.
        if message_obj is not None:
            try:
                await message_obj.edit_reply_markup()
            except Exception:
                pass # Игнорируем ошибку, если сообщение уже изменено/удалено

        for idx_part, part in enumerate(parts):
            # Прикрепляем клавиатуру только к последней части сообщения
            reply_markup = kb if idx_part == len(parts) - 1 else None
            await bot.send_message(chat_id, part, reply_markup=reply_markup)

    save_progress(progress)

@dp.callback_query_handler(lambda c: c.data.startswith("practicum:"))
async def callback_practicum(call: types.CallbackQuery):
    parts = call.data.split(":", maxsplit=1)
    if len(parts) != 2:
        await call.answer()
        return
    action = parts[1]
    await call.answer()
    
    # Логика теперь использует message_obj для попытки редактирования, если это возможно
    if action == "open":
        # Если "open" вызвано из сообщения, пытаемся его отредактировать
        await send_practicum_card(call.message.chat.id, direction="stay", message_obj=call.message)
    elif action == "next":
        await send_practicum_card(call.message.chat.id, direction="next", message_obj=call.message)
    elif action == "prev":
        await send_practicum_card(call.message.chat.id, direction="prev", message_obj=call.message)



# CALLBACK: ответы по NEJM

@dp.callback_query_handler(lambda c: c.data.startswith("nejm:answer:"))
async def handle_nejm_answer(callback_query: types.CallbackQuery):
    await callback_query.answer()

    parts = callback_query.data.split(":", maxsplit=3)
    if len(parts) != 4:
        return

    _, _, case_id_raw, opt_raw = parts
    try:
        case_id = int(case_id_raw)
        chosen_idx = int(opt_raw) - 1
    except Exception:
        return

    case = get_nejm_case(case_id)
    if not case:
        return

    uid = str(callback_query.from_user.id)
    user = ensure_user(uid)
    state = user.setdefault("nejm", {"queue": [], "answered": 0, "current": None})

    correct_idx = int(case.get("correct_index", 0))
    correct = chosen_idx == correct_idx

    state["answered"] = state.get("answered", 0) + 1
    state["current"] = None
    save_progress(progress)

    status = "✅ Верно!" if correct else "❌ Неверно."
    explanation = case.get("explanation", "").strip()

    reply_lines = [status]
    if not correct:
        options = case.get("options", [])
        if 0 <= correct_idx < len(options):
            reply_lines.append(f"Правильный ответ: {options[correct_idx]}")
    if explanation:
        reply_lines.extend(["", explanation])

    parts_reply = split_text("\n".join(reply_lines), 3000)

    # Удаляем старую клавиатуру, чтобы исключить повторные нажатия
    try:
        await callback_query.message.edit_reply_markup()
    except Exception:
        pass

    kb = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("⏭ Далее", callback_data="nejm:next")
    )

    for idx, part in enumerate(parts_reply):
        reply_markup = kb if idx == len(parts_reply) - 1 else None
        await bot.send_message(uid, part, reply_markup=reply_markup)


@dp.callback_query_handler(lambda c: c.data == "nejm:next")
async def handle_nejm_next(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await send_nejm_case(callback_query.from_user.id)


@dp.callback_query_handler(lambda c: c.data.startswith("amir:answer:"))
async def handle_amir_answer(callback_query: types.CallbackQuery):
    await callback_query.answer()

    parts = callback_query.data.split(":", maxsplit=3)
    if len(parts) != 4:
        return

    _, _, qid_raw, opt_raw = parts
    try:
        question_id = int(qid_raw)
        chosen_idx = int(opt_raw) - 1
    except Exception:
        return

    obj = AMIR_BY_ID.get(question_id)
    if not obj:
        return

    uid = str(callback_query.from_user.id)
    user = ensure_user(uid)

    correct_idx = int(obj.get("correct_index", 0))
    correct = chosen_idx == correct_idx

    cards = user.setdefault("cards", {})
    qid_str = str(question_id)
    card = cards.get(qid_str, {"interval": 1, "next_review": today_str()})
    update_interval(card, correct)
    cards[qid_str] = card

    topic = obj.get("topic", "AMIR")
    tdata = user.setdefault("topics", {}).setdefault(topic, {"correct": 0, "total": 0})
    tdata["total"] += 1
    if correct:
        tdata["correct"] += 1

    if user.get("last_day") != today_str():
        user["done_today"] = 0
        user["last_day"] = today_str()

    user["done_today"] = user.get("done_today", 0) + 1

    goal = user.get("goal_per_day", 10)
    if user["done_today"] >= goal and user.get("last_goal_day") != today_str():
        user["current_streak"] = user.get("current_streak", 0) + 1
        user["best_streak"] = max(user.get("best_streak", 0), user["current_streak"])
        user["last_goal_day"] = today_str()

    user["total_answered"] = user.get("total_answered", 0) + 1

    gained = check_awards_after_answer(user)

    save_progress(progress)

    status = "✅ Верно!" if correct else "❌ Неверно."
    explanation = obj.get("explanation", "").strip()

    reply_lines = [status]
    if not correct:
        options = obj.get("options", [])
        if 0 <= correct_idx < len(options):
            reply_lines.append(f"Правильный ответ: {options[correct_idx]}")
    if explanation:
        reply_lines.extend(["", explanation])
    if gained:
        reply_lines.append("")
        for a in gained:
            reply_lines.append(f"🎖 Новое достижение: {a} (+{ACH_REWARD_TOKENS} токенов)")

    parts_reply = split_text("\n".join(reply_lines), 3000)

    try:
        await callback_query.message.edit_reply_markup()
    except Exception:
        pass

    kb = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("⏭ Далее", callback_data="amir:next")
    )

    for idx, part in enumerate(parts_reply):
        reply_markup = kb if idx == len(parts_reply) - 1 else None
        await bot.send_message(uid, part, reply_markup=reply_markup)


@dp.callback_query_handler(lambda c: c.data == "amir:next")
async def handle_amir_next(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await send_amir_question_srs(callback_query.from_user.id)


# CALLBACK: ответы по обычным вопросам

@dp.callback_query_handler(lambda c: c.data == "next")
async def next_card(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await send_question(callback_query.from_user.id)

@dp.callback_query_handler(lambda c: c.data.startswith("a:"))
async def handle_answer(callback_query: types.CallbackQuery):
    await callback_query.answer()
    uid = str(callback_query.from_user.id)
    u = ensure_user(uid)

    try:
        _, qid_str, opt_str = callback_query.data.split(":")
        qid = int(qid_str)
        chosen_idx = int(opt_str) - 1
    except Exception:
        return

    q = Q_BY_ID.get(qid)
    if not q:
        return

    correct = (chosen_idx == int(q.get("correct_index", 0)))

    cards = u.setdefault("cards", {})
    card = cards.get(qid_str, {"interval": 1, "next_review": today_str()})
    update_interval(card, correct)
    cards[qid_str] = card

    topic = q.get("topic", "Без темы")
    tdata = u.setdefault("topics", {}).setdefault(topic, {"correct": 0, "total": 0})
    tdata["total"] += 1
    if correct:
        tdata["correct"] += 1

    # дневной прогресс / стрик
    if u.get("last_day") != today_str():
        u["done_today"] = 0
        u["last_day"] = today_str()
        
    u["done_today"] = u.get("done_today", 0) + 1

    goal = u.get("goal_per_day", 10)
    if u["done_today"] >= goal and u.get("last_goal_day") != today_str():
        # ИСПОЛЬЗУЕМ НОВЫЕ КЛЮЧИ:
        u["current_streak"] = u.get("current_streak", 0) + 1
        u["best_streak"] = max(u.get("best_streak", 0), u["current_streak"])
        u["last_goal_day"] = today_str()

    # общий счёт
    u["total_answered"] = u.get("total_answered", 0) + 1

    # достижения
    gained = check_awards_after_answer(u)

    save_progress(progress)

    status = "✅ Верно!" if correct else "❌ Неверно."
    explanation = q.get("explanation", "").strip()
    reply_lines = [status]
    if explanation:
        reply_lines.append("")
        reply_lines.append(explanation)
    if gained:
        reply_lines.append("")
        for a in gained:
            reply_lines.append(f"🎖 Новое достижение: {a} (+{ACH_REWARD_TOKENS} токенов)")

    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⏭ Далее", callback_data="next"))
    parts = split_text("\n".join(reply_lines), 3000)

    # УДАЛЯЕМ старую клавиатуру из сообщения с вопросом
    try:
        await callback_query.message.edit_reply_markup()
    except Exception:
        pass

    # ОТПРАВЛЯЕМ ответ, прикрепляя клавиатуру к последней части
    for idx, part in enumerate(parts):
        # Клавиатура прикрепляется ТОЛЬКО к последней части сообщения
        reply_markup = kb if idx == len(parts) - 1 else None
        await bot.send_message(uid, part, reply_markup=reply_markup)


async def on_startup(_dispatcher):
    # Remove any stale webhook and queued updates left during the incident.
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_my_commands([
        types.BotCommand("start", "Начать"),
        types.BotCommand("help", "Помощь"),
        types.BotCommand("train", "Выбор темы"),
        types.BotCommand("review", "Повтор на сегодня"),
        types.BotCommand("stats", "Статистика"),
        types.BotCommand("achievements", "Достижения"),
        types.BotCommand("top_done", "Топ ответов"),
        types.BotCommand("goal", "Цель на день"),
        types.BotCommand("reset_topic", "Сброс темы"),
        types.BotCommand("reset", "Полный сброс"),
        types.BotCommand("nejm", "NEJM кейсы"),
        types.BotCommand("amir", "Вопросы AMIR"),
        types.BotCommand("practicum", "Practicum"),
    ])


if __name__ == "__main__":
    print("✅ Бот запущен и ждёт сообщений в Telegram...")

    # Render Web Service health endpoint.
    try:
        from server import app
        port = int(os.getenv("PORT", "10000"))
        threading.Thread(
            target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False),
            daemon=True,
        ).start()
    except Exception:
        logging.exception("server.py не запущен")

    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)