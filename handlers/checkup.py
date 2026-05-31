"""Режим проверки счётчиков: /checkup, список непроверенных, сохранение результата."""

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)

import local_db
import search
from callbacks import (
    CheckupPageCb,
    CheckupStatusCb,
    ExitUncheckedCb,
    SkipCheckupCb,
)
from constants import CHECKUP_PAGE_SIZE
from formatting import fmt_check_date, fmt_serial
from keyboards import CHECKUP_KB, unchecked_nav_kb
from states import CheckupForm

router = Router()

_CHECKUP_NEXT = "Введите следующий серийный номер, код N.М или отправьте фото."


# ── /checkup + слово "Проверка" ───────────────────────────────────────────────


@router.message(Command("checkup", "check"))
@router.message(F.text.func(lambda t: bool(t) and t.strip().lower() == "проверка"))
async def cmd_checkup(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(CheckupForm.waiting_query)
    await message.answer(
        "<b>Режим проверки</b>\n\n"
        "Введите серийный номер, код N.М или отправьте фото со штрих-кодом.",
        parse_mode="HTML",
        reply_markup=CHECKUP_KB,
    )


# ── Выход из режима ───────────────────────────────────────────────────────────


@router.message(
    StateFilter(CheckupForm.waiting_query, CheckupForm.waiting_status, CheckupForm.waiting_comment),
    F.text == "Выйти из режима",
)
async def checkup_exit(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Режим проверки завершён.", reply_markup=ReplyKeyboardRemove())


# ── Список непроверенных ──────────────────────────────────────────────────────


@router.message(
    StateFilter(CheckupForm.waiting_query, CheckupForm.waiting_status, CheckupForm.waiting_comment),
    F.text == "Непроверенные",
)
async def checkup_show_unchecked(message: Message, state: FSMContext) -> None:
    await state.set_state(CheckupForm.waiting_query)
    await _show_unchecked_page(message, page=0)


@router.callback_query(ExitUncheckedCb.filter())
async def cb_exit_unchecked(query: CallbackQuery) -> None:
    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer("Вернулись в режим проверки.")


@router.callback_query(CheckupPageCb.filter())
async def cb_checkup_page(query: CallbackQuery, callback_data: CheckupPageCb) -> None:
    items, total = await local_db.get_unchecked_counters(
        offset=callback_data.page * CHECKUP_PAGE_SIZE,
        limit=CHECKUP_PAGE_SIZE,
    )
    total_pages = max(1, (total + CHECKUP_PAGE_SIZE - 1) // CHECKUP_PAGE_SIZE)
    text = _format_unchecked_page(items, callback_data.page, total, total_pages)
    kb = unchecked_nav_kb(callback_data.page, total_pages)
    await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await query.answer()


async def _show_unchecked_page(message: Message, page: int) -> None:
    items, total = await local_db.get_unchecked_counters(
        offset=page * CHECKUP_PAGE_SIZE,
        limit=CHECKUP_PAGE_SIZE,
    )
    if total == 0:
        await message.answer("Нет данных о счётчиках. Убедитесь, что кэш синхронизирован.")
        return
    total_pages = max(1, (total + CHECKUP_PAGE_SIZE - 1) // CHECKUP_PAGE_SIZE)
    text = _format_unchecked_page(items, page, total, total_pages)
    kb = unchecked_nav_kb(page, total_pages)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


def _format_unchecked_page(items: list[dict], page: int, total: int, total_pages: int) -> str:
    lines = [
        f"<b>Непроверенные счётчики</b> "
        f"(стр. {page + 1}/{total_pages}, всего: {total})\n"
    ]
    offset = page * CHECKUP_PAGE_SIZE
    for i, item in enumerate(items, start=offset + 1):
        date_str = fmt_check_date(item.get("last_check"))
        lines.append(f"{i}. {item['name'] or item['serial_number']} — {date_str}")
    return "\n".join(lines)


# ── Поиск счётчика (текст / фото) ─────────────────────────────────────────────


@router.message(CheckupForm.waiting_query, F.text, ~F.text.startswith("/"))
async def checkup_handle_text(message: Message, state: FSMContext) -> None:
    serial = message.text.strip()
    if not serial:
        return
    row = await search.find_by_text(serial)
    await _checkup_show_result(message, state, row, serial)


@router.message(CheckupForm.waiting_query, F.photo)
async def checkup_handle_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    barcode = await search.read_barcode_from_message(message, bot)
    if barcode is None:
        return
    row = await search.find_by_barcode(barcode)
    await _checkup_show_result(message, state, row, barcode)


async def _checkup_show_result(
    message: Message, state: FSMContext, row: dict | None, query: str
) -> None:
    if row is None:
        await message.answer(
            f"Счётчик <code>{query}</code> не найден. Попробуйте ещё раз.",
            parse_mode="HTML",
        )
        return

    consumption = str(row["Consumption"]) if row["Consumption"] is not None else "—"
    state_val = str(row["State"]) if row["State"] is not None else "—"
    update_time = (
        row["UpdateTime"].strftime("%d.%m.%Y %H:%M") if row["UpdateTime"] else "—"
    )

    await state.update_data(
        checkup_serial=row["SerialNumber"],
        checkup_name=row["Name"],
        checkup_consumption=str(row["Consumption"]) if row["Consumption"] is not None else None,
    )
    await state.set_state(CheckupForm.waiting_status)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Исправен",
                    callback_data=CheckupStatusCb(status="ok").pack(),
                ),
                InlineKeyboardButton(
                    text="Контроль",
                    callback_data=CheckupStatusCb(status="control").pack(),
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=CheckupStatusCb(status="cancel").pack(),
                ),
            ]
        ]
    )
    await message.answer(
        f"<b>Счётчик найден</b>\n\n"
        f"<b>Серийный номер:</b> <code>{fmt_serial(row['SerialNumber'])}</code>\n"
        f"<b>Название:</b> {row['Name']}\n"
        f"<b>Состояние:</b> {state_val}\n"
        f"<b>Потребление:</b> {consumption}\n"
        f"<b>Дата обновления:</b> {update_time}\n\n"
        f"Выберите результат проверки:",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ── Выбор статуса ─────────────────────────────────────────────────────────────


@router.callback_query(CheckupStatusCb.filter())
async def cb_checkup_status(
    query: CallbackQuery, callback_data: CheckupStatusCb, state: FSMContext
) -> None:
    current = await state.get_state()
    if current != CheckupForm.waiting_status:
        await query.answer("Устаревшая кнопка.", show_alert=True)
        return

    await query.message.edit_reply_markup(reply_markup=None)

    if callback_data.status == "cancel":
        await state.set_state(CheckupForm.waiting_query)
        await query.answer("Отменено.")
        await query.message.answer(f"Отменено.\n\n{_CHECKUP_NEXT}", reply_markup=CHECKUP_KB)
        return

    if callback_data.status == "ok":
        await state.update_data(checkup_status="Исправен")
        await query.answer()
        text = await _save_checkup(query.from_user, state, "Проверен, без нареканий")
        await query.message.answer(text, parse_mode="HTML", reply_markup=CHECKUP_KB)
        return

    # "control"
    await state.update_data(checkup_status="Контроль")
    await state.set_state(CheckupForm.waiting_comment)
    await query.answer()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пропустить",
                    callback_data=SkipCheckupCb(field="comment").pack(),
                )
            ]
        ]
    )
    await query.message.answer(
        "Введите <b>комментарий</b> или нажмите «Пропустить»:",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ── Ввод комментария и сохранение ─────────────────────────────────────────────


@router.message(CheckupForm.waiting_comment, F.text, ~F.text.startswith("/"))
async def checkup_handle_comment(message: Message, state: FSMContext) -> None:
    text = await _save_checkup(message.from_user, state, message.text.strip() or None)
    await message.answer(text, parse_mode="HTML", reply_markup=CHECKUP_KB)


@router.callback_query(SkipCheckupCb.filter(F.field == "comment"))
async def cb_skip_checkup_comment(query: CallbackQuery, state: FSMContext) -> None:
    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer()
    text = await _save_checkup(query.from_user, state, None)
    await query.message.answer(text, parse_mode="HTML", reply_markup=CHECKUP_KB)


async def _save_checkup(from_user, state: FSMContext, comment: str | None) -> str:
    data = await state.get_data()
    await state.set_state(CheckupForm.waiting_query)
    serial = data.get("checkup_serial", "")
    name = data.get("checkup_name")
    status = data.get("checkup_status", "")
    reading = data.get("checkup_consumption")
    user_name = from_user.full_name or from_user.username or str(from_user.id)

    await local_db.add_checkup_entry(
        user_id=from_user.id,
        user_name=user_name,
        serial=serial,
        name=name,
        status=status,
        reading=reading,
        comment=comment,
    )
    return (
        f"<b>Проверка сохранена</b>\n"
        f"<b>Серийный:</b> <code>{fmt_serial(serial)}</code>  "
        f"<b>Статус:</b> {status}\n"
        f"<b>Комментарий:</b> {comment or '—'}\n\n"
        f"{_CHECKUP_NEXT}"
    )
