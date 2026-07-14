from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)
import random
import os

# 👇 Flask для uptime
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
def calculate_successes(rolls):
    successes = 0
    tens_count = 0

    for x in rolls:
        if x >= 6:
            if x == 10:
                tens_count += 1
                if tens_count % 2 == 0:
                    successes += 3  # каждая вторая десятка
                else:
                    successes += 1
            else:
                successes += 1

    return successes


# ================== ЛОГИКА БРОСКА ==================
def roll_logic(n, p, rolls=None):
    if rolls is None:
        rolls = [random.randint(1, 10) for _ in range(n)]

    main = rolls[:n - p] if p else rolls
    paradox = rolls[n - p:] if p else []

    successes = calculate_successes(rolls)
    crit_fail = sum(1 for x in main if x == 1)
    crit_success = sum(1 for x in main if x == 10)

    text = f"🎲 Бросок: {' '.join(map(str, rolls))}\n\n"
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


# ================== /r ==================
async def r(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args

    if not args:
        n, p = 4, 0
    else:
        n = int(args[0])
        p = 0

        if len(args) >= 3 and args[1] == "p":
            p = int(args[2])

    # 🎲 проверка на парадокс
    check = random.randint(1, 10)
    paradox_text = f"Проверка на парадокс: {check}"

    context.user_data["paradox_text"] = paradox_text

    text, rolls = roll_logic(n, p)
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

    # 🔁 Повтор
    if data.startswith("repeat"):
        _, n, p = data.split("_")
        n, p = int(n), int(p)

        check = random.randint(1, 10)
        paradox_text = f"Проверка на парадокс: {check}"

        context.user_data["paradox_text"] = paradox_text

        text, rolls = roll_logic(n, p)
        full_text = f"{paradox_text}\n\n{text}"

        await query.edit_message_text(
            full_text,
            reply_markup=get_keyboard(n, p, rolls),
            parse_mode="HTML"
        )

    # 🧠 Переброс за WP
    elif data.startswith("wp"):
        _, rolls_str, p = data.split("_")
        p = int(p)

        rolls = list(map(int, rolls_str.split(",")))

        # достаём сохранённый парадокс
        paradox_text = context.user_data.get("paradox_text", "")

        # ✅ берём только значения < 6
        indexed = [(i, val) for i, val in enumerate(rolls) if val < 6]
        indexed.sort(key=lambda x: x[1])

        # максимум 3
        to_reroll = [i for i, _ in indexed[:3]]

        new_rolls = rolls.copy()
        replaced = set()

        for i in to_reroll:
            new_value = random.randint(1, 10)
            new_rolls[i] = new_value
            replaced.add(i)

        # форматирование
        display = []
        for i, val in enumerate(new_rolls):
            if i in replaced:
                display.append(f"<u>{val}</u>")
            else:
                display.append(str(val))

        n = len(new_rolls)

        main = new_rolls[:n - p] if p else new_rolls
        paradox = new_rolls[n - p:] if p else []

        successes = calculate_successes(new_rolls)
        crit_fail = sum(1 for x in main if x == 1)
        crit_success = sum(1 for x in main if x == 10)

        text = f"{paradox_text}\n\n"
        text += f"🎲 Бросок: {' '.join(display)}\n\n"
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
app.add_handler(CommandHandler("r", r))
app.add_handler(CallbackQueryHandler(button))

app.run_polling()
