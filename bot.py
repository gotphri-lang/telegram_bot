from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InputFile
import json, random, os, asyncio
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

def split_text(text, limit=3500):
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def gather_question_images(q: dict):
    entries = []
    seen = set()

    primary = q.get("image")
    if primary:
        entries.append((primary, q.get("image_caption")))
        seen.add(primary)

    raw_images = q.get("images")
    if isinstance(raw_images, list):
        captions = q.get("image_captions") if isinstance(q.get("image_captions"), list) else None
        for idx, item in enumerate(raw_images):
            caption = None
            path = None
            if isinstance(item, dict):
                path = item.get("path") or item.get("url") or item.get("image")
                caption = item.get("caption")
            elif isinstance(item, str):
                path = item
                if captions and idx < len(captions):
                    caption = captions[idx]
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

Q_BY_ID = {int(q["id"]): q for q in questions}
TOPICS = sorted(set(q["topic"] for q in questions))
TOPIC_MAP = {i: t for i, t in enumerate(TOPICS)}
TOTAL_QUESTIONS = len(questions)

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
        "last_day": today_str()
    })
    if u.get("last_day") != today_str():
        u["done_today"] = 0
        u["last_day"] = today_str()
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

# ======================
# /start
# ======================
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

# остальные команды остаются такими, как у тебя сейчас
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
    ]))
    executor.start_polling(dp, skip_updates=True)