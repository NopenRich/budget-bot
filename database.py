"""
Слой работы с базой данных (libSQL / Turso, через libsql_client).
Используется и телеграм-ботом, и HTTP API для синхронизации с PWA.

Почему не aiosqlite: на бесплатном тарифе Render файловая система
эфемерна — локальный файл budget.db стирается при каждом сне/рестарте/
редеплое. libSQL/Turso хранит данные удалённо, поэтому они переживают
это без проблем.

Настройка:
1. Зарегистрируйся на https://turso.tech (бесплатный тариф без ограничения по времени).
2. Установи turso CLI и выполни:
     turso auth login
     turso db create budget-bot
     turso db show budget-bot --url          -> это TURSO_DATABASE_URL
     turso db tokens create budget-bot        -> это TURSO_AUTH_TOKEN
3. Добавь обе переменные в Environment на Render.

Для локальной разработки без Turso: если TURSO_DATABASE_URL не задан,
используется локальный файл budget.db (через тот же libsql_client) —
удобно для тестов на своей машине.
"""
import os
import uuid
import secrets
from datetime import datetime
from typing import Optional

import libsql_client

TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "file:budget.db")
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")

DEFAULT_CATEGORIES = [
    ("Еда", "expense"),
    ("Транспорт", "expense"),
    ("Жильё", "expense"),
    ("Здоровье", "expense"),
    ("Развлечения", "expense"),
    ("Одежда", "expense"),
    ("Связь/интернет", "expense"),
    ("Прочее", "expense"),
    ("Зарплата", "income"),
    ("Подработка", "income"),
    ("Подарки", "income"),
    ("Прочее", "income"),
]

_client: Optional[libsql_client.Client] = None


def get_client() -> libsql_client.Client:
    """Один клиент на всё время жизни приложения (переиспользуем соединение)."""
    global _client
    if _client is None:
        kwargs = {"url": TURSO_DATABASE_URL}
        if TURSO_AUTH_TOKEN:
            kwargs["auth_token"] = TURSO_AUTH_TOKEN
        _client = libsql_client.create_client(**kwargs)
    return _client


async def close_client():
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def init_db():
    client = get_client()
    await client.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            sync_token TEXT UNIQUE,
            created_at TEXT
        )
    """)
    await client.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            name TEXT,
            type TEXT CHECK(type in ('income','expense')),
            UNIQUE(telegram_id, name, type)
        )
    """)
    await client.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            client_id TEXT UNIQUE,
            type TEXT CHECK(type in ('income','expense')),
            amount REAL,
            category TEXT,
            note TEXT,
            record_date TEXT,
            created_at TEXT
        )
    """)


async def get_or_create_user(telegram_id: int) -> str:
    client = get_client()
    rs = await client.execute(
        "SELECT sync_token FROM users WHERE telegram_id=?", [telegram_id]
    )
    if rs.rows:
        return rs.rows[0][0]

    token = secrets.token_urlsafe(16)
    await client.execute(
        "INSERT INTO users (telegram_id, sync_token, created_at) VALUES (?,?,?)",
        [telegram_id, token, datetime.utcnow().isoformat()],
    )
    for name, ctype in DEFAULT_CATEGORIES:
        await client.execute(
            "INSERT OR IGNORE INTO categories (telegram_id, name, type) VALUES (?,?,?)",
            [telegram_id, name, ctype],
        )
    return token


async def check_token(telegram_id: int, token: str) -> bool:
    client = get_client()
    rs = await client.execute(
        "SELECT 1 FROM users WHERE telegram_id=? AND sync_token=?",
        [telegram_id, token],
    )
    return len(rs.rows) > 0


async def get_categories(telegram_id: int, ctype: Optional[str] = None):
    client = get_client()
    if ctype:
        rs = await client.execute(
            "SELECT name FROM categories WHERE telegram_id=? AND type=? ORDER BY id",
            [telegram_id, ctype],
        )
    else:
        rs = await client.execute(
            "SELECT name, type FROM categories WHERE telegram_id=? ORDER BY id",
            [telegram_id],
        )
    return [tuple(row) for row in rs.rows]


async def add_category(telegram_id: int, name: str, ctype: str):
    client = get_client()
    await client.execute(
        "INSERT OR IGNORE INTO categories (telegram_id, name, type) VALUES (?,?,?)",
        [telegram_id, name, ctype],
    )


async def add_record(
    telegram_id: int,
    rtype: str,
    amount: float,
    category: str,
    note: str,
    record_date: str,
    client_id: Optional[str] = None,
) -> bool:
    """Возвращает True если запись реально добавлена (не дубликат по client_id)."""
    if not client_id:
        client_id = str(uuid.uuid4())
    client = get_client()
    rs = await client.execute(
        """INSERT OR IGNORE INTO records
           (telegram_id, client_id, type, amount, category, note, record_date, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        [
            telegram_id,
            client_id,
            rtype,
            amount,
            category,
            note,
            record_date,
            datetime.utcnow().isoformat(),
        ],
    )
    return rs.rows_affected > 0  # False = дубликат, уже синхронизировано ранее


async def get_records(telegram_id: int, date_from: str, date_to: str):
    client = get_client()
    rs = await client.execute(
        """SELECT type, amount, category, note, record_date
           FROM records
           WHERE telegram_id=? AND record_date BETWEEN ? AND ?
           ORDER BY record_date, id""",
        [telegram_id, date_from, date_to],
    )
    return [tuple(row) for row in rs.rows]


async def get_all_records_since(telegram_id: int, since_iso: str):
    """Для отдачи PWA всех записей, добавленных из бота (двусторонняя синхронизация)."""
    client = get_client()
    rs = await client.execute(
        """SELECT client_id, type, amount, category, note, record_date, created_at
           FROM records WHERE telegram_id=? AND created_at > ?
           ORDER BY created_at""",
        [telegram_id, since_iso],
    )
    return [tuple(row) for row in rs.rows]
