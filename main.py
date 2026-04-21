import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from contextlib import closing

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ============== CONFIG ==============
BOT_TOKEN = "8798643033:AAG-SzHEn_ZnwZy5EQcbL7yBM5XE5kBQWKs"
ADMIN_CHAT_ID = 8452106171

DB_PATH = "db.sqlite"
DEFAULT_SLOTS = ["12:00", "14:00", "16:00"]

PORTFOLIO_URL = "https://t.me/nails_channel_demo"
INSTAGRAM_URL = "https://instagram.com/"
TG_CHANNEL_URL = "https://t.me/nails_channel_demo"
MASTER_URL = "https://t.me/qam_map"

PRICE_TEXT = (
    "💸 <b>Прайс</b>\n\n"
    "💅 Маникюр классический — <b>1500₽</b>\n"
    "💎 Маникюр + покрытие — <b>2500₽</b>\n"
    "🎨 Дизайн — <b>от 100₽ за ноготь</b>\n"
    "🦶 Педикюр — <b>3000₽</b>\n"
)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


# ============== DB ==============
def db_init() -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                phone TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schedule (
                date TEXT PRIMARY KEY,
                slots TEXT NOT NULL
            )
            """
        )
        conn.commit()


def db_query(sql: str, params: tuple = (), fetch: str | None = None):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(sql, params)
        result = None
        if fetch == "one":
            result = cur.fetchone()
        elif fetch == "all":
            result = cur.fetchall()
        conn.commit()
        return result


# ============== STATES ==============
class BookingStates(StatesGroup):
    choosing_date = State()
    choosing_time = State()
    entering_name = State()
    entering_phone = State()


# ============== HELPERS ==============
def main_menu() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="📅 Записаться", callback_data="book")],
        [InlineKeyboardButton(text="💸 Прайс", callback_data="price")],
        [InlineKeyboardButton(text="📸 Портфолио", url=PORTFOLIO_URL)],
        [InlineKeyboardButton(text="📷 Instagram", url=INSTAGRAM_URL)],
        [InlineKeyboardButton(text="📢 Telegram канал", url=TG_CHANNEL_URL)],
        [InlineKeyboardButton(text="💬 Написать мастеру", url=MASTER_URL)],
        [InlineKeyboardButton(text="❌ Отменить запись", callback_data="cancel_booking")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_slots_for_date(date: str) -> list[str]:
    row = db_query("SELECT slots FROM schedule WHERE date = ?", (date,), fetch="one")
    base_slots = row[0].split(",") if row else DEFAULT_SLOTS

    taken = db_query(
        "SELECT time FROM bookings WHERE date = ?", (date,), fetch="all"
    ) or []
    taken_set = {t[0] for t in taken}

    return [s for s in base_slots if s not in taken_set]


def dates_keyboard() -> InlineKeyboardMarkup:
    today = datetime.now().date()
    rows = []
    row = []
    for i in range(30):
        d = today + timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        label = d.strftime("%d.%m")
        row.append(InlineKeyboardButton(text=label, callback_data=f"date_{d_str}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def times_keyboard(date: str, slots: list[str]) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for s in slots:
        row.append(
            InlineKeyboardButton(text=s, callback_data=f"time_{date}_{s}")
        )
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="book")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def has_active_booking(user_id: int) -> bool:
    row = db_query(
        "SELECT id FROM bookings WHERE user_id = ?", (user_id,), fetch="one"
    )
    return row is not None


# ============== ADMIN COMMANDS (must be BEFORE FSM) ==============
@dp.message(Command("add_day"))
async def cmd_add_day(msg: Message, state: FSMContext) -> None:
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    await state.clear()
    parts = (msg.text or "").split()
    if len(parts) < 3:
        await msg.answer("Использование: /add_day YYYY-MM-DD 10:00 14:00 18:00")
        return
    date = parts[1]
    times = parts[2:]
    try:
        datetime.strptime(date, "%Y-%m-%d")
        for t in times:
            datetime.strptime(t, "%H:%M")
    except ValueError:
        await msg.answer("Неверный формат даты или времени.")
        return
    slots_str = ",".join(times)
    db_query(
        "INSERT INTO schedule (date, slots) VALUES (?, ?) "
        "ON CONFLICT(date) DO UPDATE SET slots = excluded.slots",
        (date, slots_str),
    )
    await msg.answer(f"✅ День обновлён: <b>{date}</b>\nСлоты: <b>{slots_str}</b>")


@dp.message(Command("remove_day"))
async def cmd_remove_day(msg: Message, state: FSMContext) -> None:
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    await state.clear()
    parts = (msg.text or "").split()
    if len(parts) != 2:
        await msg.answer("Использование: /remove_day YYYY-MM-DD")
        return
    date = parts[1]
    db_query("DELETE FROM schedule WHERE date = ?", (date,))
    await msg.answer(f"🗑 День удалён: <b>{date}</b>")


@dp.message(Command("schedule"))
async def cmd_schedule(msg: Message, state: FSMContext) -> None:
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    await state.clear()
    rows = db_query("SELECT date, slots FROM schedule ORDER BY date", fetch="all") or []
    if not rows:
        await msg.answer("📭 Кастомных дней нет.")
        return
    text = "📋 <b>Кастомное расписание:</b>\n\n"
    for date, slots in rows:
        text += f"📅 <b>{date}</b> — {slots}\n"
    await msg.answer(text)


# ============== START ==============
@dp.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer(
        "💅 <b>Добро пожаловать!</b>\n\nВыбери действие из меню ниже:",
        reply_markup=main_menu(),
    )


# ============== CALLBACKS ==============
@dp.callback_query(F.data == "back_main")
async def cb_back_main(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text(
        "💅 <b>Главное меню</b>\n\nВыбери действие:",
        reply_markup=main_menu(),
    )
    await cb.answer()


@dp.callback_query(F.data == "price")
async def cb_price(cb: CallbackQuery) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")]]
    )
    await cb.message.edit_text(PRICE_TEXT, reply_markup=kb)
    await cb.answer()


@dp.callback_query(F.data == "cancel_booking")
async def cb_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not has_active_booking(cb.from_user.id):
        await cb.answer("У тебя нет активной записи.", show_alert=True)
        return
    db_query("DELETE FROM bookings WHERE user_id = ?", (cb.from_user.id,))
    await cb.message.answer("❌ Запись отменена.", reply_markup=main_menu())
    await cb.answer("Запись отменена")


@dp.callback_query(F.data == "book")
async def cb_book(cb: CallbackQuery, state: FSMContext) -> None:
    if has_active_booking(cb.from_user.id):
        await cb.message.answer("У тебя уже есть запись 💅 Сначала отмени её")
        await cb.answer()
        return
    await state.set_state(BookingStates.choosing_date)
    await cb.message.edit_text(
        "📅 <b>Выбери дату:</b>", reply_markup=dates_keyboard()
    )
    await cb.answer()


@dp.callback_query(F.data.startswith("date_"))
async def cb_choose_date(cb: CallbackQuery, state: FSMContext) -> None:
    if has_active_booking(cb.from_user.id):
        await cb.message.answer("У тебя уже есть запись 💅 Сначала отмени её")
        await state.clear()
        await cb.answer()
        return
    date = cb.data.split("_", 1)[1]
    slots = get_slots_for_date(date)
    if not slots:
        await cb.answer("На эту дату нет свободных слотов", show_alert=True)
        return
    await state.update_data(date=date)
    await state.set_state(BookingStates.choosing_time)
    await cb.message.edit_text(
        f"⏰ <b>Свободное время на {date}:</b>",
        reply_markup=times_keyboard(date, slots),
    )
    await cb.answer()


@dp.callback_query(F.data.startswith("time_"))
async def cb_choose_time(cb: CallbackQuery, state: FSMContext) -> None:
    if has_active_booking(cb.from_user.id):
        await cb.message.answer("У тебя уже есть запись 💅 Сначала отмени её")
        await state.clear()
        await cb.answer()
        return
    _, date, time = cb.data.split("_", 2)
    if time not in get_slots_for_date(date):
        await cb.answer("Этот слот уже занят", show_alert=True)
        return
    await state.update_data(date=date, time=time)
    await state.set_state(BookingStates.entering_name)
    await cb.message.answer("✏️ Введи своё <b>имя</b>:")
    await cb.answer()


# ============== FSM TEXT ==============
@dp.message(BookingStates.entering_name)
async def st_name(msg: Message, state: FSMContext) -> None:
    name = (msg.text or "").strip()
    if not name:
        await msg.answer("Введи корректное имя.")
        return
    await state.update_data(name=name)
    await state.set_state(BookingStates.entering_phone)
    await msg.answer("📱 Введи свой <b>телефон</b>:")


@dp.message(BookingStates.entering_phone)
async def st_phone(msg: Message, state: FSMContext) -> None:
    phone = (msg.text or "").strip()
    if len(phone) < 5:
        await msg.answer("Введи корректный телефон.")
        return
    data = await state.get_data()
    date = data.get("date")
    time = data.get("time")
    name = data.get("name")
    user_id = msg.from_user.id

    if has_active_booking(user_id):
        await msg.answer("У тебя уже есть запись 💅 Сначала отмени её")
        await state.clear()
        return

    if time not in get_slots_for_date(date):
        await msg.answer("К сожалению, этот слот уже заняли. Попробуй ещё раз.",
                         reply_markup=main_menu())
        await state.clear()
        return

    db_query(
        "INSERT INTO bookings (user_id, name, phone, date, time) VALUES (?, ?, ?, ?, ?)",
        (user_id, name, phone, date, time),
    )

    await msg.answer(
        f"💖 <b>Ты записана!</b>\n\n"
        f"📅 Дата: <b>{date}</b>\n"
        f"⏰ Время: <b>{time}</b>\n\n"
        f"Жду тебя ✨",
        reply_markup=main_menu(),
    )

    try:
        await bot.send_message(
            ADMIN_CHAT_ID,
            f"🔥 <b>Новая запись</b>\n\n"
            f"👤 Имя: {name}\n"
            f"📱 Телефон: {phone}\n"
            f"📅 Дата: {date}\n"
            f"⏰ Время: {time}\n"
            f"🆔 ID: {user_id}",
        )
    except Exception as e:
        logging.warning(f"Не удалось уведомить админа: {e}")

    await state.clear()


# ============== MAIN ==============
async def main() -> None:
    db_init()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
