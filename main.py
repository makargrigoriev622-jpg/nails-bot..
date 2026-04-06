import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан.")
if not ADMIN_CHAT_ID:
    raise ValueError("ADMIN_CHAT_ID не задан.")

DB_PATH = "bookings.db"

# Все доступные слоты времени
ALL_SLOTS = ["09:00", "10:00", "11:00", "12:00", "14:00", "15:00", "16:00", "17:00"]

# ---------------------------------------------------------------------------
# Константы текстов
# ---------------------------------------------------------------------------

MAIN_MENU_TEXT = (
    "Привет, красотка 💅\n\n"
    "Я помогу тебе записаться к мастеру,\n"
    "посмотреть прайс и портфолио ✨\n\n"
    "<b>Выбирай, что хочешь сделать:</b>"
)

DATE_SCREEN_TEXT = (
    "📅 <b>Выбери удобную дату</b>\n\n"
    "Смотри, какие дни доступны 👇"
)

# ---------------------------------------------------------------------------
# Русские названия месяцев и дней недели
# ---------------------------------------------------------------------------

RU_MONTHS = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}
RU_WEEKDAYS = {
    0: "Понедельник", 1: "Вторник", 2: "Среда", 3: "Четверг",
    4: "Пятница", 5: "Суббота", 6: "Воскресенье",
}
RU_WEEKDAYS_SHORT = {
    0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт",
    4: "Пт", 5: "Сб", 6: "Вс",
}


def ru_date(dt: datetime) -> str:
    return f"{RU_WEEKDAYS[dt.weekday()]}, {dt.day} {RU_MONTHS[dt.month]}"


def ru_date_short(dt: datetime) -> str:
    return f"{RU_WEEKDAYS_SHORT[dt.weekday()]} {dt.day} {RU_MONTHS[dt.month]}"


# ---------------------------------------------------------------------------
# База данных
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Основная таблица записей
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL
        )
    """)
    try:
        cur.execute("ALTER TABLE bookings ADD COLUMN status TEXT DEFAULT 'active'")
    except Exception:
        pass

    # Таблица занятых слотов — PRIMARY KEY гарантирует уникальность на уровне БД.
    # Даже при одновременных запросах только один INSERT пройдёт успешно.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS booked_slots (
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            PRIMARY KEY (date, time)
        )
    """)

    conn.commit()
    conn.close()


def get_booked_times(date: str) -> set:
    """Возвращает множество занятых временных слотов для указанной даты."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT time FROM booked_slots WHERE date = ?", (date,))
    booked = {row[0] for row in cur.fetchall()}
    conn.close()
    return booked


def try_reserve_slot(date: str, time: str) -> bool:
    """
    Атомарно резервирует слот.
    Возвращает True если слот успешно занят, False если уже занят.
    INSERT OR IGNORE не вызовет исключение при дубликате — просто вернёт rowcount=0.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO booked_slots (date, time) VALUES (?, ?)",
        (date, time),
    )
    reserved = cur.rowcount == 1
    conn.commit()
    conn.close()
    return reserved


def release_slot(date: str, time: str):
    """Освобождает слот после отмены записи."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM booked_slots WHERE date = ? AND time = ?", (date, time))
    conn.commit()
    conn.close()


def save_booking(user_id, username, name, phone, date, time) -> int:
    """Сохраняет запись в таблицу bookings. Слот уже должен быть зарезервирован."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO bookings (user_id, username, name, phone, date, time, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
        """,
        (user_id, username, name, phone, date, time, datetime.now().isoformat()),
    )
    booking_id = cur.lastrowid
    conn.commit()
    conn.close()
    return booking_id


def get_active_bookings(user_id) -> list:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, phone, date, time FROM bookings
        WHERE user_id = ? AND status = 'active' AND date >= ?
        ORDER BY date ASC, time ASC
        """,
        (user_id, datetime.now().strftime("%Y-%m-%d")),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_booking_by_id(booking_id: int, user_id: int):
    """Возвращает запись по id и user_id."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, date, time FROM bookings WHERE id = ? AND user_id = ? AND status = 'active'",
        (booking_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return row


def cancel_booking_db(booking_id: int, user_id: int):
    """
    Отменяет запись и освобождает слот.
    Возвращает (date, time) если успешно, иначе None.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT date, time FROM bookings WHERE id = ? AND user_id = ? AND status = 'active'",
        (booking_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None

    date, time = row
    cur.execute(
        "UPDATE bookings SET status = 'cancelled' WHERE id = ? AND user_id = ?",
        (booking_id, user_id),
    )
    conn.commit()
    conn.close()

    release_slot(date, time)
    return date, time


# ---------------------------------------------------------------------------
# Состояния FSM
# ---------------------------------------------------------------------------

class BookingStates(StatesGroup):
    choosing_date = State()
    choosing_time = State()
    entering_name = State()
    entering_phone = State()
    cancelling = State()


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------

def get_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Записаться", callback_data="book")],
            [
                InlineKeyboardButton(text="💸 Прайс", callback_data="price"),
                InlineKeyboardButton(text="📸 Портфолио", callback_data="portfolio"),
            ],
            [InlineKeyboardButton(text="❌ Отменить запись", callback_data="cancel_booking")],
        ]
    )


def get_back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_main")]
        ]
    )


def get_date_keyboard() -> InlineKeyboardMarkup:
    today = datetime.now()
    buttons = []
    for i in range(1, 8):
        day = today + timedelta(days=i)
        label = f"📆 {ru_date_short(day)}"
        value = day.strftime("%Y-%m-%d")
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"date:{value}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_time_keyboard(date: str) -> InlineKeyboardMarkup | None:
    """
    Строит клавиатуру только из свободных слотов для указанной даты.
    Возвращает None если все слоты заняты.
    """
    booked = get_booked_times(date)
    free_slots = [s for s in ALL_SLOTS if s not in booked]

    if not free_slots:
        return None

    buttons = []
    row = []
    for slot in free_slots:
        row.append(InlineKeyboardButton(text=f"🕐 {slot}", callback_data=f"time:{slot}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="⬅️ Выбрать другую дату", callback_data="back_date")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📲 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_cancel_list_keyboard(bookings) -> InlineKeyboardMarkup:
    buttons = []
    for b_id, name, phone, date, time in bookings:
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            label = f"🗓 {ru_date_short(dt)} в {time}"
        except Exception:
            label = f"🗓 {date} в {time}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"cancel:{b_id}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# Вспомогательные функции отображения
# ---------------------------------------------------------------------------

async def show_main_menu_new(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        MAIN_MENU_TEXT,
        parse_mode="HTML",
        reply_markup=get_main_menu(),
    )


async def show_main_menu_edit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        MAIN_MENU_TEXT,
        parse_mode="HTML",
        reply_markup=get_main_menu(),
    )


async def show_date_screen(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BookingStates.choosing_date)
    await callback.message.edit_text(
        DATE_SCREEN_TEXT,
        parse_mode="HTML",
        reply_markup=get_date_keyboard(),
    )


# ---------------------------------------------------------------------------
# Бот и диспетчер
# ---------------------------------------------------------------------------

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ---------------------------------------------------------------------------
# Обработчики
# ---------------------------------------------------------------------------

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await show_main_menu_new(message, state)


@dp.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await show_main_menu_edit(callback, state)


@dp.callback_query(F.data == "price")
async def show_price(callback: CallbackQuery):
    await callback.message.edit_text(
        "💸 <b>Прайс-лист</b>\n"
        "━━━━━━━━━━━━━━━━\n\n"
        "💅 <b>Ногти</b>\n"
        "  • Маникюр — 800 ₽\n"
        "  • Маникюр + покрытие — 1 400 ₽\n"
        "  • Педикюр — 1 200 ₽\n"
        "  • Наращивание — 2 500 ₽\n\n"
        "👁 <b>Ресницы</b>\n"
        "  • Классика — 1 500 ₽\n"
        "  • 2D / объём — 2 000 ₽\n"
        "  • Коррекция — 900 ₽\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "🎀 Будет красиво, обещаю!",
        parse_mode="HTML",
        reply_markup=get_back_button(),
    )


@dp.callback_query(F.data == "portfolio")
async def show_portfolio(callback: CallbackQuery):
    await callback.message.edit_text(
        "📸 <b>Моё портфолио</b>\n\n"
        "Смотри мои работы здесь:\n"
        "👉 https://instagram.com/твой_ник\n\n"
        "Там много всего красивого —\n"
        "ноготочки, реснички, довольные клиентки 💖\n\n"
        "Хочешь так же? Записывайся!",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📅 Записаться", callback_data="book")],
                [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_main")],
            ]
        ),
    )


@dp.callback_query(F.data == "book")
async def start_booking(callback: CallbackQuery, state: FSMContext):
    await show_date_screen(callback, state)


@dp.callback_query(F.data == "back_date")
async def back_to_date(callback: CallbackQuery, state: FSMContext):
    await show_date_screen(callback, state)


@dp.callback_query(BookingStates.choosing_date, F.data.startswith("date:"))
async def date_chosen(callback: CallbackQuery, state: FSMContext):
    chosen_date = callback.data.split(":", 1)[1]
    dt = datetime.strptime(chosen_date, "%Y-%m-%d")
    display_date = ru_date(dt)

    keyboard = get_time_keyboard(chosen_date)

    if keyboard is None:
        # Все слоты на этот день заняты
        await callback.message.edit_text(
            f"😢 На <b>{display_date}</b> все места уже заняты.\n\n"
            "Выбери другой день — найдём для тебя свободное время 💅",
            parse_mode="HTML",
            reply_markup=get_date_keyboard(),
        )
        return

    await state.update_data(date=chosen_date, display_date=display_date)
    await state.set_state(BookingStates.choosing_time)
    await callback.message.edit_text(
        f"✅ Дата: <b>{display_date}</b>\n\n"
        "🕐 <b>Выбери удобное время:</b>\n"
        "<i>Показаны только свободные слоты</i>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@dp.callback_query(BookingStates.choosing_time, F.data.startswith("time:"))
async def time_chosen(callback: CallbackQuery, state: FSMContext):
    chosen_time = callback.data.split(":", 1)[1]
    await state.update_data(time=chosen_time)
    await state.set_state(BookingStates.entering_name)
    data = await state.get_data()
    await callback.message.edit_text(
        f"✅ Дата: <b>{data['display_date']}</b>\n"
        f"✅ Время: <b>{chosen_time}</b>\n\n"
        "😊 <b>Как тебя зовут?</b>\n\n"
        "<i>Напиши своё имя 👇</i>",
        parse_mode="HTML",
    )


@dp.message(BookingStates.entering_name)
async def name_entered(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2:
        await message.answer("Напиши имя чуть подробнее, пожалуйста 😊")
        return
    await state.update_data(name=name)
    await state.set_state(BookingStates.entering_phone)
    await message.answer(
        f"Отлично, <b>{name}</b>! 🌸\n\n"
        "📲 <b>Оставь номер телефона,</b>\n"
        "чтобы мы тебя не потеряли 💖\n\n"
        "<i>Нажми кнопку или напиши вручную 👇</i>",
        parse_mode="HTML",
        reply_markup=get_phone_keyboard(),
    )


@dp.message(BookingStates.entering_phone, F.contact)
async def phone_from_contact(message: Message, state: FSMContext):
    await finish_booking(message, state, message.contact.phone_number)


@dp.message(BookingStates.entering_phone, F.text)
async def phone_from_text(message: Message, state: FSMContext):
    phone = message.text.strip()
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 7:
        await message.answer(
            "Хм, что-то не похоже на номер телефона 🤔\n"
            "Попробуй ещё раз, например: +7 999 123 45 67"
        )
        return
    await finish_booking(message, state, phone)


async def finish_booking(message: Message, state: FSMContext, phone: str):
    data = await state.get_data()
    name = data["name"]
    date = data["date"]
    display_date = data["display_date"]
    time = data["time"]
    user_id = message.from_user.id
    username = message.from_user.username or "—"

    # Атомарно резервируем слот — финальная проверка перед сохранением.
    # Если два пользователя нажали одновременно, только один INSERT пройдёт.
    reserved = try_reserve_slot(date, time)

    if not reserved:
        # Слот уже занят — предлагаем выбрать другое время
        await message.answer(
            "Это время уже занято 😢\n\n"
            "Пока ты заполняла данные, кто-то успел записаться раньше.\n"
            "Выбери другое время — сделаем всё красиво 💅",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )
        await state.clear()

        # Показываем свободные слоты на ту же дату
        keyboard = get_time_keyboard(date)
        if keyboard:
            await message.answer(
                f"🕐 <b>Свободное время на {display_date}:</b>",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            await state.update_data(date=date, display_date=display_date)
            await state.set_state(BookingStates.choosing_time)
        else:
            await message.answer(
                f"😢 На {display_date} больше нет свободных мест.\n"
                "Выбери другой день 👇",
                parse_mode="HTML",
                reply_markup=get_date_keyboard(),
            )
            await state.set_state(BookingStates.choosing_date)
        return

    # Слот успешно зарезервирован — сохраняем запись
    booking_id = save_booking(user_id, username, name, phone, date, time)

    await message.answer(
        "Ты записана 💖\n\n"
        "━━━━━━━━━━━━━━━━\n"
        f"📅 <b>Дата:</b> {display_date}\n"
        f"🕐 <b>Время:</b> {time}\n"
        f"👤 <b>Имя:</b> {name}\n"
        f"📱 <b>Телефон:</b> {phone}\n"
        "━━━━━━━━━━━━━━━━\n\n"
        "Ждём тебя! Сделаем идеальные ноготочки 💅✨\n\n"
        "<i>Если планы изменятся — можно отменить запись в меню.</i>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )

    try:
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                f"🔔 <b>Новая запись #{booking_id}</b>\n\n"
                f"👤 Имя: {name}\n"
                f"📱 Телефон: {phone}\n"
                f"📅 Дата: {display_date}\n"
                f"🕐 Время: {time}\n"
                f"🆔 User ID: {user_id}\n"
                f"📛 Username: @{username}"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить администратора: {e}")

    await state.clear()
    await message.answer(
        "Что ещё хочешь сделать? 💅",
        parse_mode="HTML",
        reply_markup=get_main_menu(),
    )


@dp.callback_query(F.data == "cancel_booking")
async def cancel_booking_start(callback: CallbackQuery, state: FSMContext):
    bookings = get_active_bookings(callback.from_user.id)

    if not bookings:
        await callback.message.edit_text(
            "🙅‍♀️ <b>Активных записей нет</b>\n\n"
            "Похоже, ты ещё не записывалась — или уже всё отменено 💭\n\n"
            "Хочешь записаться? 🌸",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📅 Записаться", callback_data="book")],
                    [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_main")],
                ]
            ),
        )
        return

    await state.set_state(BookingStates.cancelling)
    await callback.message.edit_text(
        "❌ <b>Отмена записи</b>\n\n"
        "Выбери запись, которую хочешь отменить 👇",
        parse_mode="HTML",
        reply_markup=get_cancel_list_keyboard(bookings),
    )


@dp.callback_query(BookingStates.cancelling, F.data.startswith("cancel:"))
async def confirm_cancel(callback: CallbackQuery, state: FSMContext):
    booking_id = int(callback.data.split(":", 1)[1])
    user_id = callback.from_user.id

    # cancel_booking_db отменяет запись и освобождает слот в booked_slots
    result = cancel_booking_db(booking_id, user_id)
    await state.clear()

    if result:
        try:
            await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"🚫 <b>Запись #{booking_id} отменена</b>\n\n"
                    f"🆔 User ID: {user_id}\n"
                    f"📛 Username: @{callback.from_user.username or '—'}"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить администратора об отмене: {e}")

        await callback.message.edit_text(
            "✅ <b>Запись отменена</b>\n\n"
            "Жаль, что не получится 🥺\n"
            "Но ждём тебя в следующий раз — будет красиво 💅\n\n"
            "Если захочешь записаться снова — я тут 🌸",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📅 Записаться снова", callback_data="book")],
                    [InlineKeyboardButton(text="🏠 Назад в меню", callback_data="back_main")],
                ]
            ),
        )
    else:
        await callback.message.edit_text(
            "Что-то пошло не так 😔\n"
            "Попробуй ещё раз или напиши нам напрямую.",
            parse_mode="HTML",
            reply_markup=get_back_button(),
        )


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

async def main():
    init_db()
    logger.info("Бот запускается...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
