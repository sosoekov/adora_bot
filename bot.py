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

import storage

TOKEN = os.getenv("BOT_TOKEN")

# ================== WEB ==================
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Bot is alive!"

def run_web():
    app_web.run(host="0.0.0.0", port=8080)


# ================== ПРАВИЛА ==================
DIE_MIN, DIE_MAX = 1, 10

# Успех — значение 6 и выше. Порог успеха фиксированный и с floor не связан:
# floor отвечает только за предбросковую проверку на парадокс.
SUCCESS_THRESHOLD = 6

# Сколько кубов перебрасывается за одно использование Силы воли.
# Нажать можно один раз на бросок, после этого кнопка пропадает.
WP_REROLL = 3

# «Любой ценой»: переброс всех кубов значениями от 6 до 10 ценой обнуления
# парадоксов и Силы воли. Шанс появления кнопки копится: стартует с
# TREAT_CHANCE_START, каждый бросок без неё поднимает его на шаг до потолка,
# появление сбрасывает обратно к старту. Шанс свой у каждого игрока.
TREAT_DIE_MIN = 6
TREAT_CHANCE_STEP = 1
TREAT_CHANCE_MAX = 50

DEFAULT_DICE = 4

# Потолок пула. Значения кубов уезжают в callback_data кнопки переброса, а там
# у Telegram лимит 64 байта: 40 кубов дают "wp_" + 40 символов + "_40" = 46.
MAX_DICE = 40


def roll_dice(n):
    return [random.randint(DIE_MIN, DIE_MAX) for _ in range(n)]


def count_tens(rolls):
    return sum(1 for x in rolls if x == DIE_MAX)


def calculate_successes(rolls):
    """Успех — значение 6 и выше.

    Каждая пара десяток добавляет ещё +2 сверх обычного счёта: две десятки
    складываются в 4 успеха, четыре — в 8. Парадоксные кубы считаются наравне
    с обычными.
    """
    successes = sum(1 for x in rolls if x >= SUCCESS_THRESHOLD)
    successes += (count_tens(rolls) // 2) * 2
    return successes


def count_paradox_crits(paradox_dice):
    """Крит на кубе парадокса — и 1, и 10. Любой из них запускает прорыв."""
    return sum(1 for x in paradox_dice if x in (DIE_MIN, DIE_MAX))


def split_pool(rolls, paradox_count):
    """Обычные кубы и парадоксные: парадоксные подменяют последние в ряду."""
    edge = len(rolls) - paradox_count
    return rolls[:edge], rolls[edge:]


def resolve_breakthrough(bt_rolls, paradox_before):
    """1-5 — ничего; 6-9 — +1 тяжёлого урона и -1 парадокс; 10 — -1 парадокс.

    Каскада нет: 1 и 10 на кубах прорыва нового прорыва не вызывают. Снять
    больше парадоксов, чем есть у игрока, нельзя — пол 0. Тяжёлый урон нигде
    не накапливается, он только выводится числом за этот бросок.
    """
    damage = sum(1 for x in bt_rolls if 6 <= x <= 9)
    hits = sum(1 for x in bt_rolls if x >= 6)
    removed = min(hits, paradox_before)
    return damage, removed, paradox_before - removed


def touch_user(chat_id, user):
    """Запомнить, как показывать игрока в /showchance.

    Telegram не отдаёт список участников чата по запросу, поэтому имя
    приходится ловить в момент обращения к боту и обновлять при следующем.
    """
    name = user.first_name or user.username or f"Игрок {user.id}"
    storage.remember_name(chat_id, user.id, name)


def roll_treat_button(chat_id, user_id):
    """Выпала ли игроку кнопка «любой ценой». Обновляет накопленный шанс."""
    chance = storage.get_treat_chance(chat_id, user_id)
    appeared = random.randint(1, 100) <= chance

    storage.set_treat_chance(
        chat_id,
        user_id,
        storage.TREAT_CHANCE_START if appeared
        else min(chance + TREAT_CHANCE_STEP, TREAT_CHANCE_MAX),
    )
    return appeared


def wp_candidates(rolls, paradox_count):
    """Индексы кубов под переброс за волю — три наименьших.

    Успехи не перебрасываются, парадоксные кубы не перебрасываются. Если
    подходящих меньше трёх, перебрасываются все, какие есть.
    """
    main, _ = split_pool(rolls, paradox_count)
    candidates = [(i, v) for i, v in enumerate(main) if v < SUCCESS_THRESHOLD]
    candidates.sort(key=lambda pair: pair[1])
    return [i for i, _ in candidates[:WP_REROLL]]


# ================== РАЗБОР АРГУМЕНТОВ ==================
def parse_roll_args(args):
    """/r M p N -> (M, N). N = None, если игрок не писал "p N".

    Кидает ValueError с готовым текстом причины.
    """
    if not args:
        return DEFAULT_DICE, None

    try:
        dice_count = int(args[0])
    except ValueError:
        raise ValueError(
            f"Количество кубов должно быть числом. Получено: «{args[0]}»"
        )
    if not 1 <= dice_count <= MAX_DICE:
        raise ValueError(
            f"Количество кубов должно быть от 1 до {MAX_DICE}. "
            f"Получено: {dice_count}"
        )

    if len(args) == 1:
        return dice_count, None

    if args[1] != "p":
        raise ValueError(
            f"Не понял «{' '.join(args[1:])}». Формат: /r {dice_count} p 2"
        )
    if len(args) < 3:
        raise ValueError(
            f"После p нужно число парадоксов. Например: /r {dice_count} p 2"
        )

    try:
        paradox = int(args[2])
    except ValueError:
        raise ValueError(
            f"Количество парадоксов должно быть числом. Получено: «{args[2]}»"
        )
    if paradox < 0:
        raise ValueError(
            f"Количество парадоксов не может быть отрицательным. "
            f"Получено: {paradox}"
        )
    if paradox > dice_count:
        raise ValueError(
            f"Парадоксов ({paradox}) больше, чем кубов ({dice_count})."
        )
    return dice_count, paradox


# ================== БРОСОК ==================
def perform_roll(chat_id, user_id, dice_count, declared_paradox):
    """Проверка на парадокс, основной бросок и, если надо, прорыв.

    Счётчик парадоксов игрока устанавливается равным итоговому числу
    парадоксных кубов, а прорыв затем списывает с него снятые.
    """
    floor = storage.get_floor(chat_id)

    # Явный "p N" затирает накопленное значение, иначе берём сохранённое.
    if declared_paradox is None:
        paradox_count = storage.get_paradox(chat_id, user_id)
    else:
        paradox_count = declared_paradox

    check = random.randint(DIE_MIN, DIE_MAX)
    check_passed = check > floor
    if not check_passed:
        paradox_count += 1

    # Парадоксные кубы подменяют последние в ряду, поэтому их не может быть
    # больше, чем кубов в броске.
    paradox_count = min(paradox_count, dice_count)
    storage.set_paradox(chat_id, user_id, paradox_count)

    rolls = roll_dice(dice_count)
    _, paradox_dice = split_pool(rolls, paradox_count)

    breakthrough = ""
    if count_paradox_crits(paradox_dice):
        bt_rolls = roll_dice(paradox_count)
        damage, removed, left = resolve_breakthrough(bt_rolls, paradox_count)
        storage.set_paradox(chat_id, user_id, left)
        breakthrough = (
            f" 🔥 Результат прорыва: {' '.join(map(str, bt_rolls))}. "
            f"Урон {damage}, снято парадоксов {removed} (осталось {left})"
        )

    return check, check_passed, rolls, paradox_count, breakthrough


# ================== ВЫВОД ==================
def format_pool(rolls, paradox_count, replaced=()):
    """Ряд кубов; парадоксные — последние в ряду — берутся в квадратные скобки."""
    def cell(i):
        return f"<u>{rolls[i]}</u>" if i in replaced else str(rolls[i])

    edge = len(rolls) - paradox_count
    parts = []
    if edge > 0:
        parts.append(" ".join(cell(i) for i in range(edge)))
    if paradox_count > 0:
        parts.append("[" + " ".join(cell(i) for i in range(edge, len(rolls))) + "]")
    return " ".join(parts)


def render_roll(check, check_passed, rolls, paradox_count, breakthrough,
                willpower, replaced=(), treat_note=""):
    _, paradox_dice = split_pool(rolls, paradox_count)

    verdict = "успех" if check_passed else "провал"
    text = f"Проверка на парадокс: {check} ({verdict})\n\n"
    text += f"🎲 Бросок: {format_pool(rolls, paradox_count, replaced)}\n\n"
    text += f"✔️ Успехи: {calculate_successes(rolls)}\n"

    tens = count_tens(rolls)
    if tens:
        text += f"✨ Крит.успехи: {tens}\n"

    crits = count_paradox_crits(paradox_dice)
    if crits:
        text += f"⚡️ Крит на кубе парадокса: {crits} (прорыв)\n"
    if breakthrough:
        text += f"{breakthrough}\n"

    text += f"🧠 Использовано воли: {willpower}\n"
    if treat_note:
        text += f"{treat_note}\n"

    text += "\n────────────\nВыбери действие:"
    return text


# ================== КНОПКИ ==================
# Кубы в callback_data кодируются одним символом на куб: у Telegram там лимит
# 64 байта, а "10" заняло бы два символа плюс разделители.
_DIE_CHARS = "123456789A"


def encode_rolls(rolls):
    return "".join(_DIE_CHARS[v - 1] for v in rolls)


def decode_rolls(text):
    return [_DIE_CHARS.index(c) + 1 for c in text]


def get_keyboard(dice_count, declared_paradox, rolls, paradox_count,
                 wp_used=False, treat=False):
    rows = []

    # Кнопка повтора отключена. Чтобы вернуть — раскомментировать этот блок,
    # обработчик "repeat_" в button() остался на месте.
    # declared = "-" if declared_paradox is None else declared_paradox
    # rows.append([
    #     InlineKeyboardButton(
    #         "🔁 Повтор", callback_data=f"repeat_{dice_count}_{declared}"
    #     )
    # ])

    # Переброс за волю даётся один раз на бросок. Плюс прятать кнопку, когда
    # перебрасывать нечего: все обычные кубы уже успехи либо их нет вовсе.
    if not wp_used and wp_candidates(rolls, paradox_count):
        rows.append([
            InlineKeyboardButton(
                "🧠 Переброс за WP",
                callback_data=(
                    f"wp_{encode_rolls(rolls)}_{paradox_count}"
                    f"_{1 if treat else 0}"
                ),
            )
        ])

    if treat:
        rows.append([
            InlineKeyboardButton(
                "🧁 любой ценой (бесплатно)",
                callback_data=f"treat_{dice_count}",
            )
        ])

    rows.append([
        InlineKeyboardButton("♻️ Сбросить волю", callback_data="wpreset")
    ])
    return InlineKeyboardMarkup(rows)


def remember_roll(context, check, check_passed, breakthrough):
    """Проверка и прорыв не влезают в callback_data, держим их в user_data.

    Парадоксные кубы за волю не перебрасываются, поэтому результат прорыва
    после переброса не меняется — его достаточно показать тот же самый.
    """
    context.user_data["check"] = (check, check_passed)
    context.user_data["breakthrough"] = breakthrough


# ================== /start ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Используй /r 5 или /r 5 p 2, где 5 — кубы, 2 — парадоксы.\n"
        "Подробности: /help"
    )


# ================== /help ==================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 Доступные команды:\n\n"
        "/r M — бросок M кубов, парадоксы берутся из накопленного счётчика\n"
        "/r M p N — бросок M кубов, из них N парадоксных;\n"
        "   N перезаписывает накопленный счётчик\n"
        "/paradox — показать свои счётчики\n"
        "/showchance — шансы кнопки «любой ценой» у всех в этом чате\n\n"
        "/floor ЧИСЛО — порог проверки на парадокс\n"
        "/floor new ЧИСЛО — изменить порог\n"
        "/floor show — показать текущий порог\n\n"
        "⚙️ Как считается:\n"
        "• успех — значение 6 и выше;\n"
        "   каждая пара десяток даёт ещё +2 успеха сверху\n"
        "• перед броском идёт проверка на парадокс: выше порога — успех,\n"
        "   порог и ниже — провал и +1 парадоксный куб\n"
        "• парадоксные кубы стоят в конце ряда в квадратных скобках\n"
        "   и считаются в успехи наравне с обычными\n"
        "• 1 или 10 на парадоксном кубе — прорыв: там 6-9 дают тяжёлый урон\n"
        "   и снимают парадокс, 10 снимает парадокс без урона\n\n"
        "🧠 Переброс за WP — переброс 3 наименьших кубов, один раз на бросок;\n"
        "   успехи и парадоксные кубы не перебрасываются\n"
        "🧁 Любой ценой — выпадает случайно: перебрасывает все кубы\n"
        "   значениями от 6 до 10, но обнуляет парадоксы и Силу воли\n"
        "♻️ Сбросить волю — обнулить свой счётчик Силы воли"
    )
    await update.message.reply_text(text)


# ================== /paradox ==================
async def paradox_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.message.from_user.id
    touch_user(chat_id, update.message.from_user)

    paradox, willpower = storage.get_counters(chat_id, user_id)
    await update.message.reply_text(
        f"⚡️ Парадоксов: {paradox}\n"
        f"🧠 Использовано воли: {willpower}"
    )


# ================== /showchance ==================
async def showchance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    touch_user(chat_id, update.message.from_user)

    rows = storage.list_treat_chances(chat_id)
    if not rows:
        await update.message.reply_text(
            "🧁 Шанс кнопки «любой ценой»\n\n"
            "В этом чате ещё никто не обращался к боту."
        )
        return

    lines = ["🧁 Шанс кнопки «любой ценой»:", ""]
    for name, user_id, chance in rows:
        lines.append(f"{name or f'Игрок {user_id}'} — {chance}%")

    await update.message.reply_text("\n".join(lines))


# ================== /floor ==================
async def floor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    chat_id = update.effective_chat.id

    if not args:
        await update.message.reply_text("Используй /floor 6 или /floor show")
        return

    if args[0] == "show":
        floor = storage.get_floor(chat_id)
        await update.message.reply_text(f"Текущий порог: {floor}")
        return

    if args[0] == "new":
        if len(args) < 2:
            await update.message.reply_text(
                "Ошибка изменения порога.\n"
                "После /floor new нужно число. Например: /floor new 6"
            )
            return
        raw = args[1]
    else:
        raw = args[0]

    try:
        value = int(raw)
    except ValueError:
        await update.message.reply_text(
            "Ошибка изменения порога.\n"
            f"Порог должен быть целым числом от {storage.FLOOR_MIN} "
            f"до {storage.FLOOR_MAX}. Получено: «{raw}»"
        )
        return

    if not storage.FLOOR_MIN <= value <= storage.FLOOR_MAX:
        await update.message.reply_text(
            "Ошибка изменения порога.\n"
            f"Порог должен быть от {storage.FLOOR_MIN} до {storage.FLOOR_MAX}. "
            f"Получено: {value}"
        )
        return

    # старое значение читаем ДО записи, иначе покажем новое дважды
    old_floor = storage.get_floor(chat_id)
    storage.set_floor(chat_id, value)

    await update.message.reply_text(
        "Порог успешно изменен.\n"
        f"Старый порог: {old_floor}\n"
        f"Новый порог: {value}"
    )


# ================== /r ==================
async def r(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.message.from_user.id
    touch_user(chat_id, update.message.from_user)

    try:
        dice_count, declared_paradox = parse_roll_args(context.args)
    except ValueError as err:
        await update.message.reply_text(f"Ошибка броска.\n{err}")
        return

    check, check_passed, rolls, paradox_count, breakthrough = perform_roll(
        chat_id, user_id, dice_count, declared_paradox
    )
    remember_roll(context, check, check_passed, breakthrough)

    _, willpower = storage.get_counters(chat_id, user_id)
    treat = roll_treat_button(chat_id, user_id)

    await update.message.reply_text(
        render_roll(check, check_passed, rolls, paradox_count,
                    breakthrough, willpower),
        reply_markup=get_keyboard(dice_count, declared_paradox,
                                  rolls, paradox_count, treat=treat),
        parse_mode="HTML",
    )


# ================== КНОПКИ ==================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    chat_id = query.message.chat.id
    user_id = query.from_user.id
    touch_user(chat_id, query.from_user)

    # ♻️ Сброс Силы воли — только свой счётчик, чужие не трогаются.
    if data == "wpreset":
        storage.reset_willpower(chat_id, user_id)
        name = query.from_user.first_name or query.from_user.username or "Игрок"
        await query.message.reply_text(f"{name} сбросил счетчик Силы воли")
        return

    # 🔁 Повтор — тот же бросок заново, вместе с проверкой на парадокс.
    if data.startswith("repeat_"):
        _, dice_raw, declared_raw = data.split("_")
        dice_count = int(dice_raw)
        declared_paradox = None if declared_raw == "-" else int(declared_raw)

        check, check_passed, rolls, paradox_count, breakthrough = perform_roll(
            chat_id, user_id, dice_count, declared_paradox
        )
        remember_roll(context, check, check_passed, breakthrough)

        _, willpower = storage.get_counters(chat_id, user_id)
        treat = roll_treat_button(chat_id, user_id)

        await query.edit_message_text(
            render_roll(check, check_passed, rolls, paradox_count,
                        breakthrough, willpower),
            reply_markup=get_keyboard(dice_count, declared_paradox,
                                      rolls, paradox_count, treat=treat),
            parse_mode="HTML",
        )
        return

    # 🧁 Любой ценой — переброс всех кубов значениями 6-10, ценой обнуления
    # парадоксов и Силы воли.
    if data.startswith("treat_"):
        dice_count = int(data.split("_")[1])

        storage.reset_paradox_and_willpower(chat_id, user_id)
        new_rolls = [random.randint(TREAT_DIE_MIN, DIE_MAX)
                     for _ in range(dice_count)]

        # Парадоксов больше нет, значит нет ни парадоксных кубов, ни прорыва.
        check, check_passed = context.user_data.get("check", (0, True))
        context.user_data["breakthrough"] = ""

        await query.edit_message_text(
            render_roll(
                check, check_passed, new_rolls, 0, "", 0,
                treat_note="🧁 Любой ценой: все кубы переброшены, "
                           "парадоксы и воля обнулены",
            ),
            reply_markup=get_keyboard(dice_count, None, new_rolls, 0,
                                      wp_used=True),
            parse_mode="HTML",
        )
        return

    # 🧠 Переброс за WP
    if data.startswith("wp_"):
        parts = data.split("_")
        rolls = decode_rolls(parts[1])
        paradox_count = int(parts[2])
        # Хвост с флагом кнопки «любой ценой» появился позже, поэтому у
        # сообщений, отправленных до обновления, его может не быть.
        treat = len(parts) > 3 and parts[3] == "1"

        to_reroll = wp_candidates(rolls, paradox_count)
        if not to_reroll:
            return

        new_rolls = rolls.copy()
        for i in to_reroll:
            new_rolls[i] = random.randint(DIE_MIN, DIE_MAX)
        replaced = set(to_reroll)

        willpower = storage.add_willpower(chat_id, user_id)

        check, check_passed = context.user_data.get("check", (0, True))
        breakthrough = context.user_data.get("breakthrough", "")

        await query.edit_message_text(
            render_roll(check, check_passed, new_rolls, paradox_count,
                        breakthrough, willpower, replaced),
            # Переброс за волю одноразовый: кнопка больше не выводится.
            reply_markup=get_keyboard(len(new_rolls), paradox_count,
                                      new_rolls, paradox_count,
                                      wp_used=True, treat=treat),
            parse_mode="HTML",
        )


# ================== ЗАПУСК ==================
def main():
    storage.init_db()

    threading.Thread(target=run_web).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("r", r))
    app.add_handler(CommandHandler("paradox", paradox_command))
    app.add_handler(CommandHandler("showchance", showchance_command))
    app.add_handler(CommandHandler("floor", floor_command))
    app.add_handler(CallbackQueryHandler(button))

    app.run_polling()


if __name__ == "__main__":
    main()
