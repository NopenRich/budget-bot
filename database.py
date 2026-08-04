"""
Слой работы с базой данных (SQLite, через aiosqlite).
Используется и телеграм-ботом, и HTTP API для синхронизации с PWA.
"""
import aiosqlite
import uuid
import secrets
from datetime import datetime, date
from typing import Optional

DB_PATH = "budget.db"

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


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                sync_token TEXT UNIQUE,
                created_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                name TEXT,
                type TEXT CHECK(type in ('income','expense')),
                UNIQUE(telegram_id, name, type)
            )
        """)
        await db.execute("""
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
        await db.commit()


async def get_or_create_user(telegram_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT sync_token FROM users WHERE telegram_id=?", (telegram_id,)
        )
        row = await cur.fetchone()
        if row:
            token = row[0]
        else:
            token = secrets.token_urlsafe(16)
            await db.execute(
                "INSERT INTO users (telegram_id, sync_token, created_at) VALUES (?,?,?)",
                (telegram_id, token, datetime.utcnow().isoformat()),
            )
            for name, ctype in DEFAULT_CATEGORIES:
                await db.execute(
                    "INSERT OR IGNORE INTO categories (telegram_id, name, type) VALUES (?,?,?)",
                    (telegram_id, name, ctype),
                )
            await db.commit()
        return token


async def check_token(telegram_id: int, token: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM users WHERE telegram_id=? AND sync_token=?",
            (telegram_id, token),
        )
        return (await cur.fetchone()) is not None


async def get_categories(telegram_id: int, ctype: Optional[str] = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if ctype:
            cur = await db.execute(
                "SELECT name FROM categories WHERE telegram_id=? AND type=? ORDER BY id",
                (telegram_id, ctype),
            )
        else:
            cur = await db.execute(
                "SELECT name, type FROM categories WHERE telegram_id=? ORDER BY id",
                (telegram_id,),
            )
        return await cur.fetchall()


async def add_category(telegram_id: int, name: str, ctype: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO categories (telegram_id, name, type) VALUES (?,?,?)",
            (telegram_id, name, ctype),
        )
        await db.commit()


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
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                """INSERT INTO records
                   (telegram_id, client_id, type, amount, category, note, record_date, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    telegram_id,
                    client_id,
                    rtype,
                    amount,
                    category,
                    note,
                    record_date,
                    datetime.utcnow().isoformat(),
                ),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False  # дубликат — уже синхронизировано ранее


async def get_records(telegram_id: int, date_from: str, date_to: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """SELECT type, amount, category, note, record_date
               FROM records
               WHERE telegram_id=? AND record_date BETWEEN ? AND ?
               ORDER BY record_date, id""",
            (telegram_id, date_from, date_to),
        )
        return await cur.fetchall()


async def get_all_records_since(telegram_id: int, since_iso: str):
    """Для отдачи PWA всех записей, добавленных из бота (двусторонняя синхронизация)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """SELECT client_id, type, amount, category, note, record_date, created_at
               FROM records WHERE telegram_id=? AND created_at > ?
               ORDER BY created_at""",
            (telegram_id, since_iso),
        )
        return await cur.fetchall()
