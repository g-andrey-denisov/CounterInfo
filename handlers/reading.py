"""Показания на дату: /reading, выбор счётчика и даты (календарь / ввод), вывод таблицы."""

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

import db
import search
from callbacks import CalendarDayCb, ReadingDateCb
from formatting import (
    diff_str,
    fmt_consumption,
    fmt_iso_date,
    fmt_serial,
    parse_input_date,
)
from keyboards import reading_date_kb
from states import ReadingForm

router = Router()


# ── /reading + слово "Показания" ─────────────────────────────────────────────


@router.message(Command("reading"))
@router.message(F.text.func(lambda t: bool(t) and t.strip().lower() == "показания"))
async def cmd_reading(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ReadingForm.waiting_query)
    await message.answer(
        "<b>Показания на дату</b>\n\n"
        "Введите серийный номер, код N.М или отправьте фото со штрих-кодом.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(ReadingForm.waiting_query, F.text)
async def reading_handle_text(message: Message, state: FSMContext) -> None:
    serial = message.text.strip()
    if not serial:
        return
    row = await search.find_by_text(serial)
    await _reading_store_counter(message, state, row, serial)


@router.message(ReadingForm.waiting_query, F.photo)
async def reading_handle_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    barcode = await search.read_barcode_from_message(message, bot)
    if barcode is None:
        return
    row = await search.find_by_barcode(barcode)
    await _reading_store_counter(message, state, row, barcode)


async def _reading_store_counter(
    message: Message, state: FSMContext, row: dict | None, query: str
) -> None:
    if row is None:
        await message.answer(
            f"Счётчик <code>{query}</code> не найден. Попробуйте ещё раз.",
            parse_mode="HTML",
        )
        return
    await state.update_data(
        reading_counter_id=row["Obj_Id_Counter"],
        reading_name=row["Name"],
        reading_serial=row["SerialNumber"],
    )
    await state.set_state(ReadingForm.waiting_date)
    await message.answer(
        f"<b>{row['Name']}</b>  <code>{fmt_serial(row['SerialNumber'])}</code>\n\n"
        "Выберите дату или введите в формате <b>дд.мм.гггг</b>:",
        parse_mode="HTML",
        reply_markup=reading_date_kb(),
    )


@router.callback_query(ReadingDateCb.filter(), StateFilter(ReadingForm.waiting_date))
async def cb_reading_date(
    query: CallbackQuery, callback_data: ReadingDateCb, state: FSMContext
) -> None:
    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer()
    data = await state.get_data()
    await state.clear()
    readings = await db.get_consumption_around_date(
        data["reading_counter_id"], callback_data.date
    )
    await query.message.answer(
        _format_readings(data["reading_name"], data["reading_serial"], callback_data.date, readings),
        parse_mode="HTML",
    )


@router.callback_query(CalendarDayCb.filter(F.scope == "reading"), StateFilter(ReadingForm.waiting_date))
async def cb_calendar_day_reading(
    query: CallbackQuery, callback_data: CalendarDayCb, state: FSMContext
) -> None:
    iso_date = f"{callback_data.year:04d}-{callback_data.month:02d}-{callback_data.day:02d}"
    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer()
    data = await state.get_data()
    await state.clear()
    readings = await db.get_consumption_around_date(data["reading_counter_id"], iso_date)
    await query.message.answer(
        _format_readings(data["reading_name"], data["reading_serial"], iso_date, readings),
        parse_mode="HTML",
    )


@router.message(ReadingForm.waiting_date, F.text)
async def reading_handle_date(message: Message, state: FSMContext) -> None:
    iso_date = parse_input_date(message.text.strip())
    if iso_date is None:
        await message.answer(
            "Неверный формат. Введите дату как <b>дд.мм.гггг</b>:", parse_mode="HTML"
        )
        return

    data = await state.get_data()
    await state.clear()

    counter_id = data["reading_counter_id"]
    name = data["reading_name"]
    serial = data["reading_serial"]

    readings = await db.get_consumption_around_date(counter_id, iso_date)
    await message.answer(
        _format_readings(name, serial, iso_date, readings),
        parse_mode="HTML",
    )


def _format_readings(name: str, serial: str, iso_date: str, readings: dict) -> str:
    before = readings["before"]
    on_date = readings["on_date"]
    after = readings["after"]

    col_serial = fmt_serial(serial)
    col_before = fmt_consumption(before)
    col_third = fmt_consumption(on_date) if on_date else (fmt_consumption(after) if after else "нет")
    col_diff = diff_str(col_before, col_third)

    H1, H2, H3, H4 = "Серийный", "До", "На дату", "Итого"
    w1 = max(len(H1), len(col_serial))
    w2 = max(len(H2), len(col_before))
    w3 = max(len(H3), len(col_third))

    lines = [
        f"{H1:<{w1}}  {H2:<{w2}}  {H3:<{w3}}  {H4}",
        f"{'-'*w1}  {'-'*w2}  {'-'*w3}  {'-'*len(H4)}",
        f"{col_serial:<{w1}}  {col_before:<{w2}}  {col_third:<{w3}}  {col_diff}",
    ]
    date_str = fmt_iso_date(iso_date)
    header = f"<b>Показания на {date_str}</b>\n\n<b>Название:</b> {name}\n\n"
    return header + "<pre>" + "\n".join(lines) + "</pre>"
