iimport asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

# === Конфигурация ===
TOKEN = "7938812822:AAE3hidbaMkycvDStg9JR2Q0F4Bpi7sP574"
ADMIN_CHAT_IDS = [7714767386, 5914528610]
COOLDOWN_SECONDS = 60
DB_PATH = "bot_data.db"

WEBHOOK_PATH = f"/webhook/{TOKEN}"
BASE_WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL")  # В Render надо обязательно задать эту переменную
if not BASE_WEBHOOK_URL:
    raise RuntimeError("RENDER_EXTERNAL_URL environment variable is not set!")
WEBHOOK_URL = f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}"

# === Логгирование ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# === Клавиатура ===
menu_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Жалоба"), KeyboardButton(text="Предложение")]
    ],
    resize_keyboard=True
)

# === Работа с БД ===
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
    next_time = datetime.fromisoformat(row[0])
    now = datetime.utcnow()
    if now >= next_time:
        return True, 0
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
        complaints, suggestions = get_stats()
        await message.answer(
            f"📊 <b>Статистика:</b>\n"
            f"Жалоб: {complaints}\n"
            f"Предложений: {suggestions}"
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
        try:
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
        except Exception as e:
            logger.error(f"Ошибка при отправке админам: {e}")

    await message.answer("✅ Спасибо! Хотите отправить ещё что-нибудь?", reply_markup=menu_keyboard)
    user_feedback_type.pop(user_id, None)

# === Запуск вебхука ===
async def on_startup(app):
    init_db()
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook установлен: {WEBHOOK_URL}")

async def on_shutdown(app):
    await bot.delete_webhook()
    logger.info("Webhook удалён")

async def main():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # Регистрируем обработчик для пути вебхука
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)

    port = int(os.getenv("PORT", 8000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info(f"🚀 Бот запущен на {WEBHOOK_URL} (порт {port})")

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")

