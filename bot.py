# -*- coding: utf-8 -*-
"""
ربات هاپ‌هاپی 🐶 -- نسخه‌ی تک‌فایلی (همه‌چیز اینجاست تا آپلودش راحت باشه)

اجرا:
    python bot.py
(قبلش env var هایی به اسم BOT_TOKEN و BOT_USERNAME ست کن)
"""

import logging
import os
import random
import sqlite3
import time
from contextlib import contextmanager
from datetime import timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ============================================================================
# بخش ۱: تنظیمات و فرمول‌های بازی (قبلاً game_data.py)
# ============================================================================

# ---------- هاپ زدن (کلیک برای پوینت) ----------
CLICK_COOLDOWN_SECONDS = 10 * 60  # هر ۱۰ دقیقه یه بار میشه هاپ زد
CLICK_MIN_POINTS = 40
CLICK_MAX_POINTS = 180

# احتمال اینکه ضمن هاپ زدن، یه استخون هم پیدا بشه (جایزه اضافه)
BONUS_BONE_CHANCE = 0.15

# ---------- پیدا کردن استخون ----------
CATCH_COOLDOWN_SECONDS = 8 * 60  # هر ۸ دقیقه یه بار میشه دنبال استخون گشت

# هر رده (tier) شانس وقوع، بازه وزن (کیلوگرم)، ضریب ارزش و ارزش غذایی داره
BONE_TIERS = [
    {
        "name": "عادی",
        "emoji": "⚪",
        "chance": 55,
        "weight_range": (0.05, 0.6),
        "value_per_kg": 900,
        "food_value_range": (1, 3),
    },
    {
        "name": "نایاب",
        "emoji": "🔵",
        "chance": 27,
        "weight_range": (0.4, 1.2),
        "value_per_kg": 1800,
        "food_value_range": (2, 5),
    },
    {
        "name": "حماسی",
        "emoji": "🟣",
        "chance": 13,
        "weight_range": (0.8, 2.0),
        "value_per_kg": 3200,
        "food_value_range": (3, 7),
    },
    {
        "name": "افسانه‌ای",
        "emoji": "🟡",
        "chance": 5,
        "weight_range": (1.5, 3.5),
        "value_per_kg": 6000,
        "food_value_range": (5, 12),
    },
]


def roll_bone():
    """یه استخون رندوم بر اساس شانس هر رده تولید می‌کنه."""
    roll = random.uniform(0, 100)
    cumulative = 0
    chosen = BONE_TIERS[0]
    for tier in BONE_TIERS:
        cumulative += tier["chance"]
        if roll <= cumulative:
            chosen = tier
            break

    weight = round(random.uniform(*chosen["weight_range"]), 2)
    value = int(weight * chosen["value_per_kg"])
    food_value = random.randint(*chosen["food_value_range"])

    return {
        "tier": chosen["name"],
        "emoji": chosen["emoji"],
        "weight": weight,
        "value": value,
        "food_value": food_value,
    }


# ---------- کارخونه هاپ‌هاپی ----------
FACTORY_BASE_CAPACITY = 40000  # ظرفیت پایه انبار
FACTORY_CAPACITY_PER_LEVEL = 4000  # به ازای هر لول کارخونه چقدر ظرفیت اضافه میشه

WORKER_BASE_COUNT = 4
WORKER_MAX_COUNT_BASE = 12  # حداکثر تعداد کارگر در سطح ۱
WORKER_MAX_PER_LEVEL = 1  # هر لول کارگر، ۱ نفر ظرفیت کارگر بیشتر

DEVICE_BASE_PRODUCTION_SECONDS = 10 * 60  # زمان پایه هر تولید
DEVICE_TIME_REDUCTION_PER_LEVEL = 20  # هر لول دستگاه، این‌قدر ثانیه از زمان کم میشه
DEVICE_MIN_PRODUCTION_SECONDS = 60

XP_PER_PRODUCTION = 200
XP_PER_LEVEL_BASE = 5000
XP_PER_LEVEL_GROWTH = 1.35  # هر سطح، این‌قدر برابر سخت‌تر میشه

UPGRADE_BASE_COST = 5000
UPGRADE_COST_GROWTH = 1.6

# مقدار محصول (استخون هاپ‌هاپی) که در هر بار تولید، به انبار اضافه میشه
def production_output(workers_count, device_level):
    base = workers_count * random.randint(80, 150)
    bonus = device_level * random.randint(20, 60)
    return base + bonus


def factory_capacity(level):
    return FACTORY_BASE_CAPACITY + (level - 1) * FACTORY_CAPACITY_PER_LEVEL


def worker_max_count(level):
    return WORKER_MAX_COUNT_BASE + (level - 1) * WORKER_MAX_PER_LEVEL


def device_production_seconds(level):
    seconds = DEVICE_BASE_PRODUCTION_SECONDS - (level - 1) * DEVICE_TIME_REDUCTION_PER_LEVEL
    return max(seconds, DEVICE_MIN_PRODUCTION_SECONDS)


def xp_needed_for_level(level):
    return int(XP_PER_LEVEL_BASE * (XP_PER_LEVEL_GROWTH ** (level - 1)))


def upgrade_cost(current_level):
    return int(UPGRADE_BASE_COST * (UPGRADE_COST_GROWTH ** (current_level - 1)))


# ============================================================================
# بخش ۲: لایه‌ی دیتابیس SQLite (قبلاً database.py)
# ============================================================================

DB_PATH = "hophop.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_cursor():
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                username TEXT,
                points INTEGER NOT NULL DEFAULT 0,
                last_click REAL DEFAULT 0,
                last_catch REAL DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS factories (
                chat_id INTEGER PRIMARY KEY,
                manager_id INTEGER,
                manager_name TEXT,
                level INTEGER NOT NULL DEFAULT 1,
                xp INTEGER NOT NULL DEFAULT 0,
                warehouse_used INTEGER NOT NULL DEFAULT 0,
                workers_count INTEGER NOT NULL DEFAULT 4,
                workers_level INTEGER NOT NULL DEFAULT 1,
                device_level INTEGER NOT NULL DEFAULT 1,
                production_active INTEGER NOT NULL DEFAULT 0,
                production_end REAL DEFAULT 0
            )
        """)


# ---------------- users ----------------

def get_or_create_user(user_id, chat_id, username):
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM users WHERE user_id=? AND chat_id=?",
            (user_id, chat_id),
        )
        row = cur.fetchone()
        if row:
            if username and row["username"] != username:
                cur.execute(
                    "UPDATE users SET username=? WHERE user_id=? AND chat_id=?",
                    (username, user_id, chat_id),
                )
            return dict(row)

        cur.execute(
            "INSERT INTO users (user_id, chat_id, username) VALUES (?, ?, ?)",
            (user_id, chat_id, username),
        )
        return {
            "user_id": user_id,
            "chat_id": chat_id,
            "username": username,
            "points": 0,
            "last_click": 0,
            "last_catch": 0,
        }


def update_user_click(user_id, chat_id, points_earned):
    now = time.time()
    with db_cursor() as cur:
        cur.execute(
            "UPDATE users SET points = points + ?, last_click = ? "
            "WHERE user_id=? AND chat_id=?",
            (points_earned, now, user_id, chat_id),
        )


def update_user_catch(user_id, chat_id):
    now = time.time()
    with db_cursor() as cur:
        cur.execute(
            "UPDATE users SET last_catch = ? WHERE user_id=? AND chat_id=?",
            (now, user_id, chat_id),
        )


def get_leaderboard(chat_id, limit=10):
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM users WHERE chat_id=? ORDER BY points DESC LIMIT ?",
            (chat_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


# ---------------- factories ----------------

def get_factory(chat_id):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM factories WHERE chat_id=?", (chat_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def create_factory(chat_id, manager_id, manager_name):
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO factories (chat_id, manager_id, manager_name) "
            "VALUES (?, ?, ?)",
            (chat_id, manager_id, manager_name),
        )
    return get_factory(chat_id)


def update_factory(chat_id, **fields):
    if not fields:
        return
    keys = ", ".join(f"{k}=?" for k in fields.keys())
    values = list(fields.values())
    values.append(chat_id)
    with db_cursor() as cur:
        cur.execute(f"UPDATE factories SET {keys} WHERE chat_id=?", values)


def add_to_warehouse(chat_id, amount):
    factory = get_factory(chat_id)
    if not factory:
        return None
    capacity = factory["warehouse_used"] + amount
    with db_cursor() as cur:
        cur.execute(
            "UPDATE factories SET warehouse_used = warehouse_used + ? WHERE chat_id=?",
            (amount, chat_id),
        )
    return get_factory(chat_id)


# ============================================================================
# بخش ۳: ربات و هندلرهای تلگرام (قبلاً bot.py)
# ============================================================================

# -*- coding: utf-8 -*-
"""
ربات هاپ‌هاپی 🐶
یه ربات سرگرمی برای گروه‌های تلگرام، الهام گرفته از سبک بازی‌های
"کلیک کن و پوینت بگیر + کارخونه بساز"، ولی با تم سگ.

اجرا:
    python bot.py
(قبلش env var به اسم BOT_TOKEN ست کن یا مستقیم پایین جایگزین کن)
"""

import logging
import os
import random
import time
from datetime import timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "HopHopGameBot")

HOP_TRIGGERS = {"هاپ", "هاپ هاپ", "هاپ‌هاپ"}
BONE_TRIGGERS = {"استخون", "استخوان"}
FACTORY_TRIGGERS = {"کارخونه هاپ‌هاپی", "کارخونه هاپ هاپی", "کارخونه"}


# ---------------------------------------------------------------------------
# ابزارهای کمکی
# ---------------------------------------------------------------------------

def fmt_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    td = timedelta(seconds=seconds)
    hours, remainder = divmod(td.seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if td.days > 0:
        hours += td.days * 24
    parts = []
    if hours:
        parts.append(f"{hours} ساعت")
    if minutes:
        parts.append(f"{minutes} دقیقه")
    if secs and not hours:
        parts.append(f"{secs} ثانیه")
    return " و ".join(parts) if parts else "چند لحظه"


def display_name(user) -> str:
    return user.first_name or user.username or "کاربر"


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        text = (
            "🐶 *ربات سرگرمی هاپ‌هاپی*\n\n"
            "🐾 یه توله بامزه برای گروهت...\n"
            "کافیه توی گروه *هاپ* بزنی تا هاپ پوینت بگیری 🐕\n\n"
            "🏆 هاپ پوینت جمع کن و با بقیه رقابت کن\n"
            "لیدربرد هاپ‌هاپی رو فتح کن و پادشاه توله‌ها شو\n\n"
            "چرا هاپ‌هاپی؟ ✨\n"
            "⚡️ پاسخگویی فوق‌العاده سریع\n"
            "🐕‍🦺 عملکرد پایدار و بدون باگ\n"
            "📦 آپدیت‌های هفتگی و قابلیت‌های جدید\n"
            "👥 کامیونیتی فعال و پرانرژی\n"
            "🚨 پشتیبانی ۲۴ ساعته\n"
            "💎 کاملاً رایگان برای همه\n\n"
            "🎉 آماده‌ای تا یه توله ناز بشی؟"
        )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                "➕ افزودن من به گروه",
                url=f"https://t.me/{BOT_USERNAME}?startgroup=true",
            )]]
        )
        await update.message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
        )
    else:
        await update.message.reply_text(
            "🐶 هاپ‌هاپی فعال شد!\nکافیه بنویسی *هاپ* تا هاپ پوینت بگیری، "
            "یا *استخون* بنویسی تا دنبال استخون بگردی.\n"
            "برای ساخت کارخونه هم بنویس *کارخونه هاپ‌هاپی*.",
            parse_mode=ParseMode.MARKDOWN,
        )


# ---------------------------------------------------------------------------
# هاپ زدن (کلیک برای پوینت)
# ---------------------------------------------------------------------------

async def hop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == ChatType.PRIVATE:
        await update.message.reply_text("این قابلیت فقط توی گروه‌ها کار می‌کنه 🐶")
        return

    user = update.effective_user
    chat_id = update.effective_chat.id
    row = get_or_create_user(user.id, chat_id, display_name(user))

    now = time.time()
    remaining = CLICK_COOLDOWN_SECONDS - (now - row["last_click"])
    if remaining > 0:
        await update.message.reply_text(
            f"⏳ الان نمی‌تونی هاپ کنی.\n"
            f"بعد از {fmt_seconds(remaining)} می‌تونی دوباره هاپ کنی 🐾"
        )
        return

    points = random.randint(CLICK_MIN_POINTS, CLICK_MAX_POINTS)
    update_user_click(user.id, chat_id, points)
    new_total = row["points"] + points

    lines = [
        f"🐾 {points} هاپ پوینت گرفتی",
        f"🪙 هاپ پوینت هات: {new_total:,}",
    ]

    # شانس پیدا کردن استخون به‌عنوان جایزه اضافه
    if random.random() < BONUS_BONE_CHANCE:
        factory = get_factory(chat_id)
        bone = roll_bone()
        if factory:
            capacity = factory_capacity(factory["level"])
            space_left = capacity - factory["warehouse_used"]
            if space_left > 0:
                added = min(bone["weight"] * 1000, space_left)  # simplistic units
                add_to_warehouse(chat_id, int(added))
                lines.append("")
                lines.append(f"🦴 در همین حین یه استخون {bone['emoji']} {bone['tier']} هم پیدا کردی و رفت انبار کارخونه!")

    lines.append(f"⏳ بعد از {fmt_seconds(CLICK_COOLDOWN_SECONDS)} می‌تونی دوباره هاپ کنی")

    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# پیدا کردن استخون
# ---------------------------------------------------------------------------

async def bone_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == ChatType.PRIVATE:
        await update.message.reply_text("این قابلیت فقط توی گروه‌ها کار می‌کنه 🐶")
        return

    user = update.effective_user
    chat_id = update.effective_chat.id
    row = get_or_create_user(user.id, chat_id, display_name(user))

    now = time.time()
    remaining = CATCH_COOLDOWN_SECONDS - (now - row["last_catch"])
    if remaining > 0:
        await update.message.reply_text(
            f"⏳ هنوز زوده.\nبعد از {fmt_seconds(remaining)} می‌تونی دوباره دنبال استخون بگردی 🦴"
        )
        return

    update_user_catch(user.id, chat_id)
    bone = roll_bone()

    text = [
        "🐕‍🦺 شما با موفقیت 🦴 گرفتید...",
        "",
        f"⭐️ سطح: {bone['emoji']} {bone['tier']}",
        f"⚖️ وزن: {bone['weight']} کیلو",
        f"🪙 ارزش: {bone['value']:,}",
        "",
        f"🍖 ارزش غذایی: {bone['food_value']}",
    ]

    factory = get_factory(chat_id)
    if factory:
        capacity = factory_capacity(factory["level"])
        space_left = capacity - factory["warehouse_used"]
        add_amount = int(bone["weight"] * 1000)
        if space_left <= 0:
            text.append("\n🚫 انبار کارخونه پره! این استخون به انبار اضافه نشد.")
        else:
            add_amount = min(add_amount, space_left)
            add_to_warehouse(chat_id, add_amount)
            text.append(f"\n📦 این استخون به انبار کارخونه اضافه شد ({add_amount:,} واحد)")
    else:
        text.append("\n🐾 این گروه هنوز کارخونه نداره؛ بنویس «کارخونه هاپ‌هاپی» تا بسازیش")

    await update.message.reply_text("\n".join(text))


# ---------------------------------------------------------------------------
# کارخونه هاپ‌هاپی
# ---------------------------------------------------------------------------

def factory_panel_text(factory) -> str:
    level = factory["level"]
    capacity = factory_capacity(level)
    worker_max = worker_max_count(level)
    device_seconds = device_production_seconds(factory["device_level"])
    xp_needed = xp_needed_for_level(level)

    lines = [
        "🏭 کارخونه هاپ‌هاپی 🐶",
        "",
        f"💼 مدیر کارخونه: {factory['manager_name']}",
        "",
        "🧳 انبار کارخونه",
        f"└ ✨ ظرفیت انبار: {factory['warehouse_used']:,} / {capacity:,}",
        "",
        "🐕 کارگران کارخونه",
        f"└ 😺 تعداد کارگران: {factory['workers_count']} / {worker_max}",
        f"└ ⭐️ سطح: {factory['workers_level']}",
        "",
        "🖨 دستگاه‌های تولید",
        f"└ ⏱ زمان تولید محصول: {fmt_seconds(device_seconds)}",
        f"└ ⭐️ سطح: {factory['device_level']}",
        "",
        f"🌸 سطح کارخونه: {level}",
        f"➕ {factory['xp']:,}xp / {xp_needed:,}xp",
    ]

    if factory["production_active"]:
        remaining = factory["production_end"] - time.time()
        if remaining > 0:
            lines.append("")
            lines.append(f"⚙️ تولید در حال انجامه، {fmt_seconds(remaining)} مونده")
        else:
            lines.append("")
            lines.append("✅ تولید تموم شده! برای برداشت محصول دکمه «درحال تولید» رو بزن")

    return "\n".join(lines)


def factory_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⚙️ درحال تولید", callback_data="factory:produce")],
            [
                InlineKeyboardButton("🧳 انبار", callback_data="factory:warehouse"),
                InlineKeyboardButton("🐕 کارگران", callback_data="factory:workers"),
            ],
            [InlineKeyboardButton("🖨 دستگاه‌های تولید", callback_data="factory:devices")],
        ]
    )


async def factory_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == ChatType.PRIVATE:
        await update.message.reply_text("کارخونه فقط توی گروه‌ها ساخته می‌شه 🐶")
        return

    chat_id = update.effective_chat.id
    user = update.effective_user
    factory = get_factory(chat_id)

    if not factory:
        factory = create_factory(chat_id, user.id, display_name(user))
        await update.message.reply_text(
            "🏭 کارخونه هاپ‌هاپی ساخته شد! 🐶\n"
            f"💼 مدیر کارخونه: {display_name(user)}\n\n"
            "حالا می‌تونی از دکمه‌های زیر مدیریتش کنی."
        )

    await update.message.reply_text(
        factory_panel_text(factory), reply_markup=factory_keyboard()
    )


async def factory_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user
    action = query.data.split(":", 1)[1]

    factory = get_factory(chat_id)
    if not factory:
        await query.answer("این گروه کارخونه نداره!", show_alert=True)
        return

    if action == "produce":
        await handle_produce(query, factory)
    elif action == "warehouse":
        capacity = factory_capacity(factory["level"])
        await query.answer(
            f"انبار: {factory['warehouse_used']:,} / {capacity:,} واحد",
            show_alert=True,
        )
    elif action == "workers":
        worker_max = worker_max_count(factory["level"])
        await query.answer(
            f"کارگران: {factory['workers_count']} / {worker_max} — سطح {factory['workers_level']}",
            show_alert=True,
        )
    elif action == "devices":
        seconds = device_production_seconds(factory["device_level"])
        await query.answer(
            f"دستگاه تولید — سطح {factory['device_level']}\nزمان هر تولید: {fmt_seconds(seconds)}",
            show_alert=True,
        )

    # پنل رو رفرش کن (مقدارها ممکنه عوض شده باشن)
    factory = get_factory(chat_id)
    try:
        await query.edit_message_text(
            factory_panel_text(factory), reply_markup=factory_keyboard()
        )
    except Exception:
        pass  # اگه متن فرقی نکرده بود، تلگرام ارور می‌ده که مهم نیست


async def handle_produce(query, factory):
    chat_id = factory["chat_id"]
    now = time.time()

    if factory["production_active"]:
        if factory["production_end"] > now:
            remaining = factory["production_end"] - now
            await query.answer(
                f"تولید در حال انجامه، {fmt_seconds(remaining)} مونده",
                show_alert=True,
            )
            return
        else:
            # تولید تموم شده - محصول رو برداشت کن
            output = production_output(factory["workers_count"], factory["device_level"])
            capacity = factory_capacity(factory["level"])
            space_left = capacity - factory["warehouse_used"]
            added = min(output, max(space_left, 0))

            new_xp = factory["xp"] + XP_PER_PRODUCTION
            new_level = factory["level"]
            xp_needed = xp_needed_for_level(new_level)
            while new_xp >= xp_needed:
                new_xp -= xp_needed
                new_level += 1
                xp_needed = xp_needed_for_level(new_level)

            update_factory(
                chat_id,
                production_active=0,
                production_end=0,
                warehouse_used=factory["warehouse_used"] + added,
                xp=new_xp,
                level=new_level,
            )
            msg = f"📦 تولید تموم شد! {added:,} واحد محصول به انبار اضافه شد."
            if new_level > factory["level"]:
                msg += f"\n🎉 کارخونه به سطح {new_level} رسید!"
            await query.answer(msg, show_alert=True)
            return

    # تولید جدید رو شروع کن
    seconds = device_production_seconds(factory["device_level"])
    update_factory(
        chat_id, production_active=1, production_end=now + seconds
    )
    await query.answer(
        f"⚙️ تولید شروع شد! بعد از {fmt_seconds(seconds)} برگرد و محصول رو برداشت کن.",
        show_alert=True,
    )


# ---------------------------------------------------------------------------
# لیدربرد
# ---------------------------------------------------------------------------

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == ChatType.PRIVATE:
        await update.message.reply_text("لیدربرد فقط توی گروه‌ها قابل مشاهده‌ست 🐶")
        return

    chat_id = update.effective_chat.id
    rows = get_leaderboard(chat_id)
    if not rows:
        await update.message.reply_text("هنوز کسی هاپ نزده! اولین نفر باش 🐾")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 لیدربرد هاپ‌هاپی\n"]
    for i, row in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i + 1}."
        name = row["username"] or "کاربر"
        lines.append(f"{medal} {name} — {row['points']:,} هاپ پوینت")

    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# راه‌اندازی
# ---------------------------------------------------------------------------

def build_text_filter(triggers):
    return filters.TEXT & filters.Regex(
        "^(" + "|".join(t.replace(" ", r"\s") for t in triggers) + ")$"
    )


def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("leaderboard", leaderboard_command))
    app.add_handler(CommandHandler("لیدربرد", leaderboard_command))

    app.add_handler(MessageHandler(build_text_filter(HOP_TRIGGERS), hop_handler))
    app.add_handler(MessageHandler(build_text_filter(BONE_TRIGGERS), bone_handler))
    app.add_handler(MessageHandler(build_text_filter(FACTORY_TRIGGERS), factory_handler))

    app.add_handler(CallbackQueryHandler(factory_callback, pattern=r"^factory:"))

    logger.info("ربات هاپ‌هاپی روشن شد 🐶")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

