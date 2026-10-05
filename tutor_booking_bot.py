#!/async def receive_time(update, context):
    from datetime import timedelta

    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("Запис скасовано.")
        return ConversationHandler.END

    if query.data == "back_to_days":
        return await back_to_days(update, context)

    time_str = query.data.replace("time_", "")
    name = context.user_data.get("name", "Без імені")
    day = context.user_data.get("day")

    data = load_data()

    if day not in data["slots"] or time_str not in data["slots"][day]:
        await query.edit_message_text(
            "😔 На жаль, цей час щойно зайняли.\n"
            "Спробуй обрати інший."
        )
        return ConversationHandler.END

    # Забираємо вибраний слот
    data["slots"][day].remove(time_str)

    # Визначаємо час початку та кінця 60-хвилинного уроку
    selected_time = datetime.strptime(time_str, "%H:%M")
    lesson_end = selected_time + timedelta(minutes=60)

    # Залишаємо тільки ті слоти, які не перетинаються з уроком
    remaining_times = []

    for slot in data["slots"][day]:
        slot_time = datetime.strptime(slot, "%H:%M")
        slot_end = slot_time + timedelta(minutes=60)

        if slot_time >= lesson_end or slot_end <= selected_time:
            remaining_times.append(slot)

    data["slots"][day] = remaining_times

    if not data["slots"][day]:
        del data["slots"][day]

    # Створюємо запис
    booking = {
        "name": name,
        "day": day,
        "time": time_str,
        "user_id": update.effective_user.id,
        "username": update.effective_user.username or "",
        "created_at": datetime.now().isoformat()
    }

    data["bookings"].append(booking)

    save_data(data)

    nice_day = format_date(day)

    # Підтвердження учню
    await query.edit_message_text(
        f"✅ Готово!\n\n"
        f"Тебе записано:\n"
        f"👤 Ім'я: {name}\n"
        f"📅 Дата: {nice_day}\n"
        f"🕐 Час: {time_str}\n\n"
        f"До зустрічі на уроці! 🌟"
    )

    # Повідомлення адміну
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
            f"Не вдалося надіслати повідомлення адміну: {e}"
        )

    return ConversationHandler.END
