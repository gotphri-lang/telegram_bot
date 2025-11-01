from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InputFile
import json, random, os, asyncio
from typing import Optional
from datetime import datetime, timedelta
from pathlib import Path

# ======================
# НАСТРОЙКА
# ======================
BOT_TOKEN = "8242848619:AAF-hYX8z1oWNrNLqgvqEKGefBaJtZ7qB0I"  # твой токен
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

BASE_DIR = Path(__file__).resolve().parent
PROGRESS_FILE = str(BASE_DIR / "progress.json")
NEJM_FILE = BASE_DIR / "nejm_cases.json"
PRACTICUM_FILE = BASE_DIR / "practicum.json"
QUESTIONS_FILE = BASE_DIR / "questions.json"

DATE_FMT = "%Y-%m-%d"
ADMIN_ID = 288158839  # твой chat_id

# Достижения за стрик (дни подряд)
ACHIEVEMENT_MILESTONES = [
    (1,  "🎈 Первый шаг"),
    (3,  "🔥 Разогрев"),
    (7,  "🏅 Неутомимый педиатр"),
    (14, "👑 Король отделения"),
    (30, "💎 Стальной клиницист"),
    (60, "🚀 Машина знаний"),
    (100,"🌟 Легенда поликлиники"),
    (180,"🏆 Хардмод-пример"),
    (365,"🎖️ Год без пропусков"),
]

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

def load_json(path: Path, default):
    if path.exists():
        with path.open(encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                pass
    return default

def save_progress(progress):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)

def split_text(text, limit=3500):
    return [text[i:i + limit] for i in range(0, len(text), limit)]

def gather_question_images(q: dict):
    seen = set()
    entries = []
    # primary
    primary = q.get("image")
    if isinstance(primary, str) and primary.strip():
        p = primary.strip()
        if p not in seen:
            entries.append((p, q.get("image_caption")))
            seen.add(p)
    # array
    imgs = q.get("images")
    if isinstance(imgs, list):
        caps = q.get("image_captions") if isinstance(q.get("image_captions"), list) else None
        for idx, item in enumerate(imgs):
            cap = None
            path = None
            if isinstance(item, dict):
                path = item.get("path") or item.get("url") or item.get("image")
                cap = item.get("caption")
            elif isinstance(item, str):
                path = item
                if caps and idx < len(caps):
                    cap = caps[idx]
            if path and path not in seen:
                entries.append((path, cap))
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
progress = load_json(Path(PROGRESS_FILE), {})
questions = load_json(QUESTIONS_FILE, [])
nejm_cases = load_json(NEJM_FILE, [])
practicum_cards = load_json(PRACTICUM_FILE, [])

Q_BY_ID = {int(q["id"]): q for q in questions if "id" in q}
TOPICS = sorted(set(q.get("topic", "Без темы") for q in questions))
TOPIC_MAP = {i: t for i, t in enumerate(TOPICS)}
TOTAL_QUESTIONS = len(questions)
TOTAL_NEJM = len(nejm_cases)
TOTAL_PRACTICUM = len(practicum_cards)

# ======================
# ПРОГРЕСС ПОЛЬЗОВАТЕЛЯ
# ======================
def get_user(uid: str, name_hint="Без имени"):
    u = progress.setdefault(uid, {
        "name": name_hint,
        "cards": {},              # {qid: {interval, next_review}}
        "topics": {},             # {topic: {correct,total}}
        "streak": 0,              # дни подряд
        "last_goal_day": None,    # когда цель достигнута
        "last_review": None,      # последний день занятия
        "goal_per_day": 10,
        "done_today": 0,
        "last_day": today_str(),
        "achievements": [],       # список названий достижений
        "nejm": {"queue": [], "answered": 0, "current": None},
        "practicum": {"index": 0},
        "done_total": 0           # всего отвечено карточек
    })
    # новый день — обнулить done_today
    if u.get("last_day") != today_str():
        u["done_today"] = 0
        u["last_day"] = today_str()
    # защита от отсутствующих ключей
    u.setdefault("achievements", [])
    u.setdefault("nejm", {"queue": [], "answered": 0, "current": None})
    u.setdefault("practicum", {"index": 0})
    u.setdefault("done_total", 0)
    return u

def update_interval(card: dict, correct: bool):
    if correct:
        card["interval"] = min(max(1, card.get("interval", 1)) * 2, 60)
        next_day = datetime.now() + timedelta(days=card["interval"])
    else:
        card["interval"] = 1
        next_day = datetime.now() + timedelta(days=1)
    card["next_review"] = next_day.strftime(DATE_FMT)
    return card

def maybe_award_achievement(u: dict):
    """Выдать достижение по стрику, если порог достигнут впервые."""
    streak = int(u.get("streak", 0))
    current_ach = set(u.get("achievements", []))
    awarded = []
    for days, title in ACHIEVEMENT_MILESTONES:
        if streak >= days and title not in current_ach:
            current_ach.add(title)
            awarded.append(title)
    if awarded:
        u["achievements"] = list(current_ach)
    return awarded

# ======================
# ЛОГИКА ВОПРОСОВ
# ======================
async def send_question(chat_id: int, topic_filter: Optional[str] = None):
    uid = str(chat_id)
    u = get_user(uid)
    cards = u.get("cards", {})

    # сначала — те, что пора повторить
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

    # иначе — новые
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

    # изображения (локальные из assets/** или URL)
    media_entries = gather_question_images(q)
    for src, caption in media_entries:
        resolved = resolve_image_source(src)
        if not resolved:
            continue
        cap = caption.strip() if isinstance(caption, str) else None
        if cap and len(cap) > 1024:
            cap = cap[:1021] + "..."
        try:
            await bot.send_photo(chat_id, resolved, caption=cap)
        except Exception as exc:
            print(f"⚠️ Не удалось отправить изображение для вопроса {qid}: {src} — {exc}")

    for idx, part in enumerate(split_text(text, 3500) or [text]):
        if idx == 0:
            await bot.send_message(chat_id, part, reply_markup=kb)
        else:
            await bot.send_message(chat_id, part)

# ======================
# NEJM
# ======================
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
        await bot.send_message(chat_id, "Не удалось получить клинический кейс. Попробуй ещё раз позже.")
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

    # изображения для кейса
    for src, caption in gather_question_images(case):
        resolved = resolve_image_source(src)
        cap = caption.strip() if isinstance(caption, str) else None
        if cap and len(cap) > 1024:
            cap = cap[:1021] + "..."
        try:
            await bot.send_photo(chat_id, resolved, caption=cap)
        except Exception as exc:
            print(f"⚠️ Не удалось отправить изображение для кейса {case_id}: {src} — {exc}")

    for idx, part in enumerate(split_text(text, 3500) or [text]):
        if idx == 0:
            await bot.send_message(chat_id, part, reply_markup=kb)
        else:
            await bot.send_message(chat_id, part)

    if notify_reset:
        await bot.send_message(chat_id, "Ты прошёл все кейсы — последовательность обновлена, можно продолжать! ✅")

    save_progress(progress)

# ======================
# PRACTICUM
# ======================
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
        try:
            await message.edit_text(text, reply_markup=kb)
        except Exception:
            await bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await bot.send_message(chat_id, text, reply_markup=kb)

    save_progress(progress)

# ======================
# КОМАНДЫ
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
        f"📚 Вопросов: {TOTAL_QUESTIONS}\n"
        f"🩺 NEJM кейсов: {TOTAL_NEJM} | 🛠 Практикум: {TOTAL_PRACTICUM}\n\n"
        "Смотри /help.",
        reply_markup=kb
    )

@dp.message_handler(commands=["help"])
async def help_cmd(message: types.Message):
    await message.answer(
        "🧭 Команды:\n"
        "/train — выбрать тему\n"
        "/review — повтор карточек на сегодня\n"
        "/stats — статистика\n"
        "/goal N — цель на день\n"
        "/reset_topic — сброс темы\n"
        "/reset — полный сброс\n"
        "/users — число пользователей (админ)\n"
        "/top_done — топ по количеству ответов\n"
        "/top_streak — топ по стрику\n"
        "/nejm — режим клинических кейсов (с картинками)\n"
        "/practicum — практикум (полезные карточки)\n"
    )

@dp.message_handler(commands=["goal"])
async def set_goal(message: types.Message):
    uid = str(message.chat.id)
    u = get_user(uid)
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return await message.answer("Формат: /goal 15 — сколько карточек в день.")
    goal = int(parts[1])
    u["goal_per_day"] = max(1, goal)
    save_progress(progress)
    await message.answer(f"🎯 Новая ежедневная цель: {u['goal_per_day']}.")

@dp.message_handler(commands=["train"])
async def choose_topic(message: types.Message):
    if not TOPICS:
        return await message.answer("Пока нет тем.")
    kb = types.InlineKeyboardMarkup(row_width=2)
    for idx, t in enumerate(TOPICS):
        kb.insert(types.InlineKeyboardButton(t, callback_data=f"train_{idx}"))
    await message.answer("🎯 Выбери тему для тренировки:", reply_markup=kb)

@dp.message_handler(commands=["review"])
async def review_today(message: types.Message):
    uid = str(message.chat.id)
    u = get_user(uid)
    due = [int(qid) for qid, meta in u.get("cards", {}).items() if is_due(meta.get("next_review"))]
    if not due:
        return await message.answer("✅ На сегодня нет карточек к повтору.")
    await message.answer(f"📘 Сегодня к повтору: {len(due)}.")
    qid = random.choice(due)
    await send_question_text(message.chat.id, Q_BY_ID[qid])

@dp.message_handler(commands=["stats"])
async def stats(message: types.Message):
    uid = str(message.chat.id)
    u = get_user(uid)
    total = len(u.get("cards", {}))
    due = sum(1 for meta in u.get("cards", {}).values() if is_due(meta.get("next_review")))
    goal = u.get("goal_per_day", 10)
    done = u.get("done_today", 0)
    streak = u.get("streak", 0)
    total_correct = sum(t["correct"] for t in u.get("topics", {}).values()) if u.get("topics") else 0
    total_answers = sum(t["total"] for t in u.get("topics", {}).values()) if u.get("topics") else 0
    acc = round(100 * total_correct / total_answers) if total_answers else 0
    ach = u.get("achievements", [])
    ach_str = "• " + "\n• ".join(ach) if ach else "—"

    msg = (
        f"🎯 Цель: {goal}/день\n"
        f"📊 Сегодня: {done}/{goal}\n"
        f"🔥 Серия: {streak} дней\n"
        f"📘 Изучено карточек (уникальных): {total}\n"
        f"📅 К повтору: {due}\n"
        f"💯 Точность: {acc}%\n\n"
        f"🏆 Достижения:\n{ach_str}"
    )
    await message.answer(msg)

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

@dp.message_handler(commands=["top_done"])
async def top_done(message: types.Message):
    # ранжируем по done_total
    rows = []
    for uid, u in progress.items():
        name = u.get("name", "Без имени")
        rows.append((int(u.get("done_total", 0)), name))
    rows.sort(reverse=True)
    lines = [f"{i+1}. {name} — {cnt}" for i, (cnt, name) in enumerate(rows[:10])]
    await message.answer("🏆 Топ по количеству ответов:\n" + ("\n".join(lines) if lines else "—"))

@dp.message_handler(commands=["top_streak"])
async def top_streak(message: types.Message):
    rows = []
    for uid, u in progress.items():
        name = u.get("name", "Без имени")
        rows.append((int(u.get("streak", 0)), name))
    rows.sort(reverse=True)
    lines = [f"{i+1}. {name} — {cnt} дн." for i, (cnt, name) in enumerate(rows[:10])]
    await message.answer("🔥 Топ по стрику:\n" + ("\n".join(lines) if lines else "—"))

@dp.message_handler(commands=["reset_topic"])
async def reset_topic(message: types.Message):
    if not TOPICS:
        return await message.answer("Пока нет тем.")
    kb = types.InlineKeyboardMarkup(row_width=2)
    for idx, t in enumerate(TOPICS):
        kb.insert(types.InlineKeyboardButton(t, callback_data=f"reset_{idx}"))
    await message.answer("Выбери тему для сброса:", reply_markup=kb)

@dp.message_handler(commands=["reset"])
async def reset_all(message: types.Message):
    uid = str(message.chat.id)
    uname = message.from_user.first_name or "Без имени"
    progress[uid] = {
        "name": uname,
        "cards": {},
        "topics": {},
        "streak": 0,
        "last_goal_day": None,
        "last_review": None,
        "goal_per_day": 10,
        "done_today": 0,
        "last_day": today_str(),
        "achievements": [],
        "nejm": {"queue": [], "answered": 0, "current": None},
        "practicum": {"index": 0},
        "done_total": 0
    }
    save_progress(progress)
    await message.answer("🔄 Полный сброс. Начинай с /start или /train.")

@dp.message_handler(commands=["nejm"])
async def nejm_command(message: types.Message):
    if not nejm_cases:
        await message.answer("Пока нет кейсов NEJM. Добавь их в nejm_cases.json.")
        return
    intro = (
        "🩺 NEJM — клинические кейсы с изображениями и вопросами.\n\n"
        f"📦 Всего кейсов: {TOTAL_NEJM}.\n"
        "Нажми «Начать», чтобы получить первый случай."
    )
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⏭ Начать", callback_data="nejm:next"))
    await message.answer(intro, reply_markup=kb)

@dp.message_handler(commands=["practicum"])
async def practicum_command(message: types.Message):
    if not practicum_cards:
        await message.answer("Практикум пока пуст. Добавь карточки в practicum.json.")
        return
    intro = (
        "🛠 Практикум по педиатрии\n"
        f"📦 Всего карточек: {TOTAL_PRACTICUM}.\n"
        "Нажми «Открыть», чтобы начать."
    )
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("📖 Открыть", callback_data="practicum:open"))
    await message.answer(intro, reply_markup=kb)

# ======================
# CALLBACK’И
# ======================
@dp.callback_query_handler(lambda c: c.data == "next")
async def callback_next(call: types.CallbackQuery):
    await call.answer()
    await send_question(call.message.chat.id)

@dp.callback_query_handler(lambda c: c.data.startswith("train_"))
async def train_topic(call: types.CallbackQuery):
    await call.answer()
    try:
        idx = int(call.data.replace("train_", "", 1))
        topic = TOPIC_MAP[idx]
    except Exception:
        await bot.send_message(call.from_user.id, "⚠️ Ошибка выбора темы.")
        return
    await bot.send_message(call.from_user.id, f"📚 Тема: {topic}")
    await send_question(call.from_user.id, topic_filter=topic)

@dp.callback_query_handler(lambda c: c.data.startswith("reset_"))
async def do_reset_topic(call: types.CallbackQuery):
    await call.answer()
    try:
        idx = int(call.data.replace("reset_", "", 1))
        topic = TOPIC_MAP[idx]
    except Exception:
        await bot.send_message(call.from_user.id, "⚠️ Ошибка выбора темы.")
        return
    uid = str(call.from_user.id)
    u = get_user(uid)
    to_del = [qid for qid, obj in Q_BY_ID.items() if obj.get("topic") == topic]
    for qid in to_del:
        u["cards"].pop(str(qid), None)
    save_progress(progress)
    await bot.send_message(uid, f"♻️ Сбросили прогресс по теме «{topic}».")

@dp.callback_query_handler(lambda c: c.data.startswith("a:"))
async def callback_answer(call: types.CallbackQuery):
    try:
        _, qid_str, answer_str = call.data.split(":")
        qid = int(qid_str)
        user_answer = int(answer_str) - 1
    except (ValueError, IndexError):
        await call.answer("Не удалось обработать ответ", show_alert=True)
        return

    q = Q_BY_ID.get(qid)
    if not q:
        await call.answer("Вопрос не найден", show_alert=True)
        return

    uid = str(call.message.chat.id)
    user = get_user(uid)
    cards = user.setdefault("cards", {})
    card = cards.setdefault(str(qid), {"interval": 1, "next_review": today_str()})

    topic = q.get("topic", "Без темы")
    topic_stats = user.setdefault("topics", {}).setdefault(topic, {"correct": 0, "total": 0})
    topic_stats["total"] += 1

    correct_index = int(q.get("correct_index", 0))
    is_correct = user_answer == correct_index
    if is_correct:
        topic_stats["correct"] += 1
    update_interval(card, is_correct)

    # учёт активности за день/всего
    if user.get("last_day") != today_str():
        user["done_today"] = 0
        user["last_day"] = today_str()
    user["done_today"] = user.get("done_today", 0) + 1
    user["done_total"] = user.get("done_total", 0) + 1

    # стрик (когда цель достигнута впервые за день)
    goal = user.get("goal_per_day", 10)
    if user["done_today"] >= goal and user.get("last_goal_day") != today_str():
        user["streak"] = user.get("streak", 0) + 1
        user["last_goal_day"] = today_str()
        gained = maybe_award_achievement(user)
        if gained:
            try:
                await call.message.answer("🏆 Новые достижения:\n" + "\n".join(f"• {x}" for x in gained))
            except Exception:
                pass

    user["last_review"] = today_str()
    save_progress(progress)

    explanation = q.get("explanation", "").strip()
    correct_option = q["options"][correct_index]
    status = "✅ Верно!" if is_correct else "❌ Неверно."
    reply = f"{status}\n\nПравильный ответ: {correct_option}"
    if explanation:
        reply += f"\n\n{explanation}"

    try:
        await call.message.edit_reply_markup()
    except Exception:
        pass

    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⏭ Далее", callback_data="next"))
    await call.answer("Верно" if is_correct else "Неверно", show_alert=False)
    await call.message.answer(reply, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("nejm:"))
async def callback_nejm(call: types.CallbackQuery):
    parts = call.data.split(":")
    if len(parts) < 2:
        await call.answer()
        return
    action = parts[1]
    uid = str(call.message.chat.id)
    user = get_user(uid)
    state = user.setdefault("nejm", {"queue": [], "answered": 0, "current": None})

    if action == "next":
        try:
            await call.message.edit_reply_markup()
        except Exception:
            pass
        await call.answer()
        await send_nejm_case(call.message.chat.id)
        return

    if action == "answer" and len(parts) == 4:
        try:
            case_id = int(parts[2])
            answer_idx = int(parts[3]) - 1
        except ValueError:
            await call.answer("Ошибка ответа", show_alert=True)
            return

        case = get_nejm_case(case_id)
        if not case:
            await call.answer("Кейс не найден", show_alert=True)
            return

        correct_index = int(case.get("correct_index", 0))
        is_correct = answer_idx == correct_index
        state["answered"] = state.get("answered", 0) + 1
        save_progress(progress)

        options = case.get("options", [])
        correct_option = options[correct_index] if 0 <= correct_index < len(options) else "—"
        status = "✅ Верно!" if is_correct else "❌ Неверно."
        reply = f"{status}\n\nПравильный ответ: {correct_option}"
        explanation = case.get("explanation")
        if explanation:
            reply += f"\n\n{explanation}"

        try:
            await call.message.edit_reply_markup()
        except Exception:
            pass

        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⏭ Далее", callback_data="nejm:next"))
        await call.answer("Верно" if is_correct else "Неверно")
        await call.message.answer(reply, reply_markup=kb)
        return

    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("practicum:"))
async def callback_practicum(call: types.CallbackQuery):
    parts = call.data.split(":", maxsplit=1)
    if len(parts) != 2:
        await call.answer()
        return
    action = parts[1]
    await call.answer()
    if action == "open":
        await send_practicum_card(call.message.chat.id, direction="stay", message=call.message)
    elif action == "next":
        await send_practicum_card(call.message.chat.id, direction="next", message=call.message)
    elif action == "prev":
        await send_practicum_card(call.message.chat.id, direction="prev", message=call.message)

# ======================
# ЗАПУСК (POLLING ONLY)
# ======================
if __name__ == "__main__":
    # Команды в меню бота
    asyncio.get_event_loop().run_until_complete(
        dp.bot.set_my_commands([
            types.BotCommand("start", "Начать"),
            types.BotCommand("help", "Помощь"),
            types.BotCommand("train", "Выбор темы"),
            types.BotCommand("review", "Повтор на сегодня"),
            types.BotCommand("stats", "Статистика"),
            types.BotCommand("goal", "Цель на день"),
            types.BotCommand("reset_topic", "Сброс темы"),
            types.BotCommand("reset", "Полный сброс"),
            types.BotCommand("nejm", "Кейсы NEJM"),
            types.BotCommand("practicum", "Практикум"),
            types.BotCommand("top_done", "Топ отвеченных"),
            types.BotCommand("top_streak", "Топ стрика"),
        ])
    )
    print("✅ Бот запущен (polling)...")
    executor.start_polling(dp, skip_updates=True)