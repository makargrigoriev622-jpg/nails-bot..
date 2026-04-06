import asyncio
import sqlite3
import os

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_CHAT_ID"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== CONFIG =====
MASTER_NAME = "Мастер 💅"
PORTFOLIO_URL = "https://ru.pinterest.com/crystalwithluv/_created/"
INSTAGRAM_URL = "https://instagram.com/"
TELEGRAM_CHANNEL_URL = "https://t.me/nails_channel_demo"
MASTER_USERNAME = "qam_map"

# ===== DATABASE =====
conn = sqlite3.connect("db.sqlite")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    name TEXT,
    phone TEXT,
    date TEXT,
    time TEXT
)
""")
conn.commit()

# ===== MENU =====
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Записаться", callback_data="book")],
        [InlineKeyboardButton(text="💸 Прайс", callback_data="price")],
        [InlineKeyboardButton(text="📸 Портфолио", url=PORTFOLIO_URL)],
        [InlineKeyboardButton(text="📷 Instagram", url=INSTAGRAM_URL)],
        [InlineKeyboardButton(text="📢 Telegram канал", url=TELEGRAM_CHANNEL_URL)],
        [InlineKeyboardButton(text="💬 Написать мастеру", url=f"https://t.me/{MASTER_USERNAME}")]
    ])

@dp.message(Command("start"))
async def start(msg: types.Message):
    await msg.answer(
        f"Привет, красотка 💖\n\nДобро пожаловать к {MASTER_NAME}\nВыбирай, что хочешь сделать:",
        reply_markup=main_menu()
    )

# ===== PRICE =====
@dp.callback_query(lambda c: c.data == "price")
async def price(call: types.CallbackQuery):
    await call.message.answer(
        "<b>Прайс:</b>\nФренч — 1000₽\nКвадрат — 500₽",
        parse_mode="HTML"
    )

# ===== BOOKING =====
@dp.callback_query(lambda c: c.data == "book")
async def choose_date(call: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="10 апреля", callback_data="date_10")],
        [InlineKeyboardButton(text="11 апреля", callback_data="date_11")]
    ])
    await call.message.answer("Выбери дату 📅", reply_markup=kb)

@dp.callback_query(lambda c: "date_" in c.data)
async def choose_time(call: types.CallbackQuery):
    date = call.data.split("_")[1]

    # проверяем занятые слоты
    cursor.execute("SELECT time FROM bookings WHERE date = ?", (date,))
    booked = [row[0] for row in cursor.fetchall()]

    all_times = ["12:00", "14:00", "16:00"]

    kb = []
    for t in all_times:
        if t not in booked:
            kb.append([InlineKeyboardButton(text=t, callback_data=f"time_{date}_{t}")])

    if not kb:
        await call.message.answer("Все окошки заняты 😢")
        return

    await call.message.answer("Выбери время ⏰", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

user_data = {}

@dp.callback_query(lambda c: "time_" in c.data)
async def get_name(call: types.CallbackQuery):
    _, date, time = call.data.split("_")
    user_data[call.from_user.id] = {"date": date, "time": time}
    await call.message.answer("Как тебя зовут? 😊")

@dp.message()
async def get_phone(msg: types.Message):
    user_id = msg.from_user.id

    if user_id in user_data and "name" not in user_data[user_id]:
        user_data[user_id]["name"] = msg.text
        await msg.answer("Оставь номер телефона 📲")
    elif user_id in user_data:
        user_data[user_id]["phone"] = msg.text

        data = user_data[user_id]

        # проверка на занятость
        cursor.execute(
            "SELECT * FROM bookings WHERE date=? AND time=?",
            (data["date"], data["time"])
        )
        if cursor.fetchone():
            await msg.answer("Это время уже занято 😢")
            return

        cursor.execute(
            "INSERT INTO bookings (user_id, name, phone, date, time) VALUES (?, ?, ?, ?, ?)",
            (user_id, data["name"], data["phone"], data["date"], data["time"])
        )
        conn.commit()

        await msg.answer(f"Ты записана 💖\n{data['date']} в {data['time']}")

        await bot.send_message(
            ADMIN_ID,
            f"Новая запись:\n{data['name']} {data['phone']}\n{data['date']} {data['time']}"
        )

        user_data.pop(user_id)

# ===== START =====
async def main():
    print("Бот запускается...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
