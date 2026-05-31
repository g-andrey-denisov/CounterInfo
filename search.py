"""Поиск счётчика: по тексту (серийный номер / код N.M) и по штрих-коду с фото."""

from io import BytesIO

import zxingcpp
from PIL import Image
from aiogram import Bot
from aiogram.types import Message

import db
from constants import NAME_CODE_RE


async def find_by_text(text: str) -> dict | None:
    """Ищет счётчик по коду адреса N.M либо по серийному номеру."""
    m = NAME_CODE_RE.match(text)
    if m:
        left = m.group(1).zfill(2)
        right = m.group(2).zfill(2)
        return await db.get_counter_by_name_code(left, right)
    return await db.get_counter_by_serial(text)


async def find_by_barcode(barcode: str) -> dict | None:
    return await db.get_counter_by_barcode(barcode)


async def read_barcode_from_message(message: Message, bot: Bot) -> str | None:
    """Распознаёт штрих-код на присланном фото.

    Сама отправляет статусное сообщение и сообщение об ошибке при неудаче.
    Возвращает текст штрих-кода или None, если код не распознан.
    """
    await message.answer("Распознаю штрих-код...")
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    buf = BytesIO()
    await bot.download_file(file.file_path, destination=buf)

    results = zxingcpp.read_barcodes(Image.open(buf))
    if not results:
        await message.answer(
            "Штрих-код не найден на фото.\n"
            "Попробуйте более чёткий снимок или введите номер вручную."
        )
        return None
    return results[0].text.strip()
