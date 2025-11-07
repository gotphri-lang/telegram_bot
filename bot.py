# -*- coding: utf-8 -*-
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InputFile
import json, random, os, asyncio
from typing import Optional, List, Tuple
from datetime import datetime, timedelta
from pathlib import Path

# ======================
# НАСТРОЙКА
# ======================
BOT_TOKEN = "8242848619:AAF2wA3EazZZD38fMHcTjeSNx-D-cDb85HQ"
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

def split_text(text, limit=3500):
    return [text[i:i + limit] for i in range(0, len(text), limit)]

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

def resolve_image_source(source: str):
    if not source:
        return None
    s = str(source)
    if s.startswith(("http://", "https://")):
        return s
    local_path = (BASE_DIR / s).resolve()
    if local_path.exists():
        return InputFile(str(local_path))
    return s  # пусть телега попробует как URL/путь

# ======================
# ДАННЫЕ
# ======================
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

Q_BY_ID = {int(q["id"]): q for q in questions}
TOPICS = sorted(set(q.get("topic", "Без темы") for q in questions))
TOPIC_MAP = {i: t for i, t in enumerate(TOPICS)}
TOTAL_QUESTIONS = len(questions)
TOTAL_NEJM = len(nejm_cases)
TOTAL_PRACTICUM = len(practicum_cards)

# ======================
# ДОСТИЖЕНИЯ / ТОКЕНЫ
# ======================
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
        "streak": 0,
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
    u.setdefault("best_streak", 0)
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
    streak = u.get("streak", 0)
    for n, title in STREAK_MILESTONES:
        if streak >= n:
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

# ======================
# ЛОГИКА ВОПРОСОВ
# ======================
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

# ======================
# КОМАНДЫ
# ======================
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    uid = str(message.chat.id)
    uname = message.from_user.first_name or "Без имени"
    ensure_user(uid, uname)
    save_progress(progress)

    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⏭ Начать", callback_data="next"))
    await message.answer(
        f"👋 Привет, {uname}!\n\n"
        "Этот бот учит педиатрию с интервальным повторением.\n\n"
        "💡 Ошибки - завтра, верные - через 2, 4, 8... дней.\n\n"
        "📚 Разделы:\n"
        f"🧠 PediaMed - {TOTAL_QUESTIONS}\n"
        f"🩺 NEJM - {TOTAL_NEJM}\n"
        f"🛠 PediaPracticum - {TOTAL_PRACTICUM}\n\n"
        "Смотри /help.",
        reply_markup=kb
    )

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

@dp.message_handler(commands=["stats"])
async def stats(message: types.Message):
    uid = str(message.chat.id)
    u = ensure_user(uid)
    total = len(u.get("cards", {}))
    due = sum(1 for meta in u.get("cards", {}).values() if is_due(meta.get("next_review")))
    goal = u.get("goal_per_day", 10)
    done = u.get("done_today", 0)
    streak = u.get("streak", 0)
    best = u.get("best_streak", 0)
    total_correct = sum(t["correct"] for t in u.get("topics", {}).values()) if u.get("topics") else 0
    total_answers = sum(t["total"] for t in u.get("topics", {}).values()) if u.get("topics") else 0
    acc = round(100 * total_correct / total_answers) if total_answers else 0
    tokens = u.get("tokens", 0)
    msg = (
        f"🎯 Цель: {goal}/день\n"
        f"📊 Сегодня: {done}/{goal}\n"
        f"🔥 Стрик: {streak} (лучший {best})\n"
        f"📘 Изучено карточек: {total}\n"
        f"📅 К повтору: {due}\n"
        f"💯 Точность: {acc}%\n"
        f"🪙 Токены: {tokens}\n"
        f"🏅 Достижений: {len(u.get('achievements', []))}"
    )
    await message.answer(msg)

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

@dp.message_handler(commands=["top_streak"])
async def top_streak_cmd(message: types.Message):
    items = []
    for uid, u in progress.items():
        items.append((u.get("name", uid), u.get("best_streak", 0)))
    items.sort(key=lambda x: x[1], reverse=True)
    top = items[:10]
    if not top:
        return await message.answer("Топ пуст.")
    lines = [f"{i+1}. {name}: {st}" for i, (name, st) in enumerate(top)]
    await message.answer("🔥 Топ по лучшему стрику:\n" + "\n".join(lines))

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
        "streak": 0,
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

# ======================
# NEJM
# ======================
@dp.message_handler(commands=["nejm"])
async def nejm_command(message: types.Message):
    if not nejm_cases:
        await message.answer("Пока нет кейсов NEJM. Добавь их в nejm_cases.json.")
        return
    intro = (
        "🩺 NEJM Clinical Cases\n\n"
        f"📦 Всего кейсов: {TOTAL_NEJM}.\n\n"
        "Нажми «Начать», чтобы получить случай (картинки без подписей)."
    )
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⏭ Начать", callback_data="nejm:next"))
    await message.answer(intro, reply_markup=kb)

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
        await bot.send_message(chat_id, "Не удалось получить клинический кейс. Попробуй ещё раз позже.")
        save_progress(progress)
        return

    state["current"] = int(case_id)
    ordinal = (state.get("answered", 0) % max(1, TOTAL_NEJM)) + 1
    header = f"🩺 NEJM Case {ordinal}/{TOTAL_NEJM}"
    text = f"{header}\n\n{case['question']}\n\n" + "\n".join(
        f"{idx + 1}) {opt}" for idx, opt in enumerate(case.get("options", []))
    )
    # картинки (без подписей)
    images = gather_images(case)
    if images:
        await send_images(chat_id, images)

    kb = types.InlineKeyboardMarkup(row_width=2)
    for idx in range(len(case.get("options", []))):
        kb.insert(types.InlineKeyboardButton(str(idx + 1), callback_data=f"nejm:answer:{case_id}:{idx+1}"))

    parts = split_text(text, 3500) or [text]
    for i, part in enumerate(parts):
        if i == 0:
            await bot.send_message(chat_id, part, reply_markup=kb)
        else:
            await bot.send_message(chat_id, part)

    if notify_reset:
        await bot.send_message(chat_id, "Ты прошёл все кейсы — последовательность обновлена. ✅")

    save_progress(progress)

@dp.callback_query_handler(lambda c: c.data.startswith("nejm:"))
async def callback_nejm(call: types.CallbackQuery):
    parts = call.data.split(":")
    if len(parts) < 2:
        await call.answer()
        return
    action = parts[1]
    uid = str(call.message.chat.id)
    user = ensure_user(uid)
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

# ======================
# PRACTICUM
# ======================
@dp.message_handler(commands=["practicum"])
async def practicum_command(message: types.Message):
    if not practicum_cards:
        await message.answer("Практикум пока пуст. Добавь карточки в practicum.json.")
        return
    intro = (
        "🛠 Практикум по педиатрии\n\n"
        f"📦 Всего карточек: {TOTAL_PRACTICUM}.\n\n"
        "Нажми «Открыть», чтобы просмотреть первую карточку."
    )
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("📖 Открыть", callback_data="practicum:open"))
    await message.answer(intro, reply_markup=kb)

async def send_practicum_card(chat_id: int, direction: str = "stay", message_obj: Optional[types.Message] = None):
    uid = str(chat_id)
    user = ensure_user(uid)
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

    if message_obj is not None:
        try:
            await message_obj.edit_text(text, reply_markup=kb)
        except Exception:
            await bot.send_message(chat_id, text, reply_markup=kb)
    else:
        await bot.send_message(chat_id, text, reply_markup=kb)

    save_progress(progress)

@dp.callback_query_handler(lambda c: c.data.startswith("practicum:"))
async def callback_practicum(call: types.CallbackQuery):
    parts = call.data.split(":", maxsplit=1)
    if len(parts) != 2:
        await call.answer()
        return
    action = parts[1]
    await call.answer()
    if action == "open":
        await send_practicum_card(call.message.chat.id, direction="stay", message_obj=call.message)
    elif action == "next":
        await send_practicum_card(call.message.chat.id, direction="next", message_obj=call.message)
    elif action == "prev":
        await send_practicum_card(call.message.chat.id, direction="prev", message_obj=call.message)

# ======================
# CALLBACK: ответы по обычным вопросам
# ======================
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
        u["streak"] = u.get("streak", 0) + 1
        u["best_streak"] = max(u.get("best_streak", 0), u["streak"])
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
    for part in split_text("\n".join(reply_lines), 3000):
        await bot.send_message(uid, part, reply_markup=kb if part.endswith(")") or part.endswith("⏭ Далее") else None)
        kb = None  # чтобы не повторять клавиатуру на каждом куске

# ======================
# ЗАПУСК
# ======================
if __name__ == "__main__":
    print("✅ Бот запущен и ждёт сообщений в Telegram...")

    # фоновый HTTP-сервер (если используется на хостинге)
    try:
        import threading
        from server import app
        threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000), daemon=True).start()
    except Exception as e:
        print(f"ℹ️ server.py не запущен: {e}")

    loop = asyncio.get_event_loop()
    loop.create_task(dp.bot.set_my_commands([
        types.BotCommand("start", "Начать"),
        types.BotCommand("help", "Помощь"),
        types.BotCommand("train", "Выбор темы"),
        types.BotCommand("review", "Повтор на сегодня"),
        types.BotCommand("stats", "Статистика"),
        types.BotCommand("achievements", "Достижения"),
        types.BotCommand("top_done", "Топ ответов"),
        types.BotCommand("top_streak", "Топ стрика"),
        types.BotCommand("goal", "Цель на день"),
        types.BotCommand("reset_topic", "Сброс темы"),
        types.BotCommand("reset", "Полный сброс"),
        types.BotCommand("users", "Пользователи (админ)"),
        types.BotCommand("nejm", "NEJM кейсы"),
        types.BotCommand("practicum", "Практикум"),
    ]))
    executor.start_polling(dp, skip_updates=True)