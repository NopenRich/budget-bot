"""
Бюджет-бот: телеграм-бот + HTTP API синхронизации с офлайн PWA.
Запуск: python main.py
Переменная окружения BOT_TOKEN обязательна.
"""
import asyncio
import io
import os
import logging
from datetime import datetime, date, timedelta
from calendar import monthrange

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

import database as db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("budget-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8000))

bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

MONTHS_RU = ["январь","февраль","март","апрель","май","июнь","июль",
             "август","сентябрь","октябрь","ноябрь","декабрь"]


# ---------- Состояния добавления записи ----------
class AddRecord(StatesGroup):
    choosing_category = State()
    entering_amount = State()
    entering_note = State()


def type_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➖ Расход", callback_data="type:expense"),
         InlineKeyboardButton(text="➕ Доход", callback_data="type:income")]
    ])
    return kb


def categories_keyboard(categories, prefix="cat"):
    rows, row = [], []
    for i, (name,) in enumerate(categories):
        row.append(InlineKeyboardButton(text=name, callback_data=f"{prefix}:{name}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def period_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Сегодня", callback_data="period:today"),
         InlineKeyboardButton(text="🗓 Этот месяц", callback_data="period:month")],
        [InlineKeyboardButton(text="📊 График по категориям", callback_data="chart:pie"),
         InlineKeyboardButton(text="📈 График по дням", callback_data="chart:bar")],
    ])
    return kb


# ---------- Хэндлеры ----------
@router.message(CommandStart())
async def start(message: Message):
    token = await db.get_or_create_user(message.from_user.id)
    await message.answer(
        "Привет! Я помогу вести бюджет.\n\n"
        "➕ /add — добавить доход или расход\n"
        "📊 /stats — статистика (сегодня / месяц / графики)\n"
        "🏷 /categories — список категорий\n"
        "➕🏷 /addcategory Название — добавить свою категорию (расход)\n"
        "➕🏷 /addincome Название — добавить свою категорию (доход)\n"
        "🔑 /token — данные для подключения офлайн-приложения\n\n"
        f"Твой Telegram ID: <code>{message.from_user.id}</code>\n"
        f"Токен синхронизации: <code>{token}</code>\n"
        "Эти два значения нужно ввести в настройках офлайн-приложения (PWA).",
        parse_mode="HTML",
    )


@router.message(Command("token"))
async def token_cmd(message: Message):
    token = await db.get_or_create_user(message.from_user.id)
    await message.answer(
        f"Telegram ID: <code>{message.from_user.id}</code>\n"
        f"Токен: <code>{token}</code>\n\n"
        "Введи их в настройках офлайн-приложения, чтобы включить синхронизацию.",
        parse_mode="HTML",
    )


@router.message(Command("add"))
async def add_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Что добавляем?", reply_markup=type_keyboard())


@router.callback_query(F.data.startswith("type:"))
async def choose_type(callback: CallbackQuery, state: FSMContext):
    rtype = callback.data.split(":")[1]
    await state.update_data(rtype=rtype)
    cats = await db.get_categories(callback.from_user.id, rtype)
    await state.set_state(AddRecord.choosing_category)
    label = "расхода" if rtype == "expense" else "дохода"
    await callback.message.edit_text(f"Выбери категорию {label}:", reply_markup=categories_keyboard(cats))
    await callback.answer()


@router.callback_query(AddRecord.choosing_category, F.data.startswith("cat:"))
async def choose_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":", 1)[1]
    await state.update_data(category=category)
    await state.set_state(AddRecord.entering_amount)
    await callback.message.edit_text(f"Категория: {category}\nВведи сумму (число):")
    await callback.answer()


@router.message(AddRecord.entering_amount)
async def enter_amount(message: Message, state: FSMContext):
    text = message.text.replace(",", ".").strip()
    try:
        amount = abs(float(text))
    except ValueError:
        await message.answer("Не похоже на число. Введи сумму ещё раз, например: 350")
        return
    await state.update_data(amount=amount)
    await state.set_state(AddRecord.entering_note)
    await message.answer("Комментарий (необязательно). Напиши текст или отправь «-», чтобы пропустить.")


@router.message(AddRecord.entering_note)
async def enter_note(message: Message, state: FSMContext):
    note = "" if message.text.strip() == "-" else message.text.strip()
    data = await state.get_data()
    today = date.today().isoformat()
    await db.add_record(
        telegram_id=message.from_user.id,
        rtype=data["rtype"],
        amount=data["amount"],
        category=data["category"],
        note=note,
        record_date=today,
    )
    sign = "-" if data["rtype"] == "expense" else "+"
    await message.answer(
        f"Записано: {sign}{data['amount']:.2f} — {data['category']}" + (f" ({note})" if note else "")
    )
    await state.clear()


@router.message(Command("categories"))
async def list_categories(message: Message):
    cats = await db.get_categories(message.from_user.id)
    expense = [c[0] for c in cats if c[1] == "expense"]
    income = [c[0] for c in cats if c[1] == "income"]
    text = "🏷 <b>Категории расходов:</b>\n" + "\n".join(f"• {c}" for c in expense)
    text += "\n\n🏷 <b>Категории доходов:</b>\n" + "\n".join(f"• {c}" for c in income)
    await message.answer(text, parse_mode="HTML")


@router.message(Command("addcategory"))
async def add_expense_category(message: Message):
    name = message.text.partition(" ")[2].strip()
    if not name:
        await message.answer("Использование: /addcategory Название")
        return
    await db.add_category(message.from_user.id, name, "expense")
    await message.answer(f"Добавлена категория расходов: {name}")


@router.message(Command("addincome"))
async def add_income_category(message: Message):
    name = message.text.partition(" ")[2].strip()
    if not name:
        await message.answer("Использование: /addincome Название")
        return
    await db.add_category(message.from_user.id, name, "income")
    await message.answer(f"Добавлена категория дохода: {name}")


@router.message(Command("stats"))
async def stats_menu(message: Message):
    await message.answer("Что показать?", reply_markup=period_keyboard())


def format_stats(records, title):
    income = sum(r[1] for r in records if r[0] == "income")
    expense = sum(r[1] for r in records if r[0] == "expense")
    lines = [f"<b>{title}</b>", f"Доходы: +{income:.2f}", f"Расходы: -{expense:.2f}",
             f"Баланс: {income - expense:+.2f}", ""]
    by_cat = {}
    for rtype, amount, category, note, rdate in records:
        if rtype == "expense":
            by_cat[category] = by_cat.get(category, 0) + amount
    if by_cat:
        lines.append("По категориям (расход):")
        for cat, amt in sorted(by_cat.items(), key=lambda x: -x[1]):
            lines.append(f"  {cat}: {amt:.2f}")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("period:"))
async def show_period_stats(callback: CallbackQuery):
    period = callback.data.split(":")[1]
    today = date.today()
    if period == "today":
        records = await db.get_records(callback.from_user.id, today.isoformat(), today.isoformat())
        title = f"Сегодня, {today.strftime('%d.%m.%Y')}"
    else:
        first = today.replace(day=1)
        records = await db.get_records(callback.from_user.id, first.isoformat(), today.isoformat())
        title = f"{MONTHS_RU[today.month-1].capitalize()} {today.year}"
    await callback.message.answer(format_stats(records, title), parse_mode="HTML")
    await callback.answer()


async def month_records(telegram_id):
    today = date.today()
    first = today.replace(day=1)
    last_day = monthrange(today.year, today.month)[1]
    last = today.replace(day=last_day)
    return await db.get_records(telegram_id, first.isoformat(), last.isoformat()), today


@router.callback_query(F.data == "chart:pie")
async def chart_pie(callback: CallbackQuery):
    records, today = await month_records(callback.from_user.id)
    by_cat = {}
    for rtype, amount, category, note, rdate in records:
        if rtype == "expense":
            by_cat[category] = by_cat.get(category, 0) + amount
    if not by_cat:
        await callback.message.answer("Нет расходов за этот месяц.")
        await callback.answer()
        return
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.pie(by_cat.values(), labels=by_cat.keys(), autopct="%1.0f%%", startangle=90)
    ax.set_title(f"Расходы по категориям — {MONTHS_RU[today.month-1]} {today.year}")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    await callback.message.answer_photo(BufferedInputFile(buf.read(), filename="pie.png"))
    await callback.answer()


@router.callback_query(F.data == "chart:bar")
async def chart_bar(callback: CallbackQuery):
    records, today = await month_records(callback.from_user.id)
    by_day = {}
    for rtype, amount, category, note, rdate in records:
        if rtype == "expense":
            by_day[rdate] = by_day.get(rdate, 0) + amount
    if not by_day:
        await callback.message.answer("Нет расходов за этот месяц.")
        await callback.answer()
        return
    days = sorted(by_day.keys())
    values = [by_day[d] for d in days]
    labels = [d[-2:] for d in days]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(labels, values)
    ax.set_title(f"Расходы по дням — {MONTHS_RU[today.month-1]} {today.year}")
    ax.set_xlabel("День")
    ax.set_ylabel("Сумма")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    await callback.message.answer_photo(BufferedInputFile(buf.read(), filename="bar.png"))
    await callback.answer()


# ---------- HTTP API для PWA ----------
api = FastAPI(title="Budget Bot Sync API")


class SyncRecord(BaseModel):
    client_id: str
    type: str  # income | expense
    amount: float
    category: str
    note: str = ""
    record_date: str  # YYYY-MM-DD


class SyncPayload(BaseModel):
    telegram_id: int
    token: str
    records: list[SyncRecord]
    since: str = "1970-01-01T00:00:00"


@api.get("/health")
async def health():
    return {"status": "ok"}


@api.post("/sync")
async def sync(payload: SyncPayload):
    if not await db.check_token(payload.telegram_id, payload.token):
        raise HTTPException(status_code=403, detail="Неверный telegram_id или токен")

    added = 0
    for rec in payload.records:
        ok = await db.add_record(
            telegram_id=payload.telegram_id,
            rtype=rec.type,
            amount=rec.amount,
            category=rec.category,
            note=rec.note,
            record_date=rec.record_date,
            client_id=rec.client_id,
        )
        if ok:
            added += 1

    server_records = await db.get_all_records_since(payload.telegram_id, payload.since)
    now_iso = datetime.utcnow().isoformat()
    return {
        "added": added,
        "server_records": [
            {
                "client_id": r[0], "type": r[1], "amount": r[2],
                "category": r[3], "note": r[4], "record_date": r[5],
            } for r in server_records
        ],
        "server_time": now_iso,
    }


@api.get("/categories/{telegram_id}")
async def api_categories(telegram_id: int, token: str):
    if not await db.check_token(telegram_id, token):
        raise HTTPException(status_code=403, detail="Неверный telegram_id или токен")
    cats = await db.get_categories(telegram_id)
    return [{"name": c[0], "type": c[1]} for c in cats]


# ---------- Запуск ----------
async def run_bot():
    if not bot:
        log.warning("BOT_TOKEN не задан — бот не запущен, работает только HTTP API.")
        return
    await dp.start_polling(bot)


async def run_api():
    config = uvicorn.Config(api, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    await db.init_db()
    await asyncio.gather(run_bot(), run_api())


if __name__ == "__main__":
    asyncio.run(main())
