
import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from datetime import datetime, timedelta
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

# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 5426579780
DATA_FILE = Path("booking_data.json")
TZ = ZoneInfo("Europe/Warsaw")
LESSON_MINUTES = 60
CANCEL_HOURS = 24

NAME, DAY, TIME, CANCEL_SELECT, TRANSFER_SELECT, TRANSFER_DAY, TRANSFER_TIME = range(7)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ================= ДАТА И ВРЕМЯ =================

def now():
    return datetime.now(TZ)


def dt(day, time):
    return datetime.strptime(
        f"{day} {time}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=TZ)


def future(day, time):
    return dt(day, time) > now()


def day_name(day):
    names = [
        "Понеділок", "Вівторок", "Середа",
        "Четвер", "П’ятниця", "Субота", "Неділя",
    ]
    d = datetime.strptime(day, "%Y-%m-%d")
    return f"{names[d.weekday()]}, {d.strftime('%d.%m')}"


def date_text(day):
    return datetime.strptime(day, "%Y-%m-%d").strftime("%d.%m.%Y")


def menu():
    return ReplyKeyboardMarkup(
        [["📝 Записатися"]],
        resize_keyboard=True,
    )


# ================= СОХРАНЕНИЕ ДАННЫХ =================

def save(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            logger.exception("Не удалось прочитать файл записей")
            data = {}
    else:
        data = {}

    data.setdefault("slots", {})
    data.setdefault("bookings", [])

    changed = False

    for b in data["bookings"]:
        if "id" not in b:
            b["id"] = uuid.uuid4().hex
            changed = True

    # Удаляем только прошедшие СВОБОДНЫЕ окна.
    for day in list(data["slots"]):
        remaining = [
            t for t in data["slots"][day]
            if future(day, t)
        ]
        if remaining != data["slots"][day]:
            changed = True
        if remaining:
            data["slots"][day] = sorted(set(remaining))
        else:
            del data["slots"][day]
            changed = True

    if changed:
        save(data)

    return data


def upcoming_for_user(data, user_id):
    result = [
        b for b in data["bookings"]
        if b["user_id"] == user_id and future(b["day"], b["time"])
    ]
    return sorted(result, key=lambda b: dt(b["day"], b["time"]))


def upcoming_all(data):
    result = [
        b for b in data["bookings"]
        if future(b["day"], b["time"])
    ]
    return sorted(result, key=lambda b: dt(b["day"], b["time"]))


async def tell_admin(context, text):
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=text)
    except Exception:
        logger.exception("Не удалось отправить сообщение админу")


# ================= СЛОТЫ И ПЕРЕСЕЧЕНИЯ =================

def overlaps(day, time, other_time):
    start = dt(day, time)
    end = start + timedelta(minutes=LESSON_MINUTES)
    other_start = dt(day, other_time)
    other_end = other_start + timedelta(minutes=LESSON_MINUTES)
    return start < other_end and other_start < end


def remove_overlapping_slots(data, day, booked_time):
    """Удаляем пересекающиеся окна только после бронирования."""
    remaining = [
        t for t in data["slots"].get(day, [])
        if not overlaps(day, booked_time, t)
    ]
    if remaining:
        data["slots"][day] = sorted(remaining)
    else:
        data["slots"].pop(day, None)


def add_slot(data, day, time):
    """
    При добавлении разрешаем пересекающиеся свободные окна.
    Запрещаем только время, которое уже занято бронированием.
    """
    if not future(day, time):
        return "past", None

    booked = next(
        (
            b for b in data["bookings"]
            if b["day"] == day and b["time"] == time
        ),
        None,
    )
    if booked:
        return "booked", booked

    data["slots"].setdefault(day, [])

    if time in data["slots"][day]:
        return "exists", None

    data["slots"][day].append(time)
    data["slots"][day].sort()
    return "added", None


# ================= /START И ЗАПИСЬ =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Привіт, {update.effective_user.first_name}! 👋\n\n"
        "Тут ти можеш записатися на урок англійської.\n"
        "Обери дію нижче.",
        reply_markup=menu(),
    )


async def begin_booking(update, context):
    await update.effective_message.reply_text(
        "Напиши своє ім'я:"
    )
    return NAME


async def receive_name(update, context):
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("Напиши, будь ласка, ім'я ще раз.")
        return NAME

    context.user_data["name"] = name
    data = load()
    days = sorted(data["slots"])

    if not days:
        await update.message.reply_text(
            "😔 Наразі немає вільних віконець.",
            reply_markup=menu(),
        )
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(day_name(d), callback_data=f"day:{d}")]
        for d in days
    ]
    keyboard.append([
        InlineKeyboardButton("❌ Скасувати запис", callback_data="stop_booking")
    ])

    await update.message.reply_text(
        f"Дякую, {name}! Обери день:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return DAY


async def receive_day(update, context):
    q = update.callback_query
    await q.answer()

    if q.data == "stop_booking":
        await q.edit_message_text("Запис скасовано.")
        return ConversationHandler.END

    day = q.data.split(":", 1)[1]
    context.user_data["day"] = day
    times = [
        t for t in load()["slots"].get(day, [])
        if future(day, t)
    ]

    if not times:
        await q.edit_message_text("На цей день вільних місць уже немає.")
        return ConversationHandler.END

    keyboard = []
    row = []
    for i, t in enumerate(sorted(times)):
        row.append(InlineKeyboardButton(t, callback_data=f"time:{t}"))
        if len(row) == 3 or i == len(times) - 1:
            keyboard.append(row)
            row = []

    keyboard.append([
        InlineKeyboardButton("◀️ Назад до днів", callback_data="back_days")
    ])
    keyboard.append([
        InlineKeyboardButton("❌ Скасувати запис", callback_data="stop_booking")
    ])

    await q.edit_message_text(
        f"Обери час на {date_text(day)}:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return TIME


async def back_days(update, context):
    q = update.callback_query
    await q.answer()

    days = sorted(load()["slots"])
    if not days:
        await q.edit_message_text("Наразі немає вільних віконець.")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(day_name(d), callback_data=f"day:{d}")]
        for d in days
    ]
    keyboard.append([
        InlineKeyboardButton("❌ Скасувати запис", callback_data="stop_booking")
    ])
    await q.edit_message_text(
        "Обери день:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return DAY


async def receive_time(update, context):
    q = update.callback_query
    await q.answer()

    if q.data == "stop_booking":
        await q.edit_message_text("Запис скасовано.")
        return ConversationHandler.END

    if q.data == "back_days":
        return await back_days(update, context)

    time = q.data.split(":", 1)[1]
    day = context.user_data["day"]
    data = load()

    if time not in data["slots"].get(day, []) or not future(day, time):
        await q.edit_message_text(
            "Цей час уже недоступний. Почни запис ще раз через меню."
        )
        return ConversationHandler.END

    # На всякий случай проверяем, не занял ли кто-то это время.
    booked = next(
        (
            b for b in data["bookings"]
            if b["day"] == day and b["time"] == time
        ),
        None,
    )
    if booked:
        await q.edit_message_text("Цей час уже зайнятий.")
        return ConversationHandler.END

    name = context.user_data["name"]
    user = update.effective_user

    data["slots"][day].remove(time)
    if not data["slots"][day]:
        del data["slots"][day]

    # Убираем пересекающиеся свободные окна только теперь,
    # когда ученик действительно забронировал урок.
    remove_overlapping_slots(data, day, time)

    booking = {
        "id": uuid.uuid4().hex,
        "name": name,
        "day": day,
        "time": time,
        "user_id": user.id,
        "username": user.username or "",
        "created_at": now().isoformat(),
    }
    data["bookings"].append(booking)
    save(data)

    keyboard = [
        [InlineKeyboardButton(
            "📝 Записатися ще раз",
            callback_data="start_booking",
        )],
        [
            InlineKeyboardButton("❌ Скасувати урок", callback_data="cancel_lesson"),
            InlineKeyboardButton("🔄 Перенести урок", callback_data="reschedule_lesson"),
        ],
    ]

    await q.edit_message_text(
        f"✅ Готово!\n\n"
        f"Тебе записано:\n"
        f"👤 Ім'я: {name}\n"
        f"📅 Дата: {date_text(day)}\n"
        f"🕐 Час: {time}\n\n"
        "До зустрічі на уроці! 🌟",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    await tell_admin(
        context,
        f"🔔 Новий запис!\n\n"
        f"👤 {name}\n"
        f"📅 {day_name(day)}\n"
        f"🕐 {time}\n"
        f"Telegram: @{booking['username'] or 'немає'}\n"
        f"ID: {user.id}",
    )
    return ConversationHandler.END


# ================= ОТМЕНА УРОКА =================

async def cancel_start(update, context):
    data = load()
    bookings = upcoming_for_user(data, update.effective_user.id)

    if not bookings:
        await update.effective_message.reply_text(
            "У тебе немає майбутніх записів.",
            reply_markup=menu(),
        )
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(
            f"{day_name(b['day'])} — {b['time']}",
            callback_data=f"cancel:{b['id']}",
        )]
        for b in bookings
    ]
    await update.effective_message.reply_text(
        "Обери урок для скасування:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CANCEL_SELECT


async def cancel_callback(update, context):
    q = update.callback_query
    await q.answer()
    booking_id = q.data.split(":", 1)[1]
    data = load()

    booking = next(
        (
            b for b in data["bookings"]
            if b["id"] == booking_id
            and b["user_id"] == update.effective_user.id
            and future(b["day"], b["time"])
        ),
        None,
    )

    if not booking:
        await q.edit_message_text("Цей запис уже недоступний.")
        return ConversationHandler.END

    hours_left = (dt(booking["day"], booking["time"]) - now()).total_seconds() / 3600
    returned = False

    if hours_left >= CANCEL_HOURS:
        status, _ = add_slot(data, booking["day"], booking["time"])
        returned = status == "added"

    data["bookings"].remove(booking)
    save(data)

    await q.edit_message_text(
        f"❌ Урок скасовано.\n\n"
        f"{day_name(booking['day'])}, {booking['time']}\n\n"
        + (
            "Віконце повернулося у вільний розклад."
            if returned else
            "Віконце не повертається у вільний розклад."
        )
    )

    await tell_admin(
        context,
        f"❌ Учень скасував урок\n"
        f"👤 {booking['name']}\n"
        f"📅 {day_name(booking['day'])}\n"
        f"🕐 {booking['time']}\n"
        f"Повернено в розклад: {'так' if returned else 'ні'}",
    )
    return ConversationHandler.END


# ================= ПЕРЕНОС УРОКА =================

async def transfer_start(update, context):
    data = load()
    bookings = upcoming_for_user(data, update.effective_user.id)

    if not bookings:
        await update.effective_message.reply_text(
            "У тебе немає майбутніх записів.",
            reply_markup=menu(),
        )
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(
            f"{day_name(b['day'])} — {b['time']}",
            callback_data=f"transfer:{b['id']}",
        )]
        for b in bookings
    ]
    await update.effective_message.reply_text(
        "Обери урок для перенесення:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return TRANSFER_SELECT


async def transfer_select(update, context):
    q = update.callback_query
    await q.answer()
    booking_id = q.data.split(":", 1)[1]
    data = load()

    booking = next(
        (
            b for b in data["bookings"]
            if b["id"] == booking_id
            and b["user_id"] == update.effective_user.id
            and future(b["day"], b["time"])
        ),
        None,
    )
    if not booking:
        await q.edit_message_text("Цей запис уже недоступний.")
        return ConversationHandler.END

    context.user_data["transfer_id"] = booking_id
    days = sorted(data["slots"])

    if not days:
        await q.edit_message_text("Наразі немає вільних слотів.")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(day_name(d), callback_data=f"transferday:{d}")]
        for d in days
    ]
    await q.edit_message_text(
        "Обери новий день:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return TRANSFER_DAY


async def transfer_day(update, context):
    q = update.callback_query
    await q.answer()
    day = q.data.split(":", 1)[1]
    context.user_data["transfer_day"] = day

    times = [
        t for t in load()["slots"].get(day, [])
        if future(day, t)
    ]
    if not times:
        await q.edit_message_text("На цей день немає вільних віконець.")
        return ConversationHandler.END

    keyboard = []
    row = []
    for i, t in enumerate(sorted(times)):
        row.append(InlineKeyboardButton(t, callback_data=f"transfertime:{day}:{t}"))
        if len(row) == 3 or i == len(times) - 1:
            keyboard.append(row)
            row = []

    await q.edit_message_text(
        f"Обери новий час на {date_text(day)}:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return TRANSFER_TIME


async def transfer_time(update, context):
    q = update.callback_query
    await q.answer()
    _, day, new_time = q.data.split(":", 2)

    booking_id = context.user_data.get("transfer_id")
    user_id = update.effective_user.id
    data = load()

    booking = next(
        (
            b for b in data["bookings"]
            if b["id"] == booking_id
            and b["user_id"] == user_id
            and future(b["day"], b["time"])
        ),
        None,
    )
    if not booking:
        await q.edit_message_text("Цей запис уже недоступний.")
        return ConversationHandler.END

    if new_time not in data["slots"].get(day, []) or not future(day, new_time):
        await q.edit_message_text("Цей час уже недоступний. Почни перенесення ще раз.")
        return ConversationHandler.END

    old_day, old_time = booking["day"], booking["time"]
    hours_left = (dt(old_day, old_time) - now()).total_seconds() / 3600

    data["slots"][day].remove(new_time)
    if not data["slots"][day]:
        del data["slots"][day]

    remove_overlapping_slots(data, day, new_time)

    returned = False
    if hours_left >= CANCEL_HOURS:
        status, _ = add_slot(data, old_day, old_time)
        returned = status == "added"

    booking["day"] = day
    booking["time"] = new_time
    booking["rescheduled_at"] = now().isoformat()
    save(data)

    keyboard = [
        [InlineKeyboardButton("📝 Записатися ще раз", callback_data="start_booking")],
        [
            InlineKeyboardButton("❌ Скасувати урок", callback_data="cancel_lesson"),
            InlineKeyboardButton("🔄 Перенести урок", callback_data="reschedule_lesson"),
        ],
    ]

    await q.edit_message_text(
        f"✅ Урок перенесено!\n\n"
        f"📅 Нова дата: {date_text(day)}\n"
        f"🕐 Новий час: {new_time}\n\n"
        + (
            "Старе віконце повернулося у розклад."
            if returned else
            "Старе віконце не повертається у розклад."
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    await tell_admin(
        context,
        f"🔄 Учень переніс урок!\n\n"
        f"👤 {booking['name']}\n"
        f"Було: {day_name(old_day)}, {old_time}\n"
        f"Стало: {day_name(day)}, {new_time}\n"
        f"Старе віконце повернено: {'так' if returned else 'ні'}",
    )
    return ConversationHandler.END


# ================= АДМИНСКИЕ КОМАНДЫ =================

async def addslot(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Ця команда тільки для викладача.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Використання:\n"
            "/addslot РРРР-ММ-ДД ЧАС1 ЧАС2 ...\n\n"
            "Приклад:\n"
            "/addslot 2026-10-15 19:00 19:30 20:00"
        )
        return

    day, *times = context.args
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        await update.message.reply_text("Дата має бути у форматі РРРР-ММ-ДД.")
        return

    data = load()
    added = []
    messages = []

    for time in times:
        try:
            datetime.strptime(time, "%H:%M")
        except ValueError:
            messages.append(f"⚠️ {time}: неправильний формат часу.")
            continue

        status, booking = add_slot(data, day, time)

        if status == "added":
            added.append(time)
        elif status == "booked":
            messages.append(
                f"⛔ {time} уже заброньовано!\n"
                f"Учень: {booking['name']}"
            )
        elif status == "exists":
            messages.append(f"ℹ️ {time} уже є у вільному розкладі.")
        elif status == "past":
            messages.append(f"⚠️ {time} уже минув.")

    save(data)

    if added:
        messages.insert(
            0,
            f"✅ Додано на {day_name(day)}:\n"
            + "\n".join(f"• {t}" for t in added),
        )

    await update.message.reply_text(
        "\n\n".join(messages) if messages else "Нічого не додано."
    )


async def delslot(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Використання:\n"
            "/delslot РРРР-ММ-ДД ГГ:ХХ\n\n"
            "Приклад:\n"
            "/delslot 2026-10-15 19:30"
        )
        return

    day, time = context.args
    data = load()

    booking = next(
        (
            b for b in data["bookings"]
            if b["day"] == day and b["time"] == time
        ),
        None,
    )

    if booking:
        await update.message.reply_text(
            f"⛔ {time} уже заброньовано учнем {booking['name']}.\n"
            "Це запис, а не вільний слот. Він не був змінений."
        )
        return

    if time in data["slots"].get(day, []):
        data["slots"][day].remove(time)
        if not data["slots"][day]:
            del data["slots"][day]
        save(data)
        await update.message.reply_text(
            f"🗑️ Видалено віконце: {day_name(day)}, {time}."
        )
    else:
        await update.message.reply_text(
            "Такого вільного віконця немає."
        )


async def slots(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    data = load()
    if not data["slots"]:
        await update.message.reply_text("Вільних слотів немає.")
        return

    lines = ["📅 Вільні віконця:", ""]
    for day in sorted(data["slots"]):
        lines.append(f"📅 {day_name(day)}")
        lines.extend(f"   • {t}" for t in sorted(data["slots"][day]))

    await update.message.reply_text("\n".join(lines))


async def bookings(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    data = load()
    all_bookings = upcoming_all(data)

    if not all_bookings:
        await update.message.reply_text("Майбутніх записів немає.")
        return

    lines = ["📋 Майбутні записи:", ""]
    current_day = None

    for b in all_bookings:
        if b["day"] != current_day:
            current_day = b["day"]
            lines.append(f"📅 {day_name(current_day)}")
        lines.append(f"   • {b['time']} — {b['name']}")

    await update.message.reply_text("\n".join(lines))


async def clearday(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) != 1:
        await update.message.reply_text(
            "Використання: /clearday РРРР-ММ-ДД"
        )
        return

    day = context.args[0]
    data = load()

    if day in data["slots"]:
        del data["slots"][day]
        save(data)
        await update.message.reply_text(
            f"🗑️ Усі вільні віконця на {day_name(day)} видалено."
        )
    else:
        await update.message.reply_text("На цей день вільних слотів немає.")


async def cancel_command(update, context):
    await update.message.reply_text(
        "Дію скасовано.",
        reply_markup=menu(),
    )
    return ConversationHandler.END


# ================= ЗАПУСК БОТА =================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не знайдено. Додай його у Render → Environment."
        )

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("book", begin_booking),
            MessageHandler(filters.Regex("^📝 Записатися$"), begin_booking),
            CallbackQueryHandler(
                begin_booking,
                pattern="^start_booking$",
            ),
            CallbackQueryHandler(
                cancel_start,
                pattern="^cancel_lesson$",
            ),
            CallbackQueryHandler(
                transfer_start,
                pattern="^reschedule_lesson$",
            ),
        ],
        states={
            NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name),
            ],
            DAY: [
                CallbackQueryHandler(receive_day, pattern="^(day:|stop_booking$)"),
            ],
            TIME: [
                CallbackQueryHandler(receive_time, pattern="^(time:|stop_booking$)"),
                CallbackQueryHandler(back_days, pattern="^back_days$"),
            ],
            CANCEL_SELECT: [
                CallbackQueryHandler(cancel_callback, pattern="^cancel:"),
            ],
            TRANSFER_SELECT: [
                CallbackQueryHandler(transfer_select, pattern="^transfer:"),
            ],
            TRANSFER_DAY: [
                CallbackQueryHandler(transfer_day, pattern="^transferday:"),
            ],
            TRANSFER_TIME: [
                CallbackQueryHandler(transfer_time, pattern="^transfertime:"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
        ],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    app.add_handler(CommandHandler("addslot", addslot))
    app.add_handler(CommandHandler("delslot", delslot))
    app.add_handler(CommandHandler("slots", slots))
    app.add_handler(CommandHandler("bookings", bookings))
    app.add_handler(CommandHandler("clearday", clearday))

    print("Бот запущений...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
