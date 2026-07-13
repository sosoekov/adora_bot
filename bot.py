from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import random
import os

# 👇 добавили Flask
from flask import Flask
import threading

TOKEN = os.getenv("BOT_TOKEN")

# ================== WEB (для UptimeRobot) ==================

app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Bot is alive!"

def run_web():
    app_web.run(host="0.0.0.0", port=8080)

# запускаем веб-сервер в отдельном потоке
threading.Thread(target=run_web).start()

# ================== ЛОГИКА БОТА ==================

# хранение floor по чатам
floors = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот запущен!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "/r N — бросить N кубов\n"
        "/r N p M — переброс последних M\n"
        "/floor X — установить порог\n"
        "/floor new X — изменить порог\n"
        "/floor show — показать порог\n"
    )
    await update.message.reply_text(text)

async def floor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args

    if not args:
        await update.message.reply_text("Укажи число")
        return

    if args[0] == "show":
        value = floors.get(chat_id, 6)
        await update.message.reply_text(f"Текущий floor: {value}")
        return

    if args[0] == "new" and len(args) > 1:
        floors[chat_id] = int(args[1])
        await update.message.reply_text(f"Новый floor: {args[1]}")
        return

    floors[chat_id] = int(args[0])
    await update.message.reply_text(f"Floor установлен: {args[0]}")

async def r(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args

    if not args:
        await update.message.reply_text("Укажи количество кубов")
        return

    n = int(args[0])
    p = 0

    if len(args) >= 3 and args[1] == "p":
        p = int(args[2])

    floor_value = floors.get(chat_id, 6)

    # 🎲 проверка на парадокс
    check = random.randint(1, 10)
    paradox_text = f"Проверка на парадокс: {check}"

    if check <= floor_value:
        p += 1
        paradox_text += "\n+1 очко парадокса"

    rolls = [random.randint(1, 10) for _ in range(n)]

    # переброс
    if p > 0:
        lowest = sorted(range(len(rolls)), key=lambda i: rolls[i])[:p]
        for i in lowest:
            new_val = random.randint(1, 10)
            rolls[i] = f"_{new_val}_"

    result = " ".join(map(str, rolls))

    await update.message.reply_text(
        f"{paradox_text}\n🎲 Бросок: {result}"
    )

# ================== ЗАПУСК ==================

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("floor", floor))
app.add_handler(CommandHandler("r", r))

app.run_polling()
