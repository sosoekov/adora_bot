from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)
import random
import os

from flask import Flask
import threading

TOKEN = os.getenv("BOT_TOKEN")

# ================== WEB ==================
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Bot is alive!"

def run_web():
    app_web.run(host="0.0.0.0", port=8080)

threading.Thread(target=run_web).start()


# ================== КНОПКИ ==================
def get_keyboard(n, p, rolls):
    rolls_str = ",".join(map(str, rolls))

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 Повтор", callback_data=f"repeat_{n}_{p}")],
        [InlineKeyboardButton("🧠 Переброс за WP", callback_data=f"wp_{rolls_str}_{p}")]
    ])


# ================== УСПЕХИ ==================
def calculate_successes(rolls, floor):
    successes = sum(1 for x in rolls if x >= floor)

    tens = sum(1 for x in rolls if x == 10)

    # каждая вторая десятка = +2 успеха (итого 3)
    successes += (tens // 2) * 2

    return successes


# ================== ЛОГИКА БРОСКА ==================
def roll_logic(n, p, floor, rolls=None):
    if rolls is None:
        rolls = [random.randint(1, 10) for _ in range(n)]

    main = rolls[:n - p] if p else rolls
    paradox = rolls[n - p:] if p else []

    successes = calculate_successes(rolls, floor)
    crit_fail = sum(1 for x in main if x == 1)
    crit_success = sum(1 for x in main if x == 10)

    text = f"🎲 Бросок: {' '.join(map(str, rolls))}\n"
    text += f"🎯 Порог: {floor}\n\n"
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


# ================== /start ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Используй /r 4 или /r 6 p 2")


# ================== /help ==================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 Доступные команды:\n\n"
        "/r N — бросок N кубов\n"
        "/r N p X — X кубов идут в парадокс\n\n"
        "/floor ЧИСЛО — установить порог\n"
        "/floor new ЧИСЛО — изменить порог\n"
        "/floor show — показать текущий порог\n\n"
        "🔁 Повтор — переброс\n"
        "🧠 WP — переброс до 3 значений < порога"
    )
    await update.message.reply_text(text)


# ================== /floor ==================
async def floor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    chat_id = update.effective_chat.id

    if not args:
        await update.message.reply_text("Используй /floor 6 или /floor show")
        return

    if args[0] == "show":
        floor = context.application.chat_data.get(chat_id, {}).get("floor", 6)
        await update.message.reply_text(f"Текущий порог: {floor}")
        return

    if args[0] == "new" and len(args) >= 2:
        value = int(args[1])
    else:
        value = int(args[0])

    context.application.chat_data.setdefault(chat_id, {})["floor"] = value

    await update.message.reply_text(f"Порог установлен: {value}")


# ================== /r ==================
async def r(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    chat_id = update.effective_chat.id

    floor = context.application.chat_data.get(chat_id, {}).get("floor", 6)

    if not args:
        n, p = 4, 0
    else:
        n = int(args[0])
        p = 0

        if len(args) >= 3 and args[1] == "p":
            p = int(args[2])

    check = random.randint(1, 10)
    paradox_text = f"Проверка на парадокс: {check}"
    context.user_data["paradox_text"] = paradox_text

    text, rolls = roll_logic(n, p, floor)
    full_text = f"{paradox_text}\n\n{text}"

    await update.message.reply_text(
        full_text,
        reply_markup=get_keyboard(n, p, rolls),
        parse_mode="HTML"
    )


# ================== КНОПКИ ==================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    chat_id = query.message.chat.id

    floor = context.application.chat_data.get(chat_id, {}).get("floor", 6)

    # 🔁 Повтор
    if data.startswith("repeat"):
        _, n, p = data.split("_")
        n, p = int(n), int(p)

        check = random.randint(1, 10)
        paradox_text = f"Проверка на парадокс: {check}"
        context.user_data["paradox_text"] = paradox_text

        text, rolls = roll_logic(n, p, floor)
        full_text = f"{paradox_text}\n\n{text}"

        await query.edit_message_text(
            full_text,
            reply_markup=get_keyboard(n, p, rolls),
            parse_mode="HTML"
        )

    # 🧠 WP
    elif data.startswith("wp"):
        _, rolls_str, p = data.split("_")
        p = int(p)

        rolls = list(map(int, rolls_str.split(",")))
        paradox_text = context.user_data.get("paradox_text", "")

        # берём только < floor
        indexed = [(i, v) for i, v in enumerate(rolls) if v < floor]
        indexed.sort(key=lambda x: x[1])

        to_reroll = [i for i, _ in indexed[:3]]

        new_rolls = rolls.copy()
        replaced = set()

        for i in to_reroll:
            new_rolls[i] = random.randint(1, 10)
            replaced.add(i)

        display = [
            f"<u>{v}</u>" if i in replaced else str(v)
            for i, v in enumerate(new_rolls)
        ]

        n = len(new_rolls)

        main = new_rolls[:n - p] if p else new_rolls
        paradox = new_rolls[n - p:] if p else []

        successes = calculate_successes(new_rolls, floor)
        crit_fail = sum(1 for x in main if x == 1)
        crit_success = sum(1 for x in main if x == 10)

        text = f"{paradox_text}\n\n"
        text += f"🎲 Бросок: {' '.join(display)}\n"
        text += f"🎯 Порог: {floor}\n\n"
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

        await query.edit_message_text(
            text,
            reply_markup=get_keyboard(n, p, new_rolls),
            parse_mode="HTML"
        )


# ================== ЗАПУСК ==================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("r", r))
app.add_handler(CommandHandler("floor", floor_command))
app.add_handler(CallbackQueryHandler(button))

app.run_polling()
