
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode
import os

# === Конфигурация ===
TOKEN = os.getenv("TOKEN")
admin_ids_str = os.getenv("ADMIN_CHAT_IDS", "")
ADMIN_CHAT_IDS = [int(x) for x in admin_ids_str.split(",") if x.strip()]
COOLDOWN_SECONDS = 60
DB_PATH = "bot_data.db"

# === Настройка логов ===
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# === Клавиатура ===
menu_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Жалоба"), KeyboardButton(text="Предложение")]
    ],
    resize_keyboard=True
)

# === БД ===
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            feedback_type TEXT,
            content TEXT,
            photo_id TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cooldowns (
            user_id INTEGER PRIMARY KEY,
            next_allowed DATETIME
        )
    """)
    conn.commit()
    conn.close()

def can_send(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT next_allowed FROM cooldowns WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return True, 0
    else:
        next_time = datetime.fromisoformat(row[0])
        now = datetime.utcnow()
        if now >= next_time:
            return True, 0
        else:
            remaining = int((next_time - now).total_seconds())
            return False, remaining

def update_cooldown(user_id):
    next_time = datetime.utcnow() + timedelta(seconds=COOLDOWN_SECONDS)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("REPLACE INTO cooldowns (user_id, next_allowed) VALUES (?, ?)", (user_id, next_time.isoformat()))
    conn.commit()
    conn.close()

def save_message(user_id, feedback_type, content, photo_id=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO messages (user_id, feedback_type, content, photo_id)
        VALUES (?, ?, ?, ?)
    """, (user_id, feedback_type, content, photo_id))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM messages WHERE feedback_type = 'Жалоба'")
    complaints = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM messages WHERE feedback_type = 'Предложение'")
    suggestions = cursor.fetchone()[0]
    conn.close()
    return complaints, suggestions

# === Логика бота ===

user_feedback_type = {}

@dp.message(F.text == "/start")
async def start_handler(message: types.Message):
    await message.answer(
        "Привет! Это анонимный бот для флуда Moonlight. Выберите действие:",
        reply_markup=menu_keyboard
    )

@dp.message(F.text == "/stats")
async def stats_handler(message: types.Message):
    if message.from_user.id in ADMIN_CHAT_IDS:
        c, s = get_stats()
        await message.answer(
    f"📊 <b>Статистика:</b>\n"
    f"Жалоб: {c}\n"
    f"Предложений: {s}"
)
    else:
        await message.answer("⛔ Команда только для админов.")

@dp.message(F.text.in_(["Жалоба", "Предложение"]))
async def type_choice_handler(message: types.Message):
    user_feedback_type[message.from_user.id] = message.text
    await message.answer(f"✍ Напишите вашу {message.text.lower()} (можно приложить фото):")

@dp.message(F.text | F.photo)
async def feedback_handler(message: types.Message):
    user_id = message.from_user.id
    can, wait = can_send(user_id)
    if not can:
        await message.answer(f"⏳ Подождите ещё {wait} секунд перед следующей отправкой.")
        return

    feedback_type = user_feedback_type.get(user_id)
    if not feedback_type:
        await message.answer("Пожалуйста, сначала выберите 'Жалоба' или 'Предложение'.")
        return

    content = message.caption or message.text or "[Без текста]"
    photo_id = message.photo[-1].file_id if message.photo else None

    save_message(user_id, feedback_type, content, photo_id)
    update_cooldown(user_id)

    for admin_id in ADMIN_CHAT_IDS:
        if photo_id:
            await bot.send_photo(
                chat_id=admin_id,
                photo=photo_id,
                caption=f"📬 Новая {feedback_type.lower()}:\n\n{content}"
            )
        else:
            await bot.send_message(
                chat_id=admin_id,
                text=f"📬 Новая {feedback_type.lower()}:\n\n{content}"
            )

    await message.answer("✅ Спасибо! Хотите отправить ещё что-нибудь?", reply_markup=menu_keyboard)
    user_feedback_type.pop(user_id, None)


# === Запуск ===
async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
