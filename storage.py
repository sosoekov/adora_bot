"""Персистентное хранилище настроек бота (SQLite).

Почему появился этот модуль
---------------------------
Раньше порог лежал в ``Application.chat_data``. В python-telegram-bot 20.x это
read-only ``MappingProxyType``, поэтому запись вида
``application.chat_data.setdefault(chat_id, {})["floor"] = value`` падала с
``AttributeError`` ещё до ответа пользователю — порог не сохранялся никогда.
И даже если бы запись работала, ``chat_data`` живёт в памяти процесса и
теряется при рестарте.

Схема (этап 1)
--------------
    chat_settings(
        chat_id INTEGER PRIMARY KEY,
        floor   INTEGER NOT NULL
    )

Миграция не требуется: персистентных данных до этого момента не существовало,
файл БД создаётся при первом запуске.

Этап 2 добавит счётчик парадоксов ОТДЕЛЬНОЙ таблицей через
``CREATE TABLE IF NOT EXISTS`` — ``chat_settings`` при этом не пересоздаётся и
не меняется, данные этапа 1 сохраняются как есть.
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

# ЭТАП 1: floor пока остаётся порогом УСПЕХА (x >= floor), поэтому дефолт
# сохраняем прежним — 6, чтобы этап 1 не менял математику броска.
# ЭТАП 3: когда floor станет только порогом ПАРАДОКСА, а успех — фиксированным
# (x > 5), это значение меняется на 2.
DEFAULT_FLOOR = 6


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
