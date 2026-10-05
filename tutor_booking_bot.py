#!/usr/bin/env python3

"""
Telegram-бот для записи на уроки английского.
Язык интерфейса: украинский.
Урок длится 60 минут.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
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


# ====================== НАСТРОЙКИ ======================

BOT_TOKEN = os.getenv("BOT_TOKEN")

ADMIN_ID = 5426579780

DATA_FILE = Path("booking_data.json")

# ========================================================


WAITING_NAME, WAITING_DAY, WAITING_TIME = range(3)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ====================== ДАННЫЕ ======================


def load_data():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)

        except (json.JSONDecodeError, OSError) as e:
            logger.error(
                f"Не вдалося прочитати {DATA_FILE}: {e}"
            )

    return {
        "slots": {},
        "bookings": []
    }


def save_data(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

    except OSError as e:
        logger.error(
            f"Не вдалося зберегти дані: {e}"
        )
        raise


# ====================== СЛОТЫ ======================


def get_free_days():
    data = load_data()

    free_days = []

    for day, times in data["slots"].items():
        if times:
            free_days.append(day)

    return sorted(free_days)


def get_free_times(day):
    data = load_data()

    return data["slots"].get(day, [])


def format_day(day):
    try:
        dt = datetime.strptime(
            day,
            "%Y-%m-%d"
        )

        nice = dt.strftime(
            "%d.%m (%A)"
        )

        days_ua = {
            "Monday": "Пн",
            "Tuesday": "Вт",
            "Wednesday": "Ср",
            "Thursday": "Чт",
            "Friday": "Пт",
            "Saturday": "Сб",
            "Sunday": "Нд",
        }

        for eng, ua in days_ua.items():
            nice = nice.replace(
                eng,
                ua
            )

        return nice

    except (ValueError, TypeError):
        return day


def format_date(day):
    try:
        dt = datetime.strptime(
            day,
            "%Y-%m-%d"
        )

        return dt.strftime(
            "%d.%m.%Y"
        )

    except (ValueError, TypeError):
        return day


# ====================== СТАРТ ======================


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    text = (
        f"Привіт, {user.first_name}! 👋\n\n"
        "Я бот для запису на уроки англійської до Сари.\n\n"
        "Щоб записатися, натисни кнопку нижче "
        "або напиши /book"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "📝 Записатися",
                callback_data="start_booking"
            )
        ]
    ]

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ====================== ЗАПИС ======================


async def book_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "Будь ласка, напиши своє ім'я (як тебе записувати):",
        reply_markup=ReplyKeyboardRemove()
    )

    return WAITING_NAME


async def start_booking_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "Будь ласка, напиши своє ім'я (як тебе записувати):"
    )

    return WAITING_NAME


async def receive_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    name = update.message.text.strip()

    if len(name) < 2:
        await update.message.reply_text(
            "Ім'я занадто коротке. Напиши, будь ласка, ще раз:"
        )

        return WAITING_NAME

    context.user_data["name"] = name

    free_days = get_free_days()

    if not free_days:
        await update.message.reply_text(
            "😔 На жаль, зараз немає вільних віконець.\n"
            "Спробуй пізніше або напиши викладачу."
        )

        return ConversationHandler.END

    keyboard = []

    for day in free_days:
        keyboard.append(
            [
                InlineKeyboardButton(
                    format_day(day),
                    callback_data=f"day_{day}"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "❌ Скасувати",
                callback_data="cancel"
            )
        ]
    )

    await update.message.reply_text(
        f"Дякую, {name}! 😊\n\n"
        "Обери вільний день:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return WAITING_DAY


async def receive_day(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text(
            "Запис скасовано."
        )

        return ConversationHandler.END

    day = query.data.replace(
        "day_",
        ""
    )

    context.user_data["day"] = day

    free_times = get_free_times(day)

    if not free_times:
        await query.edit_message_text(
            "😔 Вибач, на цей день уже немає "
            "вільних місць.\n"
            "Спробуй обрати інший день."
        )

        return ConversationHandler.END

    keyboard = []

    row = []

    for i, time in enumerate(
        sorted(free_times)
    ):
        row.append(
            InlineKeyboardButton(
                time,
                callback_data=f"time_{time}"
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
                "◀️ Назад до днів",
                callback_data="back_to_days"
            )
        ]
    )

    keyboard.append(
        [
            InlineKeyboardButton(
                "❌ Скасувати",
                callback_data="cancel"
            )
        ]
    )

    nice_day = format_date(day)

    await query.edit_message_text(
        f"Обери зручний час на {nice_day}:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return WAITING_TIME


async def back_to_days(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    free_days = get_free_days()

    if not free_days:
        await query.edit_message_text(
            "😔 Вільних днів більше немає."
        )

        return ConversationHandler.END

    keyboard = []

    for day in free_days:
        keyboard.append(
            [
                InlineKeyboardButton(
                    format_day(day),
                    callback_data=f"day_{day}"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "❌ Скасувати",
                callback_data="cancel"
            )
        ]
    )

    await query.edit_message_text(
        "Обери вільний день:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return WAITING_DAY


# ====================== ВЫБОР ВРЕМЕНИ ======================


async def receive_time(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text(
            "Запис скасовано."
        )

        return ConversationHandler.END

    if query.data == "back_to_days":
        return await back_to_days(
            update,
            context
        )

    time_str = query.data.replace(
        "time_",
        ""
    )

    name = context.user_data.get(
        "name",
        "Без імені"
    )

    day = context.user_data.get(
        "day"
    )

    data = load_data()

    # Проверяем, что выбранный слот ещё свободен
    if (
        day not in data["slots"]
        or time_str not in data["slots"][day]
    ):
        await query.edit_message_text(
            "😔 На жаль, цей час щойно зайняли.\n"
            "Спробуй обрати інший."
        )

        return ConversationHandler.END

    # ==============================
    # УРОК ДЛИТСЯ 60 МИНУТ
    # ==============================

    # Удаляем выбранный слот
    data["slots"][day].remove(
        time_str
    )

    selected_time = datetime.strptime(
        time_str,
        "%H:%M"
    )

    lesson_end = (
        selected_time
        + timedelta(minutes=60)
    )

    remaining_times = []

    # Удаляем все слоты, которые
    # пересекаются с этим 60-минутным уроком.
    for slot in data["slots"][day]:

        slot_time = datetime.strptime(
            slot,
            "%H:%M"
        )

        slot_end = (
            slot_time
            + timedelta(minutes=60)
        )

        if (
            slot_time >= lesson_end
            or slot_end <= selected_time
        ):
            remaining_times.append(
                slot
            )

    data["slots"][day] = remaining_times

    if not data["slots"][day]:
        del data["slots"][day]

    # ==============================
    # СОЗДАЁМ ЗАПИСЬ
    # ==============================

    booking = {
        "name": name,
        "day": day,
        "time": time_str,
        "user_id": update.effective_user.id,
        "username": (
            update.effective_user.username
            or ""
        ),
        "created_at": datetime.now().isoformat()
    }

    data["bookings"].append(
        booking
    )

    save_data(data)

    nice_day = format_date(day)

    # ==============================
    # ПОДТВЕРЖДЕНИЕ + КНОПКА
    # ==============================

    keyboard = [
        [
            InlineKeyboardButton(
                "📝 Записатися ще раз",
                callback_data="start_booking"
            )
        ]
    ]

    await query.edit_message_text(
        f"✅ Готово!\n\n"
        f"Тебе записано:\n"
        f"👤 Ім'я: {name}\n"
        f"📅 Дата: {nice_day}\n"
        f"🕐 Час: {time_str}\n\n"
        f"До зустрічі на уроці! 🌟",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

    # ==============================
    # СООБЩЕНИЕ АДМИНУ
    # ==============================

    admin_text = (
        f"🔔 Новий запис!\n\n"
        f"👤 Ім'я: {name}\n"
        f"📅 Дата: {nice_day}\n"
        f"🕐 Час: {time_str}\n"
        f"Telegram: @{booking['username'] or 'немає'}\n"
        f"ID: {booking['user_id']}"
    )

    try:

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_text
        )

    except Exception as e:

        logger.error(
            f"Не вдалося надіслати "
            f"повідомлення адміну: {e}"
        )

    return ConversationHandler.END


# ====================== ОТМЕНА ======================


async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "Запис скасовано.",
        reply_markup=ReplyKeyboardRemove()
    )

    return ConversationHandler.END


async def cancel_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "Запис скасовано."
    )

    return ConversationHandler.END


# ====================== АДМИН ======================


async def add_slot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "Ця команда тільки для викладача."
        )

        return

    args = context.args

    if len(args) < 2:

        await update.message.reply_text(
            "Використання:\n"
            "/addslot РІК-МІСЯЦЬ-ДЕНЬ ЧАС1 ЧАС2 ...\n\n"
            "Приклад:\n"
            "/addslot 2026-10-07 19:00 19:30 20:00"
        )

        return

    day = args[0]

    times = args[1:]

    try:

        datetime.strptime(
            day,
            "%Y-%m-%d"
        )

    except ValueError:

        await update.message.reply_text(
            "Неправильний формат дати.\n"
            "Використовуй РРРР-ММ-ДД"
        )

        return

    data = load_data()

    if day not in data["slots"]:
        data["slots"][day] = []

    added = []

    for time in times:

        try:

            datetime.strptime(
                time,
                "%H:%M"
            )

        except ValueError:

            continue

        if time not in data["slots"][day]:

            data["slots"][day].append(
                time
            )

            added.append(time)

    data["slots"][day] = sorted(
        data["slots"][day]
    )

    save_data(data)

    if added:

        await update.message.reply_text(
            f"✅ Додано на {day}:\n"
            + "\n".join(
                f"• {time}"
                for time in added
            )
        )

    else:

        await update.message.reply_text(
            "Ці години вже були додані "
            "або мають неправильний формат."
        )


async def list_slots(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if update.effective_user.id != ADMIN_ID:
        return

    data = load_data()

    if not data["slots"]:

        await update.message.reply_text(
            "Вільних слотів немає."
        )

        return

    text = "📅 Вільні віконця:\n\n"

    for day in sorted(
        data["slots"].keys()
    ):

        times = ", ".join(
            sorted(data["slots"][day])
        )

        text += (
            f"<b>{day}</b>: "
            f"{times}\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


async def list_bookings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if update.effective_user.id != ADMIN_ID:
        return

    data = load_data()

    if not data["bookings"]:

        await update.message.reply_text(
            "Записів поки немає."
        )

        return

    text = "📋 Останні записи:\n\n"

    for booking in data["bookings"][-15:]:

        text += (
            f"• {booking['name']} — "
            f"{booking['day']} "
            f"о {booking['time']}\n"
        )

    await update.message.reply_text(
        text
    )


async def clear_day(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:

        await update.message.reply_text(
            "Використання: "
            "/clearday РРРР-ММ-ДД"
        )

        return

    day = context.args[0]

    data = load_data()

    if day in data["slots"]:

        del data["slots"][day]

        save_data(data)

        await update.message.reply_text(
            f"Слоти на {day} видалено."
        )

    else:

        await update.message.reply_text(
            "На цей день слотів не було."
        )


# ====================== ЗАПУСК ======================


def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN не знайдено. "
            "Додай BOT_TOKEN у "
            "Render → Environment."
        )

    # Совместимость с Python 3.14
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

    conv_handler = ConversationHandler(

        entry_points=[
            CommandHandler(
                "book",
                book_command
            ),

            CallbackQueryHandler(
                start_booking_callback,
                pattern="^start_booking$"
            ),
        ],

        states={

            WAITING_NAME: [

                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    receive_name
                )

            ],

            WAITING_DAY: [

                CallbackQueryHandler(
                    receive_day,
                    pattern="^day_"
                ),

                CallbackQueryHandler(
                    cancel_callback,
                    pattern="^cancel$"
                ),

            ],

            WAITING_TIME: [

                CallbackQueryHandler(
                    receive_time,
                    pattern="^time_"
                ),

                CallbackQueryHandler(
                    back_to_days,
                    pattern="^back_to_days$"
                ),

                CallbackQueryHandler(
                    cancel_callback,
                    pattern="^cancel$"
                ),

            ],
        },

        fallbacks=[
            CommandHandler(
                "cancel",
                cancel
            )
        ],

        allow_reentry=True,
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        conv_handler
    )

    app.add_handler(
        CommandHandler(
            "addslot",
            add_slot
        )
    )

    app.add_handler(
        CommandHandler(
            "slots",
            list_slots
        )
    )

    app.add_handler(
        CommandHandler(
            "bookings",
            list_bookings
        )
    )

    app.add_handler(
        CommandHandler(
            "clearday",
            clear_day
        )
    )

    print("Бот запущений...")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
