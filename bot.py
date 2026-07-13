from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)

import os

TOKEN = os.getenv("BOT_TOKEN")

# ===== КНОПКИ =====
def get_keyboard(n, p, rolls):
    rolls_str = ",".join(map(str, rolls))

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 Повтор", callback_data=f"repeat_{n}_{p}")],
        [InlineKeyboardButton("🧠 Переброс за WP", callback_data=f"wp_{rolls_str}_{p}")]
    ])


# ===== ЛОГИКА БРОСКА =====
def roll_logic(n, p, rolls=None, display_override=None):
    if rolls is None:
        rolls = [random.randint(1, 10) for _ in range(n)]

    main = rolls[:n - p] if p else rolls
    paradox = rolls[n - p:] if p else []

    successes = sum(1 for x in rolls if x >= 6)
    crit_fail = sum(1 for x in main if x == 1)
    crit_success = sum(1 for x in main if x == 10)

    if display_override:
        display = display_override
    else:
        display = list(map(str, rolls))

    text = f"🎲 Бросок: {' '.join(display)}\n\n"
    text += f"✔ Успехи: {successes}\n"

    if crit_fail:
        text += f"💀 Крит.провалы: {crit_fail}\n"
    if crit_success:
        text += f"✨ Крит.успехи: {crit_success}\n"

    if p > 0:
        paradox_hits = sum(1 for x in paradox if x in (1, 10))
        if paradox_hits:
            text += f"⚡ Парадокс: {paradox_hits}\n"

    text += "\n────────────\nВыбери действие:"

    return text, rolls


# ===== /help =====
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Доступные команды:\n\n"
        "/r N — бросить N кубов\n"
        "/r N p X — X кубов идут в парадокс\n\n"
        "/floor N — установить порог\n"
        "/floor new N — изменить порог\n"
        "/floor show — показать текущий\n\n"
        "Кнопки:\n"
        "🔁 Повтор — новый бросок\n"
        "🧠 Переброс за WP — переброс 3 худших кубов"
    )

    await update.message.reply_text(text)


# ===== /floor =====
async def floor_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args

    if not args:
        await update.message.reply_text("Используй: /floor 3 или /floor show")
        return

    if args[0] == "show":
        floor = context.chat_data.get("floor", 0)
        await update.message.reply_text(f"Текущий floor: {floor}")
        return

    if args[0] == "new" and len(args) >= 2:
        value = int(args[1])
        context.chat_data["floor"] = value
        await update.message.reply_text(f"Новый floor установлен: {value}")
        return

    # обычная установка
    value = int(args[0])
    context.chat_data["floor"] = value
    await update.message.reply_text(f"Floor установлен: {value}")


# ===== /r =====
async def r(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args

    if not args:
        n, p = 4, 0
    else:
        n = int(args[0])
        p = 0

        if len(args) >= 3 and args[1] == "p":
            p = int(args[2])

    # ===== ПРОВЕРКА ПАРАДОКСА =====
    floor = context.chat_data.get("floor", 0)
    check = random.randint(1, 10)

    paradox_text = f"Проверка на парадокс: {check}\n"

    if check <= floor:
        p += 1
        paradox_text += "+1 очко парадокса\n\n"
    else:
        paradox_text += "\n"

    text, rolls = roll_logic(n, p)
    full_text = paradox_text + text

    await update.message.reply_text(
        full_text,
        reply_markup=get_keyboard(n, p, rolls),
        parse_mode="HTML"
    )


# ===== КНОПКИ =====
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    # 🔁 Повтор
    if data.startswith("repeat"):
        _, n, p = data.split("_")
        n, p = int(n), int(p)

        text, rolls = roll_logic(n, p)

        await query.edit_message_text(
            text,
            reply_markup=get_keyboard(n, p, rolls),
            parse_mode="HTML"
        )

    # 🧠 WP
    elif data.startswith("wp"):
        _, rolls_str, p = data.split("_")
        p = int(p)

        rolls = list(map(int, rolls_str.split(",")))

        indexed = list(enumerate(rolls))
        indexed.sort(key=lambda x: x[1])
        to_reroll = [i for i, _ in indexed[:3]]

        new_rolls = rolls.copy()
        replaced = set()

        for i in to_reroll:
            new_rolls[i] = random.randint(1, 10)
            replaced.add(i)

        display = []
        for i, val in enumerate(new_rolls):
            if i in replaced:
                display.append(f"<u>{val}</u>")
            else:
                display.append(str(val))

        text, _ = roll_logic(len(new_rolls), p, new_rolls, display)

        await query.edit_message_text(
            text,
            reply_markup=get_keyboard(len(new_rolls), p, new_rolls),
            parse_mode="HTML"
        )


# ===== ЗАПУСК =====
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(CommandHandler("floor", floor_cmd))
app.add_handler(CommandHandler("r", r))
app.add_handler(CallbackQueryHandler(button))

app.run_polling()
