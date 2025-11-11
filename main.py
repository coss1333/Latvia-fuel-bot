#!/usr/bin/env python3
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import List, Dict, Tuple

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode

from fuel_sources import FuelFetcher, normalize_fuel_type, SUPPORTED_FUELS
from parser_utils import format_price, human_now_lv, chunk_text

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

if not TELEGRAM_TOKEN:
    logging.warning("⚠️ TELEGRAM_BOT_TOKEN is not set. Set it in environment or .env file.")

bot = Bot(token=TELEGRAM_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

HELP_TEXT = (
    "👋 Бот цен на топливо в Латвии\n\n"
    "Команды:\n"
    "• /start — краткая справка\n"
    "• /help — справка\n"
    "• /fuels — список поддерживаемых типов топлива\n"
    "• /top <вид_топлива> [N] — топ N (по умолчанию 20) самых дешёвых АЗС по указанному топливу.\n"
    "   Примеры: /top a95, /top diesel 10, /top lpg\n\n"
    "Источники: Waze-пользовательские цены (через gas.didnt.work) + страницы сетей (Circle K, Neste, Virši, Viada).\n"
    "⏱ Данные обновляются при каждом запросе (агрегатор ~каждые 5 часов)."
)

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(HELP_TEXT)

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(HELP_TEXT)

@dp.message(Command("fuels"))
async def fuels_cmd(message: types.Message):
    fuels = ", ".join(sorted(SUPPORTED_FUELS))
    await message.answer(f"Поддерживаемые виды топлива:\n<b>{fuels}</b>")

@dp.message(Command("top"))
async def top_cmd(message: types.Message):
    args = message.text.split()[1:]
    if not args:
        return await message.answer("Укажите вид топлива. Пример: <code>/top a95</code> или <code>/top diesel 15</code>")

    fuel_raw = args[0]
    n = 20
    if len(args) >= 2 and args[1].isdigit():
        n = max(1, min(50, int(args[1])))

    fuel = normalize_fuel_type(fuel_raw)
    if not fuel:
        return await message.answer("Неизвестный вид топлива. Посмотрите список в /fuels")

    await message.answer(f"⛽ Получаю цены по <b>{fuel}</b>… Это может занять несколько секунд.")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
        fetcher = FuelFetcher(session=session)
        stations = await fetcher.fetch_all()

    # Filter by fuel and compute top N
    rows = []
    now = human_now_lv()
    for st in stations:
        price = st["prices"].get(fuel)
        if price is None:
            continue
        rows.append((price, st))

    if not rows:
        return await message.answer("Не удалось найти цены по этому виду топлива прямо сейчас. Попробуйте позже.")

    rows.sort(key=lambda x: x[0])  # ascending by price
    top_rows = rows[:n]

    lines = [
        f"⛽ <b>Топ-{len(top_rows)} по {fuel.upper()}</b> • {now}",
        "Источник(и): gas.didnt.work (Waze), Circle K, Neste, Virši, Viada",
        ""
    ]
    for i, (price, st) in enumerate(top_rows, start=1):
        addr = st.get("address") or "-"
        src = st.get("source", "-")
        ts = st.get("timestamp", "")
        ts_str = f" • {ts}" if ts else ""
        lines.append(f"{i}. <b>{st['name']}</b> — {addr}\n   {fuel.upper()}: <b>{format_price(price)}</b> • {src}{ts_str}")

    text = "\n".join(lines)
    for chunk in chunk_text(text, limit=3800):
        await message.answer(chunk)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
