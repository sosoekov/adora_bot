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
        chat_id   INTEGER NOT NULL,
        user_id   INTEGER NOT NULL,
        paradox   INTEGER NOT NULL DEFAULT 0,
        willpower INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (chat_id, user_id)
    )

Счётчики живут отдельно для каждого игрока в каждом чате, поэтому в общем чате
у всех свои значения.

Миграция не требуется. ``chat_settings`` не менялась с момента появления, а
``user_counters`` добавляется через ``CREATE TABLE IF NOT EXISTS`` — уже
записанные пороги остаются на месте, у игроков просто появляются нулевые
счётчики при первом обращении.
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
