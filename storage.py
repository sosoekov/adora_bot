"""Персистентное хранилище бота (SQLite).

Почему появился этот модуль
---------------------------
Раньше порог лежал в ``Application.chat_data``. В python-telegram-bot 20.x это
read-only ``MappingProxyType``, поэтому запись вида
``application.chat_data.setdefault(chat_id, {})["floor"] = value`` падала с
``AttributeError`` ещё до ответа пользователю — порог не сохранялся никогда.
И даже если бы запись работала, ``chat_data`` живёт в памяти процесса и
теряется при рестарте.

Схема
-----
    chat_settings(
        chat_id INTEGER PRIMARY KEY,
        floor   INTEGER NOT NULL
    )

    user_counters(
        chat_id      INTEGER NOT NULL,
        user_id      INTEGER NOT NULL,
        paradox      INTEGER NOT NULL DEFAULT 0,
        willpower    INTEGER NOT NULL DEFAULT 0,
        treat_chance INTEGER NOT NULL DEFAULT 5,
        PRIMARY KEY (chat_id, user_id)
    )

Счётчики живут отдельно для каждого игрока в каждом чате, поэтому в общем чате
у всех свои значения.

Миграции
--------
``chat_settings`` не менялась с момента появления. ``user_counters`` создаётся
через ``CREATE TABLE IF NOT EXISTS``, а колонка ``treat_chance`` добавляется в
неё через ``ALTER TABLE ADD COLUMN`` в :func:`_migrate` — см. комментарий там.
Обе операции ничего не удаляют: пороги и накопленные счётчики остаются на
месте, у уже заведённых игроков просто появляется новое поле со стартовым
значением.
"""

import os
import sqlite3
from contextlib import contextmanager

# Путь к файлу БД. Вынесен в переменную окружения, потому что в .replit стоит
# deploymentTarget = "autoscale" — там локальный диск эфемерный, и путь нужно
# уметь направить на постоянный том.
DB_PATH = os.getenv("DB_PATH", "bot_data.sqlite3")

# Порог бросается по d10, значения вне 1..10 смысла не имеют.
FLOOR_MIN = 1
FLOOR_MAX = 10

# Порог предбросковой проверки на парадокс: проверка проваливается на
# значениях <= floor, то есть при 2 парадокс приходит примерно в 20% бросков.
DEFAULT_FLOOR = 2

# Стартовый шанс появления кнопки «любой ценой», в процентах. Он же значение
# по умолчанию для колонки treat_chance.
TREAT_CHANCE_START = 5


@contextmanager
def _db():
    """Короткоживущее соединение на одну операцию.

    Соединение открывается и закрывается на каждый вызов намеренно: бот держит
    Flask в отдельном потоке, а sqlite3-соединения по умолчанию не шарятся между
    потоками. Объём операций тут — единицы в минуту, цена открытия ничтожна.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:  # commit при успехе, rollback при исключении
            yield conn
    finally:
        conn.close()


def init_db():
    """Создать схему, если её ещё нет. Идемпотентно."""
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                floor   INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_counters (
                chat_id   INTEGER NOT NULL,
                user_id   INTEGER NOT NULL,
                paradox   INTEGER NOT NULL DEFAULT 0,
                willpower INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        _migrate(conn)


def _migrate(conn):
    """Догнать схему до текущей версии, ничего не потеряв.

    ``ALTER TABLE ADD COLUMN`` с ``DEFAULT`` не переписывает существующие
    строки: у игроков, заведённых до появления кнопки «любой ценой», просто
    появляется treat_chance со стартовым значением, а накопленные парадоксы
    и воля остаются как были. Проверка по PRAGMA делает вызов идемпотентным,
    поэтому миграция безопасно переживает любое число перезапусков.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(user_counters)")}
    if "treat_chance" not in columns:
        conn.execute(
            "ALTER TABLE user_counters ADD COLUMN treat_chance "
            f"INTEGER NOT NULL DEFAULT {TREAT_CHANCE_START}"
        )


# ================== ПОРОГ ЧАТА ==================
def get_floor(chat_id):
    """Порог чата, либо DEFAULT_FLOOR, если он не задавался."""
    with _db() as conn:
        row = conn.execute(
            "SELECT floor FROM chat_settings WHERE chat_id = ?", (chat_id,)
        ).fetchone()
    return row[0] if row else DEFAULT_FLOOR


def set_floor(chat_id, value):
    """Записать порог чата (upsert)."""
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO chat_settings (chat_id, floor) VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET floor = excluded.floor
            """,
            (chat_id, value),
        )


# ================== СЧЁТЧИКИ ИГРОКА ==================
def get_counters(chat_id, user_id):
    """(парадоксы, использовано воли) для игрока в этом чате."""
    with _db() as conn:
        row = conn.execute(
            "SELECT paradox, willpower FROM user_counters "
            "WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()
    return (row[0], row[1]) if row else (0, 0)


def get_paradox(chat_id, user_id):
    return get_counters(chat_id, user_id)[0]


def set_paradox(chat_id, user_id, value):
    """Установить счётчик парадоксов. Ниже нуля не опускается."""
    value = max(0, value)
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO user_counters (chat_id, user_id, paradox, willpower)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET paradox = excluded.paradox
            """,
            (chat_id, user_id, value),
        )
    return value


def add_willpower(chat_id, user_id):
    """Отметить одно использование Силы воли и вернуть новое значение."""
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO user_counters (chat_id, user_id, paradox, willpower)
            VALUES (?, ?, 0, 1)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET willpower = willpower + 1
            """,
            (chat_id, user_id),
        )
        row = conn.execute(
            "SELECT willpower FROM user_counters WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()
    return row[0]


def reset_willpower(chat_id, user_id):
    """Обнулить счётчик Силы воли одного игрока. Чужие не трогает."""
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO user_counters (chat_id, user_id, paradox, willpower)
            VALUES (?, ?, 0, 0)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET willpower = 0
            """,
            (chat_id, user_id),
        )


def reset_paradox_and_willpower(chat_id, user_id):
    """Обнулить и парадоксы, и волю — цена кнопки «любой ценой»."""
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO user_counters (chat_id, user_id, paradox, willpower)
            VALUES (?, ?, 0, 0)
            ON CONFLICT(chat_id, user_id)
            DO UPDATE SET paradox = 0, willpower = 0
            """,
            (chat_id, user_id),
        )


# ================== ШАНС КНОПКИ «ЛЮБОЙ ЦЕНОЙ» ==================
def get_treat_chance(chat_id, user_id):
    """Текущий шанс появления кнопки для игрока, в процентах."""
    with _db() as conn:
        row = conn.execute(
            "SELECT treat_chance FROM user_counters "
            "WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()
    return row[0] if row else TREAT_CHANCE_START


def set_treat_chance(chat_id, user_id, value):
    """Записать новый шанс появления кнопки."""
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO user_counters
                (chat_id, user_id, paradox, willpower, treat_chance)
            VALUES (?, ?, 0, 0, ?)
            ON CONFLICT(chat_id, user_id)
            DO UPDATE SET treat_chance = excluded.treat_chance
            """,
            (chat_id, user_id, value),
        )
