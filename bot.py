from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InputFile
import json, random, os, asyncio
from typing import Optional, List, Tuple
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

# Поддержим оба имени файла на всякий случай
NEJM_FILE_MAIN = BASE_DIR / "nejm_cases.json"
NEJM_FILE_ALT  = BASE_DIR / "nejm.cases.json"
PRACTICUM_FILE = BASE_DIR / "practicum.json"

# ======================
# УТИЛИТЫ
# ======================
def today_str() -> str:
    return datetime.now().strftime(DATE_FMT)

def is_due(date_str: str) -> bool:
    if not date_str:
        return False
    try:
        d = datetime.strptime(date_str, DATE_FMT).date()
    except Exception:
        return False
    return datetime.now().date() >= d

def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_progress(progress: dict) -> None:
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)

def split_text(text: str, limit: int = 3500) -> List[str]:
    return [text[i:i+limit] for i in range(0, len(text), limit)]

def load_json(path: Path) -> list:
    if path.exists():
        try:
            with path.open(encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            print(f"⚠️ Не удалось прочитать {path.name}")
    return []

def gather_question_images(q: dict) -> List[Tuple[str, Optional[str]]]:
    seen = set()
    entries: List[Tuple[str, Optional[str]]] = []
    # primary
    prim = q.get("image")
    if isinstance(prim, str) and prim.strip():
        entries.append((prim.strip(), q.get("image_caption")))
        seen.add(prim.strip())
    # list forms
    imgs = q.get("images")
    if isinstance(imgs, list):
        caps = q.get("image_captions") if isinstance(q.get("image_captions"), list) else None
        for i, item in enumerate(imgs):
            path, cap = None, None
            if isinstance(item, str):
                path = item.strip()
                cap = (caps[i] if (caps and i < len(caps)) else None)
            elif isinstance(item, dict):
                path = item.get("path") or item.get("url") or item.get("image")
                cap  = item.get("caption")
            if path and path not in seen:
                entries.append((path, cap))
                seen.add(path)
    return entries

def resolve_image_source(source: str):
    if not source:
        return None
    s = str(source)
    if s.startswith(("http://","https://")):
        return s
    local = (BASE_DIR / s).resolve()
    if local.exists():
        return InputFile(str(local))
    return s

# ======================
# ДАННЫЕ
# ======================
progress = load_progress()

with open("questions.json", encoding="utf-8") as f:
    questions = json.load(f)

# NEJM: поддержим оба имени
nejm_cases = load_json(NEJM_FILE_MAIN)
if not nejm_cases:
    nejm_cases = load_json(NEJM_FILE_ALT)

practicum_cards = load_json(PRACTICUM_FILE)

Q_BY_ID = {int(q["id"]): q for q in questions}
TOPICS = sorted(set(q.get("topic", "Без темы") for q in questions))
TOPIC_MAP = {i: t for i, t in enumerate(TOPICS)}
TOTAL_QUESTIONS = len(questions)
TOTAL_NEJM = len(nejm_cases)
TOTAL_PRACTICUM = len(practicum_cards)

# ======================
# ДОСТИЖЕНИЯ (как раньше + формулировки)
# ======================
# По streak (серия дней достижения дневной цели)
ACHIEVEMENTS_STREAK = [
    (1,   "Первый шаг",              "🟢"),
    (3,   "Разогреваемся",          "🔥"),
    (7,   "Непотушимый педиатр",    "💪"),
    (14,  "Сила привычки",          "🧠"),
    (30,  "Месяц стабильности",     "📅"),
    (60,  "Железная дисциплина",    "🧲"),
    (100, "Столетник",              "🏅"),
    (180, "Полугодовой марафон",    "🎽"),
    (365, "Король отделения",       "👑"),
]

# По общему количеству ответов
ACHIEVEMENTS_DONE = [
    (50,   "50 ответов — старт дан",         "🏁"),
    (100,  "100 ответов — уверенно идёшь",   "🚀"),
    (250,  "250 ответов — хороший тонус",    "⚙️"),
    (500,  "500 ответов — мастер практики",  "🛠"),
    (1000, "1000 ответов — легенда",         "🌟"),
]

def ensure_user(uid: str, name_hint="Без имени") -> dict:
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
        "total_answers": 0,
        "achievements": [],  # список ключей "streak:7" или "done:100"
        "nejm": {"queue": [], "answered": 0, "current": None},
        "practicum": {"index": 0},
    })
    # смена дня
    if u.get("last_day") != today_str():
        u["done_today"] = 0
        u["last_day"] = today_str()
    # страховки
    u.setdefault("total_answers", 0)
    u.setdefault("achievements", [])
    u.setdefault("nejm", {"queue": [], "answered": 0, "current": None})
    u.setdefault("practicum", {"index": 0})
    u.setdefault("topics", {})
    return u

def update_interval(card: dict, correct: bool) -> dict:
    if correct:
        card["interval"] = min(max(1, card.get("interval", 1)) * 2, 60)
        next_day = datetime.now() + timedelta(days=card["interval"])
    else:
        card["interval"] = 1
        next_day = datetime.now() + timedelta(days=1)
    card["next_review"] = next_day.strftime(DATE_FMT)
    return card

def check_and_award_achievements(uid: str) -> List[str]:
    """Возвращает список новых описаний наград."""
    u = progress[uid]
    got = set(u.get("achievements", []))
    new_msgs = []

    # Streak
    s = int(u.get("streak", 0))
    for days, title, emoji in ACHIEVEMENTS_STREAK:
        key = f"streak:{days}"
        if s >= days and key not in got:
            u["achievements"].append(key)
            new_msgs.append(f"{emoji} Достижение: «{title}» — серия {days}+ дней!")

    # Done total
    d = int(u.get("total_answers", 0))
    for n, title, emoji in ACHIEVEMENTS_DONE:
        key = f"done:{n}"
        if d >= n and key not in got:
            u["achievements"].append(key)
            new_msgs.append(f"{emoji} Достижение: «{title}» — всего ответов {n}+!")
    return new_msgs

# ======================
# ЛОГИКА ВОПРОСОВ
# ======================
async def send_question(chat_id: int, topic_filter: Optional[str] = None):
    uid = str(chat_id)
    u = ensure_user(uid)
    cards = u.get("cards", {})

    # сперва — due
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

    # иначе — новый
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
    header = f"🧠 {topic}\n\n{q['question']}\n\n"
    options = "\n".join(f"{i+1}) {opt}" for i, opt in enumerate(q["options"]))
    text = header + options

    # картинки (если есть)
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

    kb = types.InlineKeyboardMarkup(row_width=3)
    for i in range(len(q["options"])):
        kb.insert(types.InlineKeyboardButton(str(i + 1), callback_data=f"a:{qid}:{i+1}"))
    kb.add(types.InlineKeyboardButton("⏭ Далее", callback_data="next"))

    for part in split_text(text, 3500) or [text]:
        await bot.send_message(chat_id, part, reply_markup=kb if part == (split_text(text, 3500) or [text])[0] else None)

# ======================
# NEJM
# ======================
def ensure_nejm_queue(state: dict) -> List[int]:
    if not nejm_cases:
        return []
    q = state.get("queue") or []
    if not q:
        q = [int(x["id"]) for x in nejm_cases if "id" in x]
        random.shuffle(q)
        state["queue"] = q
    return q

def get_nejm_case(case_id: int) -> Optional[dict]:
    for case in nejm_cases:
        if int(case.get("id", -1)) == int(case_id):
            return case
    return None

async def send_nejm_case(chat_id: int, *, notify_reset: bool = False):
    uid = str(chat_id)
    u = ensure_user(uid)
    state = u.setdefault("nejm", {"queue": [], "answered": 0, "current": None})
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
    body = f"{header}\n\n{case['question']}\n\n" + "\n".join(
        f"{i+1}) {opt}" for i, opt in enumerate(case.get("options", []))
    )

    # изображения
    media_entries = gather_question_images(case)
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
            print(f"⚠️ Не удалось отправить изображение для кейса {case_id}: {src} — {exc}")

    kb = types.InlineKeyboardMarkup(row_width=2)
    for i in range(len(case.get("options", []))):
        kb.insert(types.InlineKeyboardButton(str(i + 1), callback_data=f"nejm:answer:{case_id}:{i+1}"))

    for part in split_text(body, 3500) or [body]:
        await bot.send_message(chat_id, part, reply_markup=kb if part == (split_text(body, 3500) or [body])[0] else None)

    if notify_reset:
        await bot.send_message(chat_id, "Ты прошёл все кейсы — последовательность обновлена, можно продолжать! ✅")

    save_progress(progress)

# ======================
# PRACTICUM
# ======================
async def send_practicum_card(chat_id: int, direction: str = "stay", message: Optional[types.Message] = None):
    uid = str(chat_id)
    u = ensure_user(uid)
    state = u.setdefault("practicum", {"index": 0})
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
    text = f"{title}\n\n{body}\n\n📚 Карточка {idx + 1} из {total}".strip()

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="practicum:prev"),
           types.InlineKeyboardButton("⏭ Далее", callback_data="practicum:next"))

    if message:
        await message.edit_text(text, reply_markup=kb)
    else:
        await bot.send_message(chat_id, text, reply_markup=kb)

    save_progress(progress)

# ======================
# ХЕНДЛЕРЫ
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
        "💡 Ошибки — завтра; верные — через 2, 4, 8 и т.д. дней.\n\n"
        f"📚 Всего вопросов: {TOTAL_QUESTIONS}.\n"
        f"🩺 NEJM кейсов: {TOTAL_NEJM} • 🛠 Practicum: {TOTAL_PRACTICUM}\n\n"
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
        "/achievements — твои достижения\n"
        "/top_done — топ по количеству ответов\n"
        "/top_streak — топ по серии\n"
        "/nejm — клинические кейсы (с изображениями)\n"
        "/practicum — карточки практикума\n"
        "/reset_topic — сброс прогресса по теме\n"
        "/reset — полный сброс\n"
        "/users — количество пользователей (админ)"
    )

@dp.message_handler(commands=["goal"])
async def set_goal(message: types.Message):
    uid = str(message.chat.id)
    u = ensure_user(uid)
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
    total_cards = len(u.get("cards", {}))
    due = sum(1 for meta in u.get("cards", {}).values() if is_due(meta.get("next_review")))
    goal = u.get("goal_per_day", 10)
    done = u.get("done_today", 0)
    streak = u.get("streak", 0)
    total_correct = sum(t["correct"] for t in u.get("topics", {}).values())
    total_answers = sum(t["total"] for t in u.get("topics", {}).values())
    u["total_answers"] = max(u.get("total_answers", 0), total_answers)
    acc = round(100 * total_correct / total_answers) if total_answers else 0
    msg = (
        f"🎯 Цель: {goal}/день\n"
        f"📊 Сегодня: {done}/{goal}\n"
        f"🔥 Серия: {streak} дней\n"
        f"📘 Выученных карточек: {total_cards}\n"
        f"📅 К повтору: {due}\n"
        f"💯 Точность: {acc}%\n"
        f"🧮 Всего ответов: {u['total_answers']}"
    )
    await message.answer(msg)

@dp.message_handler(commands=["achievements"])
async def achievements(message: types.Message):
    uid = str(message.chat.id)
    u = ensure_user(uid)
    got = set(u.get("achievements", []))
    if not got:
        await message.answer("Пока нет достижений. Держи ритм — и всё прилетит. 💪")
        return
    lines = ["🏆 Твои достижения:"]
    for key in sorted(got):
        kind, val = key.split(":")
        if kind == "streak":
            days = int(val)
            rec = next((x for x in ACHIEVEMENTS_STREAK if x[0] == days), None)
            if rec: lines.append(f"{rec[2]} {rec[1]} — серия {days}+")
        elif kind == "done":
            n = int(val)
            rec = next((x for x in ACHIEVEMENTS_DONE if x[0] == n), None)
            if rec: lines.append(f"{rec[2]} {rec[1]}")
    await message.answer("\n".join(lines))

@dp.message_handler(commands=["top_done"])
async def top_done(message: types.Message):
    # Топ по total_answers
    items = []
    for uid, u in progress.items():
        name = u.get("name", "Без имени")
        total = max(u.get("total_answers", 0), sum(t["total"] for t in u.get("topics", {}).values()))
        items.append((total, name))
    items.sort(reverse=True)
    lines = ["🏅 Топ по количеству ответов:"]
    for i, (total, name) in enumerate(items[:10], 1):
        lines.append(f"{i}. {name} — {total}")
    await message.answer("\n".join(lines))

@dp.message_handler(commands=["top_streak"])
async def top_streak(message: types.Message):
    items = []
    for uid, u in progress.items():
        name = u.get("name", "Без имени")
        items.append((int(u.get("streak", 0)), name))
    items.sort(reverse=True)
    lines = ["🔥 Топ по серии (streak):"]
    for i, (s, name) in enumerate(items[:10], 1):
        lines.append(f"{i}. {name} — {s} дней")
    await message.answer("\n".join(lines))

@dp.message_handler(commands=["nejm"])
async def nejm_command(message: types.Message):
    if not nejm_cases:
        await message.answer("Пока нет кейсов NEJM. Добавь их в nejm_cases.json.")
        return
    intro = (
        "🩺 New England Journal of Medicine — клинические кейсы с изображениями.\n\n"
        f"📦 Доступно кейсов: {TOTAL_NEJM}.\n"
        "Нажми «Начать», чтобы получить кейс."
    )
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⏭ Начать", callback_data="nejm:next"))
    await message.answer(intro, reply_markup=kb)

@dp.message_handler(commands=["practicum"])
async def practicum_command(message: types.Message):
    if not practicum_cards:
        await message.answer("Практикум пока пуст. Добавь карточки в practicum.json.")
        return
    intro = (
        "🛠 Практикум по педиатрии — краткие карточки с советами и алгоритмами.\n\n"
        f"📦 Всего карточек: {TOTAL_PRACTICUM}.\n"
        "Нажми «Открыть», чтобы начать."
    )
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("📖 Открыть", callback_data="practicum:open"))
    await message.answer(intro, reply_markup=kb)

@dp.message_handler(commands=["users"])
async def users_count(message: types.Message):
    if str(message.chat.id) != str(ADMIN_ID):
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
        "last_goal_day": None,
        "last_review": None,
        "goal_per_day": 10,
        "done_today": 0,
        "last_day": today_str(),
        "total_answers": 0,
        "achievements": [],
        "nejm": {"queue": [], "answered": 0, "current": None},
        "practicum": {"index": 0},
    }
    save_progress(progress)
    await message.answer("🔄 Полный сброс. Начинай с /start или /train.")

# ===== CALLBACKS =====
@dp.callback_query_handler(lambda c: c.data == "next")
async def callback_next(call: types.CallbackQuery):
    await call.answer()
    await send_question(call.message.chat.id)

@dp.callback_query_handler(lambda c: c.data.startswith("a:"))
async def callback_answer(call: types.CallbackQuery):
    try:
        _, qid_str, ans_str = call.data.split(":")
        qid = int(qid_str); user_ans = int(ans_str) - 1
    except Exception:
        await call.answer("Ошибка ответа", show_alert=True); return

    q = Q_BY_ID.get(qid)
    if not q:
        await call.answer("Вопрос не найден", show_alert=True); return

    uid = str(call.message.chat.id)
    u = ensure_user(uid)
    cards = u.setdefault("cards", {})
    card = cards.setdefault(qid_str, {"interval": 1, "next_review": today_str()})

    topic = q.get("topic", "Без темы")
    tstats = u.setdefault("topics", {}).setdefault(topic, {"correct": 0, "total": 0})
    tstats["total"] += 1

    correct_index = int(q.get("correct_index", 0))
    is_correct = (user_ans == correct_index)
    if is_correct:
        tstats["correct"] += 1
    update_interval(card, is_correct)

    # дневная цель / streak
    if u.get("last_day") != today_str():
        u["done_today"] = 0
        u["last_day"] = today_str()
    u["done_today"] = u.get("done_today", 0) + 1
    u["total_answers"] = u.get("total_answers", 0) + 1
    goal = u.get("goal_per_day", 10)
    if u["done_today"] >= goal and u.get("last_goal_day") != today_str():
        u["streak"] = u.get("streak", 0) + 1
        u["last_goal_day"] = today_str()

    save_progress(progress)

    # достижение
    new_awards = check_and_award_achievements(uid)
    status = "✅ Верно!" if is_correct else "❌ Неверно."
    correct_opt = q["options"][correct_index]
    reply = f"{status}\n\nПравильный ответ: {correct_opt}"
    exp = q.get("explanation", "").strip()
    if exp:
        reply += f"\n\n{exp}"
    if new_awards:
        reply += "\n\n" + "\n".join(new_awards)

    try:
        await call.message.edit_reply_markup()
    except Exception:
        pass

    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⏭ Далее", callback_data="next"))
    await call.answer("Принято")
    for part in split_text(reply, 3500) or [reply]:
        await call.message.answer(part, reply_markup=kb if part == (split_text(reply, 3500) or [reply])[0] else None)

@dp.callback_query_handler(lambda c: c.data.startswith("nejm:"))
async def callback_nejm(call: types.CallbackQuery):
    parts = call.data.split(":")
    if len(parts) < 2:
        await call.answer(); return
    action = parts[1]
    uid = str(call.message.chat.id)
    u = ensure_user(uid)
    state = u.setdefault("nejm", {"queue": [], "answered": 0, "current": None})

    if action == "next":
        try: await call.message.edit_reply_markup()
        except Exception: pass
        await call.answer()
        await send_nejm_case(call.message.chat.id)
        return

    if action == "answer" and len(parts) == 4:
        try:
            case_id = int(parts[2])
            answer_idx = int(parts[3]) - 1
        except ValueError:
            await call.answer("Ошибка ответа", show_alert=True); return

        case = get_nejm_case(case_id)
        if not case:
            await call.answer("Кейс не найден", show_alert=True); return

        correct_index = int(case.get("correct_index", 0))
        is_correct = (answer_idx == correct_index)
        state["answered"] = state.get("answered", 0) + 1
        save_progress(progress)

        options = case.get("options", [])
        correct_opt = options[correct_index] if 0 <= correct_index < len(options) else "—"
        status = "✅ Верно!" if is_correct else "❌ Неверно."
        reply = f"{status}\n\nПравильный ответ: {correct_opt}"
        exp = case.get("explanation")
        if exp:
            reply += f"\n\n{exp}"

        try: await call.message.edit_reply_markup()
        except Exception: pass

        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⏭ Далее", callback_data="nejm:next"))
        await call.answer("Принято")
        await call.message.answer(reply, reply_markup=kb)
        return

    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("practicum:"))
async def callback_practicum(call: types.CallbackQuery):
    parts = call.data.split(":", maxsplit=1)
    if len(parts) != 2:
        await call.answer(); return
    action = parts[1]
    await call.answer()
    if action == "open":
        await send_practicum_card(call.message.chat.id, direction="stay", message=call.message)
    elif action == "next":
        await send_practicum_card(call.message.chat.id, direction="next", message=call.message)
    elif action == "prev":
        await send_practicum_card(call.message.chat.id, direction="prev", message=call.message)

# ======================
# ЗАПУСК
# ======================
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
        types.BotCommand("achievements", "Мои достижения"),
        types.BotCommand("top_done", "Топ отвеченных"),
        types.BotCommand("top_streak", "Топ по серии"),
        types.BotCommand("reset_topic", "Сброс темы"),
        types.BotCommand("reset", "Полный сброс"),
        types.BotCommand("nejm", "Клинические кейсы NEJM"),
        types.BotCommand("practicum", "Практикум по педиатрии"),
        types.BotCommand("users", "Пользователи (админ)"),
    ]))
    executor.start_polling(dp, skip_updates=True)