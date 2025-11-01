from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InputFile
import json, random, os, asyncio
from typing import Optional
from datetime import datetime, timedelta
from pathlib import Path

# ======================
# НАСТРОЙКА
# ======================
BOT_TOKEN = "8242848619:AAF-hYX8z1oWNrNLqgvqEKGefBaJtZ7qB0I"
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

PROGRESS_FILE = "progress.json"
DATE_FMT = "%Y-%m-%d"
ADMIN_ID = 288158839  # твой chat_id
BASE_DIR = Path(__file__).resolve().parent
NEJM_FILE = BASE_DIR / "nejm_cases.json"
PRACTICUM_FILE = BASE_DIR / "practicum.json"

# ======================
# УТИЛИТЫ
# ======================
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
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_progress(progress):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)

def gather_question_images(q: dict):
    seen = set()
    entries = []
    for key in ("image", "images"):
        value = q.get(key)
        if isinstance(value, str):
            path = value.strip()
            if path and path not in seen:
                entries.append((path, q.get("image_caption", "")))
                seen.add(path)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    path = item.strip()
                    if path and path not in seen:
                        entries.append((path, None))
                        seen.add(path)
                elif isinstance(item, dict):
                    path = item.get("path")
                    caption = item.get("caption", "")
                    if path and path not in seen:
                        entries.append((path, caption))
                        seen.add(path)
    return entries

def resolve_image_source(source: str):
    if not source:
        return None
    source_str = str(source)
    if source_str.startswith(("http://", "https://")):
        return source_str
    local_path = (BASE_DIR / source_str).resolve()
    if local_path.exists():
        return InputFile(str(local_path))
    return source_str

# ======================
# ДАННЫЕ
# ======================
progress = load_progress()

with open("questions.json", encoding="utf-8") as f:
    questions = json.load(f)

def load_optional_json(path: Path):
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            try:
                return json.load(fh)
            except json.JSONDecodeError:
                print(f"⚠️ Не удалось прочитать {path.name}: ошибка формата JSON")
    return []

nejm_cases = load_optional_json(NEJM_FILE)
practicum_cards = load_optional_json(PRACTICUM_FILE)

Q_BY_ID = {int(q["id"]): q for q in questions}
TOPICS = sorted(set(q["topic"] for q in questions))
TOPIC_MAP = {i: t for i, t in enumerate(TOPICS)}
TOTAL_QUESTIONS = len(questions)
TOTAL_NEJM = len(nejm_cases)
TOTAL_PRACTICUM = len(practicum_cards)

# ======================
# ВСПОМОГАТЕЛЬНОЕ
# ======================
def get_user(uid: str, name_hint="Без имени"):
    u = progress.setdefault(uid, {
        "name": name_hint,
        "cards": {},
        "topics": {},
        "streak": 0,
        "last_goal_day": None,
        "last_review": None,
        "goal_per_day": 10,
        "done_today": 0,
        "last_day": today_str(),
        "nejm": {
            "queue": [],
            "answered": 0,
            "current": None
        },
        "practicum": {
            "index": 0
        }
    })
    if u.get("last_day") != today_str():
        u["done_today"] = 0
        u["last_day"] = today_str()
    u.setdefault("nejm", {"queue": [], "answered": 0, "current": None})
    u.setdefault("practicum", {"index": 0})
    return u

async def send_question(chat_id: int, topic_filter: str = None):
    uid = str(chat_id)
    u = get_user(uid)
    cards = u.get("cards", {})

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

    done_ids = {int(k) for k in cards.keys()}
    pool = [q for q in questions if int(q["id"]) not in done_ids]
    if topic_filter:
        pool = [q for q in pool if q.get("topic") == topic_filter]

    if not pool:
        await bot.send_message(chat_id, "Все вопросы по этой теме изучены. ✅")
        return
    q = random.choice(pool)
    await send_question_text(chat_id, q)

async def send_question_text(chat_id: int, q: dict):
    qid = q.get("id")
    text = f"🧠 {q['question']}\n\n"
    for i, opt in enumerate(q["options"]):
        text += f"{i + 1}) {opt}\n"

    kb = types.InlineKeyboardMarkup(row_width=3)
    for i in range(len(q["options"])):
        kb.insert(types.InlineKeyboardButton(str(i + 1), callback_data=f"a:{qid}:{i+1}"))
    kb.add(types.InlineKeyboardButton("⏭ Далее", callback_data="next"))

    media_entries = gather_question_images(q)
    for src, caption in media_entries:
        resolved = resolve_image_source(src)
        if not resolved:
            continue
        caption_text = caption.strip() if isinstance(caption, str) else None
        if caption_text and len(caption_text) > 1024:
            caption_text = caption_text[:1021] + "..."
        try:
            await bot.send_photo(chat_id, resolved, caption=caption_text)
        except Exception as exc:
            print(f"⚠️ Не удалось отправить изображение для вопроса {qid}: {src} — {exc}")

    await bot.send_message(chat_id, text, reply_markup=kb)

def ensure_nejm_queue(state: dict):
    if not nejm_cases:
        return []
    queue = state.get("queue")
    if not queue:
        queue = [item.get("id") for item in nejm_cases if item.get("id") is not None]
        random.shuffle(queue)
        state["queue"] = queue
    return queue

def get_nejm_case(case_id: int):
    for case in nejm_cases:
        if int(case.get("id", -1)) == int(case_id):
            return case
    return None

async def send_nejm_case(chat_id: int, *, notify_reset: bool = False):
    uid = str(chat_id)
    user = get_user(uid)
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
        await bot.send_message(chat_id, "Не удалось получить кейс. Попробуй позже.")
        save_progress(progress)
        return

    state["current"] = int(case_id)
    ordinal = (state.get("answered", 0) % max(1, TOTAL_NEJM)) + 1
    header = f"🩺 NEJM Clinical Case {ordinal} из {TOTAL_NEJM}"
    text = f"{header}\n\n{case['question']}\n\n" + "\n".join(
        f"{idx + 1}) {opt}" for idx, opt in enumerate(case.get("options", []))
    )
    kb = types.InlineKeyboardMarkup(row_width=2)
    for idx in range(len(case.get("options", []))):
        kb.insert(types.InlineKeyboardButton(str(idx + 1), callback_data=f"nejm:answer:{case_id}:{idx+1}"))

    await bot.send_message(chat_id, text, reply_markup=kb)
    save_progress(progress)

async def send_practicum_card(chat_id: int, direction: str = "stay", message: Optional[types.Message] = None):
    uid = str(chat_id)
    user = get_user(uid)
    state = user.setdefault("practicum", {"index": 0})
    if not practicum_cards:
        await bot.send_message(chat_id, "Практикум пока пуст. Добавь карточки в practicum.json.")
        return

    total = TOTAL_PRACTICUM
    idx = state.get("index", 0)
    if direction == "next":
        idx = (idx + 1) % total
    elif direction == "prev":
        idx = (idx - 1) % total
    state["index"] = idx

    card = practicum_cards[idx]
    title = card.get("title", "Практикум")
    body = card.get("content", "")
    footer = f"\n\n📚 Карточка {idx + 1} из {total}"
    text = f"{title}\n\n{body}{footer}".strip()

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("⬅️ Назад", callback_data="practicum:prev"),
        types.InlineKeyboardButton("⏭ Далее", callback_data="practicum:next")
    )

    if message is not None:
        await message.edit_text(text, reply_markup=kb)
    else:
        await bot.send_message(chat_id, text, reply_markup=kb)

    save_progress(progress)

def update_interval(card: dict, correct: bool):
    if correct:
        card["interval"] = min(max(1, card.get("interval", 1)) * 2, 60)
        next_day = datetime.now() + timedelta(days=card["interval"])
    else:
        card["interval"] = 1
        next_day = datetime.now() + timedelta(days=1)
    card["next_review"] = next_day.strftime(DATE_FMT)
    return card

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    uid = str(message.chat.id)
    uname = message.from_user.first_name or "Без имени"
    get_user(uid, uname)
    save_progress(progress)

    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⏭ Начать", callback_data="next"))
    await message.answer(
        f"👋 Привет, {uname}!\n\n"
        "Этот бот учит педиатрию с интервальным повторением.\n\n"
        "💡 Ошибки повторяются завтра, верные ответы — через 2, 4, 8 и т.д. дней.\n\n"
        "🎯 Ежедневная цель по умолчанию: 10 карточек.\n\n"
        f"📚 Всего доступно вопросов: {TOTAL_QUESTIONS}.\n\n"
        "💬 We are what we repeatedly do.\n\n"
        "Смотри /help.",
        reply_markup=kb
    )

@dp.message_handler(commands=["nejm"])
async def nejm_command(message: types.Message):
    if not nejm_cases:
        await message.answer("Пока нет кейсов NEJM. Добавь их в nejm_cases.json.")
        return
    intro = (
        "🩺 New England Journal of Medicine\n\n"
        "Клинические кейсы NEJM с изображениями и вопросами.\n\n"
        f"📦 Всего кейсов: {TOTAL_NEJM}.\n\n"
        "Нажми «Начать», чтобы получить первый кейс."
    )
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⏭ Начать", callback_data="nejm:next"))
    await message.answer(intro, reply_markup=kb)

@dp.message_handler(commands=["practicum"])
async def practicum_command(message: types.Message):
    if not practicum_cards:
        await message.answer("Практикум пока пуст. Добавь карточки в practicum.json.")
        return
    intro = (
        "🛠 Практикум по педиатрии\n\n"
        "Коллекция кратких карточек с советами и алгоритмами.\n\n"
        f"📦 Всего карточек: {TOTAL_PRACTICUM}.\n\n"
        "Нажми «Открыть», чтобы начать."
    )
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("📖 Открыть", callback_data="practicum:open"))
    await message.answer(intro, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "next")
async def callback_next(call: types.CallbackQuery):
    await call.answer()
    await send_question(call.message.chat.id)

# остальные callback-и остаются как в патче

if __name__ == "__main__":
    print("✅ Бот запущен и ждёт сообщений в Telegram...")
    import threading
    from server import app
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000), daemon=True).start()

    loop = asyncio.get_event_loop()
    loop.create_task(dp.bot.set_my_commands([
        types.BotCommand("start", "Начать"),
        types.BotCommand("help", "Помощь"),
        types.BotCommand("train", "Выбор темы"),
        types.BotCommand("review", "Повтор на сегодня"),
        types.BotCommand("stats", "Статистика"),
        types.BotCommand("goal", "Цель на день"),
        types.BotCommand("reset_topic", "Сброс темы"),
        types.BotCommand("reset", "Полный сброс"),
        types.BotCommand("nejm", "Клинические кейсы NEJM"),
        types.BotCommand("practicum", "Практикум по педиатрии"),
    ]))
    executor.start_polling(dp, skip_updates=True)