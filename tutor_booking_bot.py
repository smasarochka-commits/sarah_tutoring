#!/usr/bin/env python3

import asyncio
import json
import logging
import os
import uuid

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)


# =========================================================
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

ADMIN_ID = 5426579780

DATA_FILE = Path("booking_data.json")

TIMEZONE = ZoneInfo("Europe/Warsaw")

LESSON_MINUTES = 60

CANCEL_LIMIT_HOURS = 24


# =========================================================
# СОСТОЯНИЯ
# =========================================================

WAITING_NAME = 1
WAITING_DAY = 2
WAITING_TIME = 3

CANCEL_SELECT = 4

TRANSFER_SELECT = 5
TRANSFER_TIME = 6


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# ГЛАВНОЕ МЕНЮ
# =========================================================

def main_menu_keyboard():

    return ReplyKeyboardMarkup(
        [
            ["📝 Записатися"],
        ],
        resize_keyboard=True,
    )


# =========================================================
# ВРЕМЯ
# =========================================================

def now_local():

    return datetime.now(TIMEZONE)


def slot_datetime(day, time_str):

    return datetime.strptime(
        f"{day} {time_str}",
        "%Y-%m-%d %H:%M",
    ).replace(
        tzinfo=TIMEZONE
    )


def is_future_slot(day, time_str):

    return (
        slot_datetime(day, time_str)
        > now_local()
    )


# =========================================================
# DATA
# =========================================================

def load_data():

    if DATA_FILE.exists():

        try:

            with open(
                DATA_FILE,
                "r",
                encoding="utf-8",
            ) as f:

                data = json.load(f)

        except (
            json.JSONDecodeError,
            OSError,
        ) as e:

            logger.error(
                f"Не вдалося прочитати "
                f"{DATA_FILE}: {e}"
            )

            data = {}

    else:

        data = {}

    data.setdefault(
        "slots",
        {},
    )

    data.setdefault(
        "bookings",
        [],
    )

    changed = False

    # Добавляем ID старым записям,
    # если они были созданы до этой версии.
    for booking in data["bookings"]:

        if "id" not in booking:

            booking["id"] = uuid.uuid4().hex

            changed = True

    # Удаляем прошедшие свободные слоты.
    if clean_expired_slots(data):

        changed = True

    if changed:

        save_data(data)

    return data


def save_data(data):

    with open(
        DATA_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )


# =========================================================
# УДАЛЕНИЕ ПРОШЕДШИХ СЛОТОВ
# =========================================================

def clean_expired_slots(data):

    changed = False

    for day in list(
        data["slots"].keys()
    ):

        future_times = [

            time

            for time in data["slots"][day]

            if is_future_slot(
                day,
                time,
            )
        ]

        if future_times:

            future_times = sorted(
                future_times
            )

            if future_times != data["slots"][day]:

                data["slots"][day] = future_times

                changed = True

        else:

            del data["slots"][day]

            changed = True

    return changed


# =========================================================
# ДНИ НЕДЕЛИ
# =========================================================

def format_day(day):

    days = {
        0: "Понеділок",
        1: "Вівторок",
        2: "Середа",
        3: "Четвер",
        4: "П’ятниця",
        5: "Субота",
        6: "Неділя",
    }

    try:

        dt = datetime.strptime(
            day,
            "%Y-%m-%d",
        )

        return (
            f"{days[dt.weekday()]}, "
            f"{dt.strftime('%d.%m')}"
        )

    except (
        ValueError,
        TypeError,
    ):

        return day


def format_date(day):

    try:

        return datetime.strptime(
            day,
            "%Y-%m-%d",
        ).strftime(
            "%d.%m.%Y"
        )

    except (
        ValueError,
        TypeError,
    ):

        return day


# =========================================================
# БРОНИРОВАНИЯ
# =========================================================

def upcoming_bookings_for_user(
    data,
    user_id,
):

    bookings = [

        booking

        for booking in data["bookings"]

        if (
            booking.get("user_id") == user_id
            and is_future_slot(
                booking["day"],
                booking["time"],
            )
        )
    ]

    return sorted(

        bookings,

        key=lambda booking:
            slot_datetime(
                booking["day"],
                booking["time"],
            )
    )


def upcoming_all_bookings(data):

    bookings = [

        booking

        for booking in data["bookings"]

        if is_future_slot(
            booking["day"],
            booking["time"],
        )
    ]

    return sorted(

        bookings,

        key=lambda booking:
            slot_datetime(
                booking["day"],
                booking["time"],
            )
    )


# =========================================================
# ФОРМАТИРОВАНИЕ ЗАПИСЕЙ ДЛЯ АДМИНА
# =========================================================

def format_bookings_by_days(
    bookings,
):

    if not bookings:

        return (
            "📋 Майбутні записи:\n\n"
            "Немає майбутніх записів."
        )

    lines = [
        "📋 Майбутні записи:",
        "",
    ]

    current_day = None

    for booking in bookings:

        day = booking["day"]

        if day != current_day:

            current_day = day

            lines.append(
                f"📅 {format_day(day)}"
            )

        lines.append(
            f"   • "
            f"{booking['name']} — "
            f"{booking['time']}"
        )

    return "\n".join(lines)


# =========================================================
# СВОБОДНЫЕ СЛОТЫ
# =========================================================

def get_free_days():

    data = load_data()

    clean_expired_slots(data)

    save_data(data)

    return sorted(
        data["slots"].keys()
    )


def get_free_times(day):

    data = load_data()

    times = [

        time

        for time in data["slots"].get(
            day,
            [],
        )

        if is_future_slot(
            day,
            time,
        )
    ]

    return sorted(times)


# =========================================================
# ПРОВЕРКА ПЕРЕСЕЧЕНИЯ СЛОТОВ
# =========================================================

def slot_overlaps_existing(
    day,
    new_time,
    existing_times,
):

    start = slot_datetime(
        day,
        new_time,
    )

    end = (
        start
        + timedelta(
            minutes=LESSON_MINUTES
        )
    )

    for other_time in existing_times:

        other_start = slot_datetime(
            day,
            other_time,
        )

        other_end = (
            other_start
            + timedelta(
                minutes=LESSON_MINUTES
            )
        )

        if (
            start < other_end
            and other_start < end
        ):

            return True

    return False


def add_slot_if_free(
    data,
    day,
    time_str,
):

    if not is_future_slot(
        day,
        time_str,
    ):

        return False

    data["slots"].setdefault(
        day,
        [],
    )

    if time_str in data["slots"][day]:

        return False

    if slot_overlaps_existing(
        day,
        time_str,
        data["slots"][day],
    ):

        return False

    data["slots"][day].append(
        time_str
    )

    data["slots"][day].sort()

    return True


# =========================================================
# УДАЛЕНИЕ ПЕРЕСЕКАЮЩИХСЯ СЛОТОВ
# =========================================================

def remove_overlapping_slots(
    data,
    day,
    selected_time,
):

    selected_start = slot_datetime(
        day,
        selected_time,
    )

    selected_end = (
        selected_start
        + timedelta(
            minutes=LESSON_MINUTES
        )
    )

    remaining = []

    for slot in data["slots"].get(
        day,
        [],
    ):

        slot_start = slot_datetime(
            day,
            slot,
        )

        slot_end = (
            slot_start
            + timedelta(
                minutes=LESSON_MINUTES
            )
        )

        if (
            slot_start >= selected_end
            or slot_end <= selected_start
        ):

            remaining.append(slot)

    if remaining:

        data["slots"][day] = sorted(
            remaining
        )

    else:

        data["slots"].pop(
            day,
            None,
        )


# =========================================================
# СООБЩЕНИЕ АДМИНУ
# =========================================================

async def notify_admin(
    context,
    text,
):

    try:

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=text,
        )

    except Exception as e:

        logger.error(
            f"Не вдалося надіслати "
            f"повідомлення адміну: {e}"
        )


# =========================================================
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    await update.message.reply_text(

        f"Привіт, "
        f"{user.first_name}! 👋\n\n"

        "Тут ти можеш "
        "записатися на урок "
        "англійської.\n\n"

        "Обери потрібну дію:",

        reply_markup=main_menu_keyboard(),
    )


# =========================================================
# НАЧАЛО ЗАПИСИ
# =========================================================

async def book_command(
    update,
    context,
):

    await update.message.reply_text(

        "Будь ласка, "
        "напиши своє ім'я:",

        reply_markup=main_menu_keyboard(),
    )

    return WAITING_NAME


async def start_booking_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "Будь ласка, "
        "напиши своє ім'я:"
    )

    return WAITING_NAME


# =========================================================
# ИМЯ
# =========================================================

async def receive_name(
    update,
    context,
):

    name = update.message.text.strip()

    if len(name) < 2:

        await update.message.reply_text(
            "Ім'я занадто коротке. "
            "Напиши ще раз:"
        )

        return WAITING_NAME

    context.user_data["name"] = name

    free_days = get_free_days()

    if not free_days:

        await update.message.reply_text(

            "😔 Зараз немає "
            "вільних віконець.",

            reply_markup=main_menu_keyboard(),
        )

        return ConversationHandler.END

    keyboard = []

    for day in free_days:

        keyboard.append(
            [
                InlineKeyboardButton(
                    format_day(day),
                    callback_data=f"day_{day}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "❌ Скасувати",
                callback_data="booking_cancel",
            )
        ]
    )

    await update.message.reply_text(

        f"Дякую, {name}! 😊\n\n"
        "Обери вільний день:",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    return WAITING_DAY


# =========================================================
# ВЫБОР ДНЯ
# =========================================================

async def receive_day(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    if query.data == "booking_cancel":

        await query.edit_message_text(
            "Запис скасовано."
        )

        return ConversationHandler.END

    day = query.data.replace(
        "day_",
        "",
    )

    context.user_data["day"] = day

    free_times = get_free_times(day)

    if not free_times:

        await query.edit_message_text(
            "😔 На цей день уже немає "
            "вільних місць."
        )

        return ConversationHandler.END

    keyboard = []

    row = []

    for i, time in enumerate(
        free_times
    ):

        row.append(
            InlineKeyboardButton(
                time,
                callback_data=f"time_{time}",
            )
        )

        if (
            len(row) == 3
            or i == len(free_times) - 1
        ):

            keyboard.append(row)

            row = []

    keyboard.append(
        [
            InlineKeyboardButton(
                "◀️ Назад",
                callback_data="back_to_days",
            )
        ]
    )

    keyboard.append(
        [
            InlineKeyboardButton(
                "❌ Скасувати",
                callback_data="booking_cancel",
            )
        ]
    )

    await query.edit_message_text(

        f"Обери час "
        f"на {format_date(day)}:",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    return WAITING_TIME


# =========================================================
# НАЗАД К ДНЯМ
# =========================================================

async def back_to_days(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    free_days = get_free_days()

    if not free_days:

        await query.edit_message_text(
            "😔 Вільних днів "
            "більше немає."
        )

        return ConversationHandler.END

    keyboard = []

    for day in free_days:

        keyboard.append(
            [
                InlineKeyboardButton(
                    format_day(day),
                    callback_data=f"day_{day}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "❌ Скасувати",
                callback_data="booking_cancel",
            )
        ]
    )

    await query.edit_message_text(

        "Обери вільний день:",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    return WAITING_DAY


# =========================================================
# ВЫБОР ВРЕМЕНИ И СОЗДАНИЕ ЗАПИСИ
# =========================================================

async def receive_time(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    if query.data == "booking_cancel":

        await query.edit_message_text(
            "Запис скасовано."
        )

        return ConversationHandler.END

    if query.data == "back_to_days":

        return await back_to_days(
            update,
            context,
        )

    time_str = query.data.replace(
        "time_",
        "",
    )

    name = context.user_data.get(
        "name",
        "Без імені",
    )

    day = context.user_data.get(
        "day"
    )

    data = load_data()

    if (
        day not in data["slots"]
        or time_str not in data["slots"][day]
    ):

        await query.edit_message_text(

            "😔 На жаль, "
            "цей час щойно зайняли.\n\n"
            "Спробуй обрати інший."
        )

        return ConversationHandler.END

    # Удаляем выбранный слот.
    data["slots"][day].remove(
        time_str
    )

    # Удаляем все слоты,
    # пересекающиеся с 60-минутным уроком.
    remove_overlapping_slots(
        data,
        day,
        time_str,
    )

    booking = {

        "id": uuid.uuid4().hex,

        "name": name,

        "day": day,

        "time": time_str,

        "user_id":
            update.effective_user.id,

        "username":
            update.effective_user.username
            or "",

        "created_at":
            now_local().isoformat(),
    }

    data["bookings"].append(
        booking
    )

    save_data(data)

    # =====================================================
    # ТРИ КНОПКИ ПОСЛЕ УСПЕШНОЙ ЗАПИСИ
    # =====================================================

    keyboard = [

        [
            InlineKeyboardButton(
                "📝 Записатися ще раз",
                callback_data="start_booking",
            )
        ],

        [
            InlineKeyboardButton(
                "❌ Скасувати урок",
                callback_data="cancel_lesson",
            ),

            InlineKeyboardButton(
                "🔄 Перенести урок",
                callback_data="reschedule_lesson",
            ),
        ],
    ]

    await query.edit_message_text(

        f"✅ Готово!\n\n"

        f"Тебе записано:\n"

        f"👤 Ім'я: {name}\n"

        f"📅 Дата: "
        f"{format_date(day)}\n"

        f"🕐 Час: {time_str}\n\n"

        f"До зустрічі на уроці! 🌟",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    # Сообщение админу.
    await notify_admin(

        context,

        f"🔔 Новий запис!\n\n"

        f"👤 Ім'я: {name}\n"

        f"📅 {format_day(day)}\n"

        f"🕐 {time_str}\n"

        f"Telegram: "
        f"@{booking['username'] or 'немає'}\n"

        f"ID: {booking['user_id']}",
    )

    return ConversationHandler.END


# =========================================================
# ОТМЕНА УРОКА
# =========================================================

async def cancel_lesson_start(
    update,
    context,
):

    user_id = update.effective_user.id

    data = load_data()

    bookings = upcoming_bookings_for_user(
        data,
        user_id,
    )

    if not bookings:

        await update.message.reply_text(

            "У тебе немає "
            "майбутніх записів.",

            reply_markup=main_menu_keyboard(),
        )

        return ConversationHandler.END

    keyboard = []

    for booking in bookings:

        keyboard.append(

            [
                InlineKeyboardButton(

                    f"{format_day(booking['day'])} "
                    f"— {booking['time']}",

                    callback_data=
                        f"cancel_booking:"
                        f"{booking['id']}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "◀️ Назад",
                callback_data="action_back",
            )
        ]
    )

    await update.message.reply_text(

        "Обери урок, "
        "який хочеш скасувати:",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    return CANCEL_SELECT


async def cancel_lesson_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    user_id = update.effective_user.id

    data = load_data()

    bookings = upcoming_bookings_for_user(
        data,
        user_id,
    )

    if not bookings:

        await query.message.reply_text(

            "У тебе немає "
            "майбутніх записів.",

            reply_markup=main_menu_keyboard(),
        )

        return ConversationHandler.END

    keyboard = []

    for booking in bookings:

        keyboard.append(

            [
                InlineKeyboardButton(

                    f"{format_day(booking['day'])} "
                    f"— {booking['time']}",

                    callback_data=
                        f"cancel_booking:"
                        f"{booking['id']}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "◀️ Назад",
                callback_data="action_back",
            )
        ]
    )

    await query.message.reply_text(

        "Обери урок, "
        "який хочеш скасувати:",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    return CANCEL_SELECT


async def cancel_booking_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    booking_id = query.data.split(
        ":",
        1
    )[1]

    user_id = update.effective_user.id

    data = load_data()

    booking = next(

        (
            booking

            for booking in data["bookings"]

            if (
                booking.get("id")
                == booking_id

                and booking.get("user_id")
                == user_id

                and is_future_slot(
                    booking["day"],
                    booking["time"],
                )
            )
        ),

        None,
    )

    if not booking:

        await query.message.reply_text(
            "Цей запис уже недоступний.",
            reply_markup=main_menu_keyboard(),
        )

        return ConversationHandler.END

    lesson_start = slot_datetime(
        booking["day"],
        booking["time"],
    )

    hours_left = (

        lesson_start - now_local()

    ).total_seconds() / 3600

    returned = False

    # Возвращаем слот,
    # если до урока минимум 24 часа.
    if hours_left >= CANCEL_LIMIT_HOURS:

        returned = add_slot_if_free(

            data,

            booking["day"],

            booking["time"],
        )

    data["bookings"].remove(
        booking
    )

    save_data(data)

    if returned:

        slot_text = (
            "Віконце знову "
            "доступне для запису."
        )

    else:

        slot_text = (
            "Віконце не повертається "
            "у вільний розклад."
        )

    await query.message.reply_text(

        f"❌ Урок скасовано.\n\n"

        f"📅 {format_day(booking['day'])}\n"
        f"🕐 {booking['time']}\n\n"

        f"{slot_text}",

        reply_markup=main_menu_keyboard(),
    )

    await notify_admin(

        context,

        f"❌ Учень скасував урок!\n\n"

        f"👤 {booking['name']}\n"

        f"📅 {format_day(booking['day'])}\n"

        f"🕐 {booking['time']}\n\n"

        f"Повернено у вільні слоти: "
        f"{'так' if returned else 'ні'}",
    )

    return ConversationHandler.END


# =========================================================
# ПЕРЕНОС УРОКА
# =========================================================

async def reschedule_start(
    update,
    context,
):

    user_id = update.effective_user.id

    data = load_data()

    bookings = upcoming_bookings_for_user(
        data,
        user_id,
    )

    if not bookings:

        await update.message.reply_text(

            "У тебе немає "
            "майбутніх записів.",

            reply_markup=main_menu_keyboard(),
        )

        return ConversationHandler.END

    keyboard = []

    for booking in bookings:

        keyboard.append(

            [
                InlineKeyboardButton(

                    f"{format_day(booking['day'])} "
                    f"— {booking['time']}",

                    callback_data=
                        f"transfer_booking:"
                        f"{booking['id']}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "◀️ Назад",
                callback_data="action_back",
            )
        ]
    )

    await update.message.reply_text(

        "Обери урок, "
        "який хочеш перенести:",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    return TRANSFER_SELECT


async def reschedule_lesson_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    user_id = update.effective_user.id

    data = load_data()

    bookings = upcoming_bookings_for_user(
        data,
        user_id,
    )

    if not bookings:

        await query.message.reply_text(

            "У тебе немає "
            "майбутніх записів.",

            reply_markup=main_menu_keyboard(),
        )

        return ConversationHandler.END

    keyboard = []

    for booking in bookings:

        keyboard.append(

            [
                InlineKeyboardButton(

                    f"{format_day(booking['day'])} "
                    f"— {booking['time']}",

                    callback_data=
                        f"transfer_booking:"
                        f"{booking['id']}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "◀️ Назад",
                callback_data="action_back",
            )
        ]
    )

    await query.message.reply_text(

        "Обери урок, "
        "який хочеш перенести:",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    return TRANSFER_SELECT


async def transfer_booking_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    booking_id = query.data.split(
        ":",
        1
    )[1]

    user_id = update.effective_user.id

    data = load_data()

    booking = next(

        (
            booking

            for booking in data["bookings"]

            if (
                booking.get("id")
                == booking_id

                and booking.get("user_id")
                == user_id

                and is_future_slot(
                    booking["day"],
                    booking["time"],
                )
            )
        ),

        None,
    )

    if not booking:

        await query.message.reply_text(
            "Цей запис уже недоступний.",
            reply_markup=main_menu_keyboard(),
        )

        return ConversationHandler.END

    context.user_data[
        "transfer_booking_id"
    ] = booking_id

    free_days = get_free_days()

    if not free_days:

        await query.message.reply_text(

            "😔 Зараз немає "
            "вільних слотів "
            "для перенесення.",

            reply_markup=main_menu_keyboard(),
        )

        return ConversationHandler.END

    keyboard = []

    for day in free_days:

        keyboard.append(

            [
                InlineKeyboardButton(
                    format_day(day),
                    callback_data=
                        f"transfer_day:{day}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "◀️ Назад",
                callback_data="action_back",
            )
        ]
    )

    await query.message.reply_text(

        "Обери новий день:",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    return TRANSFER_TIME


async def transfer_day_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    day = query.data.replace(
        "transfer_day:",
        "",
    )

    times = get_free_times(day)

    if not times:

        await query.message.reply_text(
            "😔 На цей день уже немає "
            "вільних місць."
        )

        return ConversationHandler.END

    keyboard = []

    row = []

    for i, time in enumerate(
        times
    ):

        row.append(

            InlineKeyboardButton(

                time,

                callback_data=
                    f"transfer_time:"
                    f"{day}:"
                    f"{time}",
            )
        )

        if (
            len(row) == 3
            or i == len(times) - 1
        ):

            keyboard.append(row)

            row = []

    keyboard.append(
        [
            InlineKeyboardButton(
                "◀️ Назад",
                callback_data="action_back",
            )
        ]
    )

    await query.message.reply_text(

        f"Обери новий час "
        f"на {format_date(day)}:",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    return TRANSFER_TIME


async def transfer_time_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    if query.data == "action_back":

        await query.message.reply_text(
            "Обери потрібну дію:",
            reply_markup=main_menu_keyboard(),
        )

        return ConversationHandler.END

    parts = query.data.split(
        ":",
        2
    )

    day = parts[1]

    new_time = parts[2]

    booking_id = context.user_data.get(
        "transfer_booking_id"
    )

    user_id = update.effective_user.id

    data = load_data()

    booking = next(

        (
            booking

            for booking in data["bookings"]

            if (
                booking.get("id")
                == booking_id

                and booking.get("user_id")
                == user_id

                and is_future_slot(
                    booking["day"],
                    booking["time"],
                )
            )
        ),

        None,
    )

    if not booking:

        await query.message.reply_text(
            "Цей запис уже недоступний.",
            reply_markup=main_menu_keyboard(),
        )

        return ConversationHandler.END

    if (
        day not in data["slots"]
        or new_time not in data["slots"][day]
    ):

        await query.message.reply_text(

            "😔 Цей час щойно зайняли.\n"
            "Спробуй інший час."
        )

        return ConversationHandler.END

    old_day = booking["day"]

    old_time = booking["time"]

    old_start = slot_datetime(
        old_day,
        old_time,
    )

    hours_left = (

        old_start - now_local()

    ).total_seconds() / 3600

    # Забираем новый слот.
    data["slots"][day].remove(
        new_time
    )

    # Убираем пересекающиеся слоты.
    remove_overlapping_slots(
        data,
        day,
        new_time,
    )

    returned = False

    # Возвращаем старый слот,
    # если перенос сделан минимум за 24 часа.
    if hours_left >= CANCEL_LIMIT_HOURS:

        returned = add_slot_if_free(

            data,

            old_day,

            old_time,
        )

    # Меняем существующую запись.
    booking["day"] = day

    booking["time"] = new_time

    booking["rescheduled_at"] = (
        now_local().isoformat()
    )

    save_data(data)

    if returned:

        old_slot_text = (
            "Старе віконце знову "
            "доступне для запису."
        )

    else:

        old_slot_text = (
            "Старе віконце "
            "не повертається."
        )

    # После переноса снова показываем
    # три кнопки.
    keyboard = [

        [
            InlineKeyboardButton(
                "📝 Записатися ще раз",
                callback_data="start_booking",
            )
        ],

        [
            InlineKeyboardButton(
                "❌ Скасувати урок",
                callback_data="cancel_lesson",
            ),

            InlineKeyboardButton(
                "🔄 Перенести урок",
                callback_data="reschedule_lesson",
            ),
        ],
    ]

    await query.message.reply_text(

        f"✅ Урок перенесено!\n\n"

        f"📅 Нова дата: "
        f"{format_date(day)}\n"

        f"🕐 Новий час: "
        f"{new_time}\n\n"

        f"{old_slot_text}",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    await notify_admin(

        context,

        f"🔄 Учень переніс урок!\n\n"

        f"👤 {booking['name']}\n\n"

        f"Було:\n"
        f"📅 {format_day(old_day)}\n"
        f"🕐 {old_time}\n\n"

        f"Стало:\n"
        f"📅 {format_day(day)}\n"
        f"🕐 {new_time}\n\n"

        f"Старе віконце повернено: "
        f"{'так' if returned else 'ні'}",
    )

    return ConversationHandler.END


# =========================================================
# НАЗАД ИЗ ОТМЕНЫ/ПЕРЕНОСА
# =========================================================

async def action_back(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    await query.message.reply_text(

        "Обери потрібну дію:",

        reply_markup=main_menu_keyboard(),
    )

    return ConversationHandler.END


# =========================================================
# АДМИН: /SLOTS
# =========================================================

async def admin_slots(
    update,
    context,
):

    if update.effective_user.id != ADMIN_ID:

        return

    data = load_data()

    if not data["slots"]:

        await update.message.reply_text(
            "Вільних слотів немає."
        )

        return

    lines = [
        "📅 Вільні віконця:",
        "",
    ]

    for day in sorted(
        data["slots"]
    ):

        lines.append(
            f"📅 {format_day(day)}"
        )

        for time in sorted(
            data["slots"][day]
        ):

            lines.append(
                f"   • {time}"
            )

    await update.message.reply_text(
        "\n".join(lines)
    )


# =========================================================
# АДМИН: /BOOKINGS
# =========================================================

async def admin_bookings(
    update,
    context,
):

    if update.effective_user.id != ADMIN_ID:

        return

    data = load_data()

    bookings = upcoming_all_bookings(
        data
    )

    await update.message.reply_text(

        format_bookings_by_days(
            bookings
        )
    )


# =========================================================
# АДМИН: /ADDSLOT
# =========================================================

async def add_slot(
    update,
    context,
):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "Ця команда тільки "
            "для викладача."
        )

        return

    args = context.args

    if len(args) < 2:

        await update.message.reply_text(

            "Використання:\n\n"

            "/addslot "
            "РРРР-ММ-ДД "
            "ЧАС1 ЧАС2 ...\n\n"

            "Наприклад:\n"

            "/addslot "
            "2026-10-11 "
            "09:00 10:00 11:00"
        )

        return

    day = args[0]

    try:

        datetime.strptime(
            day,
            "%Y-%m-%d",
        )

    except ValueError:

        await update.message.reply_text(

            "❌ Неправильний формат дати.\n\n"

            "Використовуй:\n"
            "РРРР-ММ-ДД"
        )

        return

    data = load_data()

    added = []

    for time in args[1:]:

        try:

            datetime.strptime(
                time,
                "%H:%M",
            )

        except ValueError:

            continue

        if add_slot_if_free(

            data,
            day,
            time,
        ):

            added.append(time)

    save_data(data)

    if added:

        await update.message.reply_text(

            f"✅ Додано:\n\n"

            f"📅 {format_day(day)}\n\n"

            + "\n".join(
                f"• {time}"
                for time in added
            )
        )

    else:

        await update.message.reply_text(

            "Нічого не додано.\n\n"

            "Можливо, ці години вже "
            "існують, перетинаються "
            "або вже минули."
        )


# =========================================================
# АДМИН: /CLEARDAY
# =========================================================

async def clear_day(
    update,
    context,
):

    if update.effective_user.id != ADMIN_ID:

        return

    if not context.args:

        await update.message.reply_text(

            "Використання:\n\n"
            "/clearday РРРР-ММ-ДД"
        )

        return

    day = context.args[0]

    data = load_data()

    if day in data["slots"]:

        del data["slots"][day]

        save_data(data)

        await update.message.reply_text(

            f"🗑️ Слоти на "
            f"{format_day(day)} "
            f"видалено."
        )

    else:

        await update.message.reply_text(
            "На цей день слотів "
            "не було."
        )


# =========================================================
# /CANCEL
# =========================================================

async def cancel_command(
    update,
    context,
):

    await update.message.reply_text(

        "Дію скасовано.",

        reply_markup=main_menu_keyboard(),
    )

    return ConversationHandler.END


# =========================================================
# ЗАПУСК
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(

            "BOT_TOKEN не знайдено.\n"

            "Додай BOT_TOKEN у:\n"
            "Render → Environment."
        )

    # Совместимость с Python 3.14.
    try:

        asyncio.get_event_loop()

    except RuntimeError:

        asyncio.set_event_loop(
            asyncio.new_event_loop()
        )

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )


    # =====================================================
    # ОСНОВНОЙ CONVERSATION HANDLER
    # =====================================================

    conversation = ConversationHandler(

        entry_points=[

            # -------------------------
            # НОВАЯ ЗАПИСЬ
            # -------------------------

            CommandHandler(
                "book",
                book_command,
            ),

            MessageHandler(
                filters.Regex(
                    "^📝 Записатися$"
                ),
                book_command,
            ),

            CallbackQueryHandler(
                start_booking_callback,
                pattern="^start_booking$",
            ),


            # -------------------------
            # ОТМЕНА УЖЕ СУЩЕСТВУЮЩЕГО УРОКА
            # -------------------------

            CallbackQueryHandler(
                cancel_lesson_callback,
                pattern="^cancel_lesson$",
            ),

            MessageHandler(
                filters.Regex(
                    "^❌ Скасувати урок$"
                ),
                cancel_lesson_start,
            ),


            # -------------------------
            # ПЕРЕНОС УЖЕ СУЩЕСТВУЮЩЕГО УРОКА
            # -------------------------

            CallbackQueryHandler(
                reschedule_lesson_callback,
                pattern="^reschedule_lesson$",
            ),

            MessageHandler(
                filters.Regex(
                    "^🔄 Перенести урок$"
                ),
                reschedule_start,
            ),
        ],


        states={

            # =================================================
            # ЗАПИСЬ
            # =================================================

            WAITING_NAME: [

                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    receive_name,
                ),
            ],


            WAITING_DAY: [

                CallbackQueryHandler(
                    receive_day,
                    pattern="^day_",
                ),

                CallbackQueryHandler(
                    receive_day,
                    pattern="^booking_cancel$",
                ),
            ],


            WAITING_TIME: [

                CallbackQueryHandler(
                    receive_time,
                    pattern="^time_",
                ),

                CallbackQueryHandler(
                    back_to_days,
                    pattern="^back_to_days$",
                ),

                CallbackQueryHandler(
                    receive_time,
                    pattern="^booking_cancel$",
                ),
            ],


            # =================================================
            # ОТМЕНА
            # =================================================

            CANCEL_SELECT: [

                CallbackQueryHandler(
                    cancel_booking_callback,
                    pattern="^cancel_booking:",
                ),

                CallbackQueryHandler(
                    action_back,
                    pattern="^action_back$",
                ),
            ],


            # =================================================
            # ПЕРЕНОС — ВЫБОР СТАРОГО УРОКА
            # =================================================

            TRANSFER_SELECT: [

                CallbackQueryHandler(
                    transfer_booking_callback,
                    pattern="^transfer_booking:",
                ),

                CallbackQueryHandler(
                    action_back,
                    pattern="^action_back$",
                ),
            ],


            # =================================================
            # ПЕРЕНОС — ВЫБОР НОВОГО ВРЕМЕНИ
            # =================================================

            TRANSFER_TIME: [

                CallbackQueryHandler(
                    transfer_day_callback,
                    pattern="^transfer_day:",
                ),

                CallbackQueryHandler(
                    transfer_time_callback,
                    pattern="^transfer_time:",
                ),

                CallbackQueryHandler(
                    action_back,
                    pattern="^action_back$",
                ),
            ],
        },


        fallbacks=[

            CommandHandler(
                "cancel",
                cancel_command,
            ),
        ],


        allow_reentry=True,
    )


    # =====================================================
    # /START
    # =====================================================

    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )


    # =====================================================
    # ОСНОВНОЙ HANDLER
    # =====================================================

    app.add_handler(
        conversation
    )


    # =====================================================
    # АДМИНСКИЕ КОМАНДЫ
    # =====================================================

    app.add_handler(
        CommandHandler(
            "addslot",
            add_slot,
        )
    )

    app.add_handler(
        CommandHandler(
            "slots",
            admin_slots,
        )
    )

    app.add_handler(
        CommandHandler(
            "bookings",
            admin_bookings,
        )
    )

    app.add_handler(
        CommandHandler(
            "clearday",
            clear_day,
        )
    )


    print(
        "Бот запущений..."
    )


    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# =========================================================
# START PROGRAM
# =========================================================

if __name__ == "__main__":

    main()
