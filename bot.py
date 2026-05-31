import asyncio
import calendar
import logging
import logging.handlers
import re
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Awaitable

import zxingcpp
from PIL import Image

from aiogram import BaseMiddleware, Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    TelegramObject,
)
from aiogram.filters.callback_data import CallbackData

from config import settings
import db
import local_db

logger = logging.getLogger(__name__)

# ── FSM ───────────────────────────────────────────────────────────────────────


class Form(StatesGroup):
    waiting_comment = State()


class CheckupForm(StatesGroup):
    waiting_query = State()
    waiting_status = State()
    waiting_comment = State()


class CheckupsForm(StatesGroup):
    waiting_date = State()


class ReadingForm(StatesGroup):
    waiting_query = State()
    waiting_date = State()


class PeriodForm(StatesGroup):
    waiting_query = State()
    waiting_period = State()


class MonthlyForm(StatesGroup):
    waiting_query = State()
    waiting_year = State()


# ── Callback data ─────────────────────────────────────────────────────────────


class SaveNotebookCb(CallbackData, prefix="snb"):
    serial: str


class SkipReadingCb(CallbackData, prefix="srp"):
    entry_id: int


class ClearConfirmCb(CallbackData, prefix="clr"):
    yes: int  # 1 = yes, 0 = no


class CheckupStatusCb(CallbackData, prefix="cst"):
    status: str  # "ok" | "control" | "cancel"


class SkipCheckupCb(CallbackData, prefix="skc"):
    field: str  # "comment"


class CheckupPageCb(CallbackData, prefix="cpp"):
    page: int


class ExitUncheckedCb(CallbackData, prefix="exu"):
    pass


class CheckupsDateCb(CallbackData, prefix="csd"):
    date: str  # ISO YYYY-MM-DD


class PeriodPresetCb(CallbackData, prefix="pps"):
    start: str  # ISO YYYY-MM-DD
    end: str    # ISO YYYY-MM-DD


class ReadingDateCb(CallbackData, prefix="rdt"):
    date: str  # ISO YYYY-MM-DD




class CheckupsPageCb(CallbackData, prefix="csp"):
    date: str  # ISO YYYY-MM-DD
    page: int


class ExitCheckupsCb(CallbackData, prefix="excs"):
    pass


# ── In-memory store: user_id → pending notebook entry_id ─────────────────────

_pending_entry: dict[int, int] = {}


# ── Access middleware ─────────────────────────────────────────────────────────


class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if (
            user
            and settings.ALLOWED_USER_IDS
            and user.id not in settings.ALLOWED_USER_IDS
        ):
            if isinstance(event, Message):
                await event.answer("Доступ запрещён.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Доступ запрещён.", show_alert=True)
            return
        return await handler(event, data)


# ── Router ────────────────────────────────────────────────────────────────────

router = Router()

_KEYWORDS = frozenset(["блокнот", "очисти", "проверка", "проверки", "показания", "период", "месяцы"])

_CHECKUP_PAGE_SIZE = 15
_CHECKUPS_PAGE_SIZE = 10

CHECKUP_KB = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Непроверенные"),
            KeyboardButton(text="Выйти из режима"),
        ]
    ],
    resize_keyboard=True,
)

_NAME_CODE_RE = re.compile(r"^(\d{1,3})[.,ю](\d{1,3})$")

WELCOME = (
    "<b>Бот поиска и контроля счётчиков электроэнергии и воды</b>\n\n"
    "Отправьте <b>фото со штрих-кодом</b> или введите:\n"
    "  • серийный номер\n"
    "  • код адреса в формате <b>N.М</b> — например <code>26.1</code>\n\n"
    "<b>Команды:</b>\n"
    "  /notebook — блокнот\n"
    "  /clear — очистить блокнот\n"
    "  /checkup — режим проверки\n"
    "  /checkups — журнал проверок\n"
    "  /reading, <b>Показания</b> — показания на дату\n"
    "  /period, <b>Период</b> — посуточный отчёт (до 90 дней)\n"
    "  /monthly, <b>Месяцы</b> — помесячный отчёт (до 5 лет)\n"
    "  /help — эта справка\n\n"
    "Ключевые слова: <b>Блокнот</b>, <b>Очисти</b>, <b>Проверка</b>, <b>Проверки</b>, "
    "<b>Показания</b>, <b>Период</b>, <b>Месяцы</b>."
)

# ── /start, /help ─────────────────────────────────────────────────────────────


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())


# ── /notebook + слово "Блокнот" ───────────────────────────────────────────────


@router.message(Command("notebook", "note"))
@router.message(F.text.func(lambda t: bool(t) and t.strip().lower() == "блокнот"))
async def cmd_notebook(message: Message, state: FSMContext) -> None:
    await state.clear()
    entries = await local_db.get_notebook(message.from_user.id)
    if not entries:
        await message.answer("Блокнот пуст.")
        return
    n = len(entries)
    lines = [f"<b>Блокнот ({n} {_plural(n, 'запись', 'записи', 'записей')})</b>\n"]
    for i, e in enumerate(entries, 1):
        comment = e["comment"] or "не указано"
        lines.append(
            f"<b>#{i}</b>  {e['ts']}\n"
            f"Серийный:   <code>{_fmt_serial(e['serial_number'])}</code>\n"
            f"Название:   {e['name'] or '—'}\n"
            f"Состояние:  {e['state'] or '—'}\n"
            f"Показ.(БД): {e['db_consumption'] or '—'}\n"
            f"Комментарий: {comment}\n"
            "──────────────────────"
        )
    await _send_long(message, "\n".join(lines))


# ── /clear + слово "Очисти" ───────────────────────────────────────────────────


@router.message(Command("clear"))
@router.message(F.text.func(lambda t: bool(t) and t.strip().lower() == "очисти"))
async def cmd_clear(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not await local_db.notebook_count(message.from_user.id):
        await message.answer("Блокнот пуст — нечего очищать.")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, очистить", callback_data=ClearConfirmCb(yes=1).pack()
                ),
                InlineKeyboardButton(
                    text="Отмена", callback_data=ClearConfirmCb(yes=0).pack()
                ),
            ]
        ]
    )
    await message.answer("Очистить блокнот? Все записи будут удалены.", reply_markup=kb)


@router.callback_query(ClearConfirmCb.filter())
async def cb_clear_confirm(query: CallbackQuery, callback_data: ClearConfirmCb) -> None:
    if callback_data.yes:
        await local_db.clear_notebook(query.from_user.id)
        await query.message.edit_text("Блокнот очищен.", reply_markup=None)
    else:
        await query.message.edit_text("Отменено.", reply_markup=None)
    await query.answer()


# ── /checkup + слово "Проверка" ───────────────────────────────────────────────


_CHECKUP_NEXT = (
    "Введите следующий серийный номер, код N.М или отправьте фото.\n"
    "/start — выход из режима проверки."
)


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


# ── /checkups + слово "Проверки" ──────────────────────────────────────────────


@router.message(Command("checkups"))
@router.message(F.text.func(lambda t: bool(t) and t.strip().lower() == "проверки"))
async def cmd_checkups(message: Message, state: FSMContext) -> None:
    await state.clear()
    dates = await local_db.get_checkup_dates(limit=5)
    if not dates:
        await message.answer("Журнал проверок пуст.")
        return
    await state.set_state(CheckupsForm.waiting_date)
    await message.answer(
        "Выберите дату или введите в формате <b>дд.мм.гггг</b>:",
        parse_mode="HTML",
        reply_markup=_build_checkups_date_kb(dates),
    )


@router.message(CheckupsForm.waiting_date, F.text)
async def checkups_handle_date(message: Message, state: FSMContext) -> None:
    iso_date = _parse_input_date(message.text.strip())
    if iso_date is None:
        await message.answer(
            "Неверный формат. Введите дату как <b>дд.мм.гггг</b>:", parse_mode="HTML"
        )
        return
    _, total = await local_db.get_checkups_by_date(iso_date, offset=0, limit=1)
    if total == 0:
        dates = await local_db.get_checkup_dates(limit=5)
        await message.answer(
            f"В указанную дату ({_fmt_iso_date(iso_date)}) проверок не проводилось.\n\n"
            "Выберите дату или введите в формате <b>дд.мм.гггг</b>:",
            parse_mode="HTML",
            reply_markup=_build_checkups_date_kb(dates),
        )
        return
    await state.clear()
    await _show_checkups_page(message, iso_date, page=0)


@router.callback_query(ExitCheckupsCb.filter())
async def cb_exit_checkups(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await query.message.delete()
    await query.answer("Выход из журнала проверок.")


@router.callback_query(CheckupsDateCb.filter())
async def cb_checkups_date(
    query: CallbackQuery, callback_data: CheckupsDateCb, state: FSMContext
) -> None:
    await state.clear()
    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer()
    await _show_checkups_page(query.message, callback_data.date, page=0)


@router.callback_query(CheckupsPageCb.filter())
async def cb_checkups_page(query: CallbackQuery, callback_data: CheckupsPageCb) -> None:
    items, total = await local_db.get_checkups_by_date(
        callback_data.date,
        offset=callback_data.page * _CHECKUPS_PAGE_SIZE,
        limit=_CHECKUPS_PAGE_SIZE,
    )
    total_pages = max(1, (total + _CHECKUPS_PAGE_SIZE - 1) // _CHECKUPS_PAGE_SIZE)
    text = _format_checkups_page(items, callback_data.date, callback_data.page, total, total_pages)
    kb = _checkups_nav_kb(callback_data.date, callback_data.page, total_pages)
    await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await query.answer()


async def _show_checkups_page(message: Message, iso_date: str, page: int) -> None:
    items, total = await local_db.get_checkups_by_date(
        iso_date,
        offset=page * _CHECKUPS_PAGE_SIZE,
        limit=_CHECKUPS_PAGE_SIZE,
    )
    if total == 0:
        await message.answer(f"Проверок за {_fmt_iso_date(iso_date)} не найдено.")
        return
    total_pages = max(1, (total + _CHECKUPS_PAGE_SIZE - 1) // _CHECKUPS_PAGE_SIZE)
    text = _format_checkups_page(items, iso_date, page, total, total_pages)
    kb = _checkups_nav_kb(iso_date, page, total_pages)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


def _format_checkups_page(
    items: list[dict], iso_date: str, page: int, total: int, total_pages: int
) -> str:
    lines = [
        f"<b>Проверки за {_fmt_iso_date(iso_date)}</b> "
        f"(стр. {page + 1}/{total_pages}, всего: {total})\n"
    ]
    for e in items:
        ca = e.get("checked_at", "")
        time_str = ca[11:16] if len(ca) >= 16 else "—"
        reading = e["reading"] or "—"
        comment = e["comment"] or "—"
        lines.append(
            f"{time_str}  {e['user_name'] or '—'}\n"
            f"<code>{_fmt_serial(e['serial_number'])}</code>  {e['name'] or '—'}\n"
            f"Статус: <b>{e['status']}</b>  Показания: {reading}\n"
            f"Комментарий: {comment}\n"
            "──────────────────────"
        )
    return "\n".join(lines)


def _build_checkups_date_kb(dates: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        text=_fmt_iso_date(d),
        callback_data=CheckupsDateCb(date=d).pack(),
    )] for d in dates]
    rows.append([InlineKeyboardButton(
        text="Выйти",
        callback_data=ExitCheckupsCb().pack(),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _checkups_nav_kb(iso_date: str, page: int, total_pages: int) -> InlineKeyboardMarkup | None:
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton(
            text="← Назад",
            callback_data=CheckupsPageCb(date=iso_date, page=page - 1).pack(),
        ))
    if page + 1 < total_pages:
        buttons.append(InlineKeyboardButton(
            text="Вперёд →",
            callback_data=CheckupsPageCb(date=iso_date, page=page + 1).pack(),
        ))
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


# ── Checkup: выход из режима ──────────────────────────────────────────────────


@router.message(
    StateFilter(CheckupForm.waiting_query, CheckupForm.waiting_status, CheckupForm.waiting_comment),
    F.text == "Выйти из режима",
)
async def checkup_exit(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Режим проверки завершён.", reply_markup=ReplyKeyboardRemove())


# ── Checkup: список непроверенных ─────────────────────────────────────────────


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
        offset=callback_data.page * _CHECKUP_PAGE_SIZE,
        limit=_CHECKUP_PAGE_SIZE,
    )
    total_pages = max(1, (total + _CHECKUP_PAGE_SIZE - 1) // _CHECKUP_PAGE_SIZE)
    text = _format_unchecked_page(items, callback_data.page, total, total_pages)
    kb = _unchecked_nav_kb(callback_data.page, total_pages)
    await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await query.answer()


async def _show_unchecked_page(message: Message, page: int) -> None:
    items, total = await local_db.get_unchecked_counters(
        offset=page * _CHECKUP_PAGE_SIZE,
        limit=_CHECKUP_PAGE_SIZE,
    )
    if total == 0:
        await message.answer("Нет данных о счётчиках. Убедитесь, что кэш синхронизирован.")
        return
    total_pages = max(1, (total + _CHECKUP_PAGE_SIZE - 1) // _CHECKUP_PAGE_SIZE)
    text = _format_unchecked_page(items, page, total, total_pages)
    kb = _unchecked_nav_kb(page, total_pages)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


def _format_unchecked_page(items: list[dict], page: int, total: int, total_pages: int) -> str:
    lines = [
        f"<b>Непроверенные счётчики</b> "
        f"(стр. {page + 1}/{total_pages}, всего: {total})\n"
    ]
    offset = page * _CHECKUP_PAGE_SIZE
    for i, item in enumerate(items, start=offset + 1):
        date_str = _fmt_check_date(item.get("last_check"))
        lines.append(f"{i}. {item['name'] or item['serial_number']} — {date_str}")
    return "\n".join(lines)


def _unchecked_nav_kb(page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="← Назад",
            callback_data=CheckupPageCb(page=page - 1).pack(),
        ))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(
            text="Вперёд →",
            callback_data=CheckupPageCb(page=page + 1).pack(),
        ))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(
        text="Выйти",
        callback_data=ExitUncheckedCb().pack(),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Checkup: поиск счётчика (текст) ──────────────────────────────────────────


@router.message(CheckupForm.waiting_query, F.text)
async def checkup_handle_text(message: Message, state: FSMContext) -> None:
    serial = message.text.strip()
    if not serial:
        return
    m = _NAME_CODE_RE.match(serial)
    if m:
        left = m.group(1).zfill(2)
        right = m.group(2).zfill(2)
        row = await db.get_counter_by_name_code(left, right)
    else:
        row = await db.get_counter_by_serial(serial)
    await _checkup_show_result(message, state, row, serial)


# ── Checkup: поиск счётчика (фото) ───────────────────────────────────────────


@router.message(CheckupForm.waiting_query, F.photo)
async def checkup_handle_photo(message: Message, state: FSMContext, bot: Bot) -> None:
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
        return

    barcode = results[0].text.strip()
    row = await db.get_counter_by_barcode(barcode)
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
        f"<b>Серийный номер:</b> <code>{_fmt_serial(row['SerialNumber'])}</code>\n"
        f"<b>Название:</b> {row['Name']}\n"
        f"<b>Состояние:</b> {state_val}\n"
        f"<b>Потребление:</b> {consumption}\n"
        f"<b>Дата обновления:</b> {update_time}\n\n"
        f"Выберите результат проверки:",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ── Checkup: выбор статуса (callback) ────────────────────────────────────────


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
        await query.message.answer(f"Отменено.\n\n{_CHECKUP_NEXT}")
        return

    if callback_data.status == "ok":
        await state.update_data(checkup_status="Исправен")
        await query.answer()
        text = await _save_checkup(query.from_user, state, "Проверен, без нареканий")
        await query.message.answer(text, parse_mode="HTML")
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


# ── Checkup: ввод комментария и сохранение ────────────────────────────────────


@router.message(CheckupForm.waiting_comment, F.text)
async def checkup_handle_comment(message: Message, state: FSMContext) -> None:
    text = await _save_checkup(message.from_user, state, message.text.strip() or None)
    await message.answer(text, parse_mode="HTML")


@router.callback_query(SkipCheckupCb.filter(F.field == "comment"))
async def cb_skip_checkup_comment(query: CallbackQuery, state: FSMContext) -> None:
    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer()
    text = await _save_checkup(query.from_user, state, None)
    await query.message.answer(text, parse_mode="HTML")


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
        f"<b>Серийный:</b> <code>{_fmt_serial(serial)}</code>  "
        f"<b>Статус:</b> {status}\n"
        f"<b>Комментарий:</b> {comment or '—'}\n\n"
        f"{_CHECKUP_NEXT}"
    )


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
    m = _NAME_CODE_RE.match(serial)
    if m:
        left = m.group(1).zfill(2)
        right = m.group(2).zfill(2)
        row = await db.get_counter_by_name_code(left, right)
    else:
        row = await db.get_counter_by_serial(serial)
    await _reading_store_counter(message, state, row, serial)


@router.message(ReadingForm.waiting_query, F.photo)
async def reading_handle_photo(message: Message, state: FSMContext, bot: Bot) -> None:
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
        return

    barcode = results[0].text.strip()
    row = await db.get_counter_by_barcode(barcode)
    await _reading_store_counter(message, state, row, barcode)


def _reading_date_kb() -> InlineKeyboardMarkup:
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    first_current = today.replace(day=1)
    last_prev = first_current - timedelta(days=1)
    first_prev = last_prev.replace(day=1)

    def _btn(label: str, d) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=f"{label} · {d.strftime('%d.%m.%Y')}",
            callback_data=ReadingDateCb(date=d.strftime("%Y-%m-%d")).pack(),
        )

    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("1-й пред. месяца", first_prev)],
        [_btn("Посл. пред. месяца", last_prev)],
        [_btn("1-й тек. месяца", first_current)],
        [_btn("Вчера", yesterday)],
        [_btn("Сегодня", today)],
    ])


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
        f"<b>{row['Name']}</b>  <code>{_fmt_serial(row['SerialNumber'])}</code>\n\n"
        "Выберите дату или введите в формате <b>дд.мм.гггг</b>:",
        parse_mode="HTML",
        reply_markup=_reading_date_kb(),
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



@router.message(ReadingForm.waiting_date, F.text)
async def reading_handle_date(message: Message, state: FSMContext) -> None:
    iso_date = _parse_input_date(message.text.strip())
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

    col_serial = _fmt_serial(serial)
    col_before = _fmt_consumption(before)
    col_third = _fmt_consumption(on_date) if on_date else (_fmt_consumption(after) if after else "нет")
    col_diff = _diff_str(col_before, col_third)

    H1, H2, H3, H4 = "Серийный", "До", "На дату", "Итого"
    w1 = max(len(H1), len(col_serial))
    w2 = max(len(H2), len(col_before))
    w3 = max(len(H3), len(col_third))

    lines = [
        f"{H1:<{w1}}  {H2:<{w2}}  {H3:<{w3}}  {H4}",
        f"{'-'*w1}  {'-'*w2}  {'-'*w3}  {'-'*len(H4)}",
        f"{col_serial:<{w1}}  {col_before:<{w2}}  {col_third:<{w3}}  {col_diff}",
    ]
    date_str = _fmt_iso_date(iso_date)
    header = f"<b>Показания на {date_str}</b>\n\n<b>Название:</b> {name}\n\n"
    return header + "<pre>" + "\n".join(lines) + "</pre>"


# ── /period + слово "Период" ──────────────────────────────────────────────────

_PERIOD_MAX_DAYS = 90


@router.message(Command("period"))
@router.message(F.text.func(lambda t: bool(t) and t.strip().lower() == "период"))
async def cmd_period(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PeriodForm.waiting_query)
    await message.answer(
        "<b>Показания за период</b>\n\n"
        "Введите серийный номер, код N.М или отправьте фото со штрих-кодом.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(PeriodForm.waiting_query, F.text)
async def period_handle_text(message: Message, state: FSMContext) -> None:
    serial = message.text.strip()
    if not serial:
        return
    m = _NAME_CODE_RE.match(serial)
    if m:
        left = m.group(1).zfill(2)
        right = m.group(2).zfill(2)
        row = await db.get_counter_by_name_code(left, right)
    else:
        row = await db.get_counter_by_serial(serial)
    await _period_store_counter(message, state, row, serial)


@router.message(PeriodForm.waiting_query, F.photo)
async def period_handle_photo(message: Message, state: FSMContext, bot: Bot) -> None:
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
        return

    barcode = results[0].text.strip()
    row = await db.get_counter_by_barcode(barcode)
    await _period_store_counter(message, state, row, barcode)


def _period_preset_kb() -> InlineKeyboardMarkup:
    today = datetime.now().date()
    first_current = today.replace(day=1)
    last_prev = first_current - timedelta(days=1)
    first_prev = last_prev.replace(day=1)
    monday = today - timedelta(days=today.weekday())
    prev_monday = monday - timedelta(days=7)
    prev_sunday = monday - timedelta(days=1)

    def _btn(label: str, s, e) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=label,
            callback_data=PeriodPresetCb(
                start=s.strftime("%Y-%m-%d"),
                end=e.strftime("%Y-%m-%d"),
            ).pack(),
        )

    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(
            f"Пред. месяц · {first_prev.strftime('%d.%m')}–{last_prev.strftime('%d.%m.%Y')}",
            first_prev, last_prev,
        )],
        [_btn(
            f"Тек. месяц · {first_current.strftime('%d.%m')}–{today.strftime('%d.%m.%Y')}",
            first_current, today,
        )],
        [_btn(
            f"Пред. неделя · {prev_monday.strftime('%d.%m')}–{prev_sunday.strftime('%d.%m.%Y')}",
            prev_monday, prev_sunday,
        )],
        [_btn(
            f"Текущая неделя · {monday.strftime('%d.%m')}–{today.strftime('%d.%m.%Y')}",
            monday, today,
        )],
    ])


async def _period_store_counter(
    message: Message, state: FSMContext, row: dict | None, query: str
) -> None:
    if row is None:
        await message.answer(
            f"Счётчик <code>{query}</code> не найден. Попробуйте ещё раз.",
            parse_mode="HTML",
        )
        return
    await state.update_data(
        period_counter_id=row["Obj_Id_Counter"],
        period_name=row["Name"],
        period_serial=row["SerialNumber"],
    )
    await state.set_state(PeriodForm.waiting_period)
    await message.answer(
        f"<b>{row['Name']}</b>  <code>{_fmt_serial(row['SerialNumber'])}</code>\n\n"
        f"Выберите период или введите в формате <b>дд.мм.гггг - дд.мм.гггг</b>\n"
        f"(максимум {_PERIOD_MAX_DAYS} дней):",
        parse_mode="HTML",
        reply_markup=_period_preset_kb(),
    )


@router.callback_query(PeriodPresetCb.filter(), StateFilter(PeriodForm.waiting_period))
async def cb_period_preset(
    query: CallbackQuery, callback_data: PeriodPresetCb, state: FSMContext
) -> None:
    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer()
    data = await state.get_data()
    await state.clear()

    counter_id = data["period_counter_id"]
    name = data["period_name"]
    serial = data["period_serial"]

    start_iso, end_iso = callback_data.start, callback_data.end
    raw = await db.get_consumption_for_period(counter_id, start_iso, end_iso)
    rows = _build_period_rows(start_iso, end_iso, raw["pre"], raw["in_period"], raw["post"])
    text = _format_period_table(name, serial, start_iso, end_iso, rows)
    await query.message.answer(text, parse_mode="HTML")


@router.message(PeriodForm.waiting_period, F.text)
async def period_handle_period(message: Message, state: FSMContext) -> None:
    parsed = _parse_period_input(message.text.strip())
    if parsed is None:
        await message.answer(
            "Неверный формат. Введите период как <b>дд.мм.гггг - дд.мм.гггг</b>:",
            parse_mode="HTML",
        )
        return

    start_iso, end_iso, corrections = parsed
    start_dt = datetime.strptime(start_iso, "%Y-%m-%d")
    end_dt = datetime.strptime(end_iso, "%Y-%m-%d")

    if end_dt < start_dt:
        await message.answer(
            "Начало периода не может быть позже его конца. Попробуйте снова:"
        )
        return

    delta = (end_dt - start_dt).days + 1
    if delta > _PERIOD_MAX_DAYS:
        await message.answer(
            f"Период не может превышать {_PERIOD_MAX_DAYS} дней "
            f"(запрошено {delta}). Попробуйте снова:"
        )
        return

    data = await state.get_data()
    await state.clear()

    counter_id = data["period_counter_id"]
    name = data["period_name"]
    serial = data["period_serial"]

    raw = await db.get_consumption_for_period(counter_id, start_iso, end_iso)
    rows = _build_period_rows(start_iso, end_iso, raw["pre"], raw["in_period"], raw["post"])
    text = _format_period_table(name, serial, start_iso, end_iso, rows, corrections)
    await _send_long(message, text)


def _parse_date_clamped(text: str) -> tuple[str, bool] | None:
    """Парсит дату с автокоррекцией числа до последнего дня месяца.
    Возвращает (iso_date, was_clamped) или None.
    """
    direct = _parse_input_date(text.strip())
    if direct:
        return direct, False

    m = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$', text.strip())
    if m:
        try:
            day, month = int(m.group(1)), int(m.group(2))
            yr = int(m.group(3))
            if len(m.group(3)) == 2:
                yr += 2000 if yr < 70 else 1900
            if 1 <= month <= 12 and day >= 1:
                max_day = calendar.monthrange(yr, month)[1]
                if day > max_day:
                    return datetime(yr, month, max_day).strftime("%Y-%m-%d"), True
        except (ValueError, OverflowError):
            pass

    return None


def _parse_period_input(text: str) -> tuple[str, str, list[str]] | None:
    """Извлекает две даты из строки вида 'дд.мм.гггг - дд.мм.гггг'.
    Возвращает (start_iso, end_iso, corrections) или None.
    """
    matches = re.findall(r'\d{1,2}\.\d{1,2}\.\d{2,4}|\d{4}-\d{2}-\d{2}', text)
    if len(matches) < 2:
        return None

    res_s = _parse_date_clamped(matches[0])
    res_e = _parse_date_clamped(matches[1])
    if not res_s or not res_e:
        return None

    start_iso, s_clamped = res_s
    end_iso, e_clamped = res_e
    corrections: list[str] = []
    if s_clamped:
        corrections.append(f"{matches[0]} → {_fmt_iso_date(start_iso)}")
    if e_clamped:
        corrections.append(f"{matches[1]} → {_fmt_iso_date(end_iso)}")
    return start_iso, end_iso, corrections


def _build_period_rows(
    start_date: str,
    end_date: str,
    pre: dict | None,
    in_period: list[dict],
    post: dict | None,
) -> list[dict]:
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    rows = []
    current = start_dt
    while current <= end_dt:
        day_end = current + timedelta(days=1)

        before = None
        for rec in reversed(in_period):
            ut = rec.get("UpdateTime")
            if ut and ut < current:
                before = rec
                break
        if before is None:
            before = pre

        on_date = None
        for rec in reversed(in_period):
            ut = rec.get("UpdateTime")
            if ut and current <= ut < day_end:
                on_date = rec
                break

        after = None
        if not on_date:
            for rec in in_period:
                ut = rec.get("UpdateTime")
                if ut and ut >= day_end:
                    after = rec
                    break
            if after is None:
                after = post

        rows.append({"date": current, "before": before, "on_date": on_date, "after": after})
        current += timedelta(days=1)
    return rows


def _format_period_table(
    name: str, serial: str, start_date: str, end_date: str, rows: list[dict],
    corrections: list[str] | None = None,
) -> str:
    col_date = [r["date"].strftime("%d.%m.%Y") for r in rows]
    col_before: list[str] = []
    col_third: list[str] = []

    for r in rows:
        col_before.append(_fmt_consumption(r["before"]))
        if r["on_date"]:
            col_third.append(_fmt_consumption(r["on_date"]))
        elif r["after"]:
            col_third.append(_fmt_consumption(r["after"]))
        else:
            col_third.append("нет")

    col_diff = [_diff_str(b, t) for b, t in zip(col_before, col_third)]

    H1, H2, H3, H4 = "Дата", "До", "На дату", "Итого"
    w1 = max(len(H1), max((len(v) for v in col_date), default=0))
    w2 = max(len(H2), max((len(v) for v in col_before), default=0))
    w3 = max(len(H3), max((len(v) for v in col_third), default=0))
    w4 = max(len(H4), max((len(v) for v in col_diff), default=0))

    lines = [
        f"{H1:<{w1}}  {H2:<{w2}}  {H3:<{w3}}  {H4}",
        f"{'-'*w1}  {'-'*w2}  {'-'*w3}  {'-'*w4}",
    ]
    for d, b, t, di in zip(col_date, col_before, col_third, col_diff):
        lines.append(f"{d:<{w1}}  {b:<{w2}}  {t:<{w3}}  {di}")

    # Итог: первые и последние показания за период
    non_empty = [v for v in col_third if v != "нет"]
    first_val = non_empty[0] if non_empty else "нет"
    last_val = non_empty[-1] if len(non_empty) > 1 else first_val

    HS1, HS2 = "Первые", "Последние"
    ws1 = max(len(HS1), len(first_val))
    ws2 = max(len(HS2), len(last_val))
    lines += [
        "",
        f"{HS1:<{ws1}}  {HS2}",
        f"{'-'*ws1}  {'-'*ws2}",
        f"{first_val:<{ws1}}  {last_val}",
    ]

    correction_note = ""
    if corrections:
        correction_note = (
            "⚠️ Дата введена неправильно, приблизил к ближайшей: "
            + "; ".join(corrections) + "\n\n"
        )
    header = (
        f"<b>Показания за период</b>\n\n"
        f"{correction_note}"
        f"<b>Название:</b> {name}\n"
        f"<b>Серийный:</b> <code>{_fmt_serial(serial)}</code>\n"
        f"<b>Период:</b> {_fmt_iso_date(start_date)} — {_fmt_iso_date(end_date)}\n\n"
    )
    return header + "<pre>" + "\n".join(lines) + "</pre>"


# ── /monthly + слово "Месяцы" ─────────────────────────────────────────────────

_MONTH_NAMES = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
_MONTHLY_MAX_YEARS = 5


@router.message(Command("monthly"))
@router.message(F.text.func(lambda t: bool(t) and t.strip().lower() == "месяцы"))
async def cmd_monthly(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(MonthlyForm.waiting_query)
    await message.answer(
        "<b>Помесячный отчёт</b>\n\n"
        "Введите серийный номер, код N.М или отправьте фото со штрих-кодом.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(MonthlyForm.waiting_query, F.text)
async def monthly_handle_text(message: Message, state: FSMContext) -> None:
    serial = message.text.strip()
    if not serial:
        return
    m = _NAME_CODE_RE.match(serial)
    if m:
        left = m.group(1).zfill(2)
        right = m.group(2).zfill(2)
        row = await db.get_counter_by_name_code(left, right)
    else:
        row = await db.get_counter_by_serial(serial)
    await _monthly_store_counter(message, state, row, serial)


@router.message(MonthlyForm.waiting_query, F.photo)
async def monthly_handle_photo(message: Message, state: FSMContext, bot: Bot) -> None:
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
        return

    barcode = results[0].text.strip()
    row = await db.get_counter_by_barcode(barcode)
    await _monthly_store_counter(message, state, row, barcode)


async def _monthly_store_counter(
    message: Message, state: FSMContext, row: dict | None, query: str
) -> None:
    if row is None:
        await message.answer(
            f"Счётчик <code>{query}</code> не найден. Попробуйте ещё раз.",
            parse_mode="HTML",
        )
        return
    await state.update_data(
        monthly_counter_id=row["Obj_Id_Counter"],
        monthly_name=row["Name"],
        monthly_serial=row["SerialNumber"],
    )
    await state.set_state(MonthlyForm.waiting_year)
    await message.answer(
        f"<b>{row['Name']}</b>  <code>{_fmt_serial(row['SerialNumber'])}</code>\n\n"
        f"Введите год или диапазон лет в формате <b>гггг</b> или <b>гггг-гггг</b>\n"
        f"(максимум {_MONTHLY_MAX_YEARS} лет):",
        parse_mode="HTML",
    )


@router.message(MonthlyForm.waiting_year, F.text)
async def monthly_handle_year(message: Message, state: FSMContext) -> None:
    parsed = _parse_year_input(message.text.strip())
    if parsed is None:
        await message.answer(
            "Неверный формат. Введите год как <b>гггг</b> или <b>гггг-гггг</b>:",
            parse_mode="HTML",
        )
        return

    start_year, end_year = parsed
    if end_year < start_year:
        await message.answer("Начало диапазона не может быть позже конца. Попробуйте снова:")
        return

    if end_year - start_year + 1 > _MONTHLY_MAX_YEARS:
        await message.answer(
            f"Диапазон не может превышать {_MONTHLY_MAX_YEARS} лет "
            f"(запрошено {end_year - start_year + 1}). Попробуйте снова:"
        )
        return

    data = await state.get_data()
    await state.clear()

    counter_id = data["monthly_counter_id"]
    name = data["monthly_name"]
    serial = data["monthly_serial"]

    start_iso = f"{start_year}-01-01"
    end_iso = f"{end_year}-12-31"
    raw = await db.get_consumption_for_period(counter_id, start_iso, end_iso)
    rows = _build_monthly_rows(start_year, 1, end_year, 12, raw["pre"], raw["in_period"], raw["post"])
    text = _format_monthly_table(name, serial, start_year, end_year, rows)
    await _send_long(message, text)


def _parse_year_input(text: str) -> tuple[int, int] | None:
    if re.fullmatch(r'\d{4}', text):
        y = int(text)
        return y, y
    m = re.fullmatch(r'(\d{4})\s*[-–—]\s*(\d{4})', text)
    if not m:
        m = re.fullmatch(r'(\d{4})\s+(\d{4})', text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _build_monthly_rows(
    start_year: int, start_month: int,
    end_year: int, end_month: int,
    pre: dict | None,
    in_period: list[dict],
    post: dict | None,
) -> list[dict]:
    rows = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        month_start = datetime(year, month, 1)
        month_end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)

        before = None
        for rec in reversed(in_period):
            ut = rec.get("UpdateTime")
            if ut and ut < month_start:
                before = rec
                break
        if before is None:
            before = pre

        in_month = [
            r for r in in_period
            if r.get("UpdateTime") and month_start <= r["UpdateTime"] < month_end
        ]
        displayed = in_month[-1] if in_month else None

        rows.append({"year": year, "month": month, "before": before, "displayed": displayed})
        month += 1
        if month > 12:
            month = 1
            year += 1
    return rows


def _format_monthly_table(
    name: str, serial: str, start_year: int, end_year: int, rows: list[dict]
) -> str:
    def _before_val(rec: dict | None) -> str:
        if not rec:
            return "0"
        c = rec.get("Consumption")
        return str(round(c)) if c is not None else "0"

    cur_ym = (datetime.now().year, datetime.now().month)
    rows = [r for r in rows if (r["year"], r["month"]) <= cur_ym]

    col_year = [str(r["year"]) for r in rows]
    col_month = [_MONTH_NAMES[r["month"]] for r in rows]
    col_before = [_before_val(r["before"]) for r in rows]
    col_disp = [_fmt_consumption(r["displayed"]) for r in rows]
    col_diff = [_diff_str(b, d) for b, d in zip(col_before, col_disp)]

    H1, H2, H3, H4, H5 = "Год", "Месяц", "До", "За месяц", "Итого"
    w1 = max(len(H1), max((len(v) for v in col_year), default=0))
    w2 = max(len(H2), max((len(v) for v in col_month), default=0))
    w3 = max(len(H3), max((len(v) for v in col_before), default=0))
    w4 = max(len(H4), max((len(v) for v in col_disp), default=0))
    w5 = max(len(H5), max((len(v) for v in col_diff), default=0))

    lines = [
        f"{H1:<{w1}}  {H2:<{w2}}  {H3:<{w3}}  {H4:<{w4}}  {H5}",
        f"{'-'*w1}  {'-'*w2}  {'-'*w3}  {'-'*w4}  {'-'*w5}",
    ]
    for yr, mn, b, d, di in zip(col_year, col_month, col_before, col_disp, col_diff):
        lines.append(f"{yr:<{w1}}  {mn:<{w2}}  {b:<{w3}}  {d:<{w4}}  {di}")

    period_str = str(start_year) if start_year == end_year else f"{start_year}–{end_year}"
    header = (
        f"<b>Помесячный отчёт</b>\n\n"
        f"<b>Название:</b> {name}\n"
        f"<b>Серийный:</b> <code>{_fmt_serial(serial)}</code>\n"
        f"<b>Период:</b> {period_str}\n\n"
    )
    return header + "<pre>" + "\n".join(lines) + "</pre>"


# ── Фото ──────────────────────────────────────────────────────────────────────


@router.message(F.photo, StateFilter(None))
async def handle_photo(message: Message, bot: Bot) -> None:
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
        return

    barcode = results[0].text.strip()
    logger.info("Barcode decoded: %s", barcode)
    row = await db.get_counter_by_barcode(barcode)
    await _show_counter(message, row, barcode)


# ── Текст: серийный номер ─────────────────────────────────────────────────────


@router.message(
    F.text,
    ~F.text.startswith("/"),
    F.text.func(lambda t: bool(t) and t.strip().lower() not in _KEYWORDS),
    StateFilter(None),
)
async def handle_text(message: Message) -> None:
    serial = message.text.strip()
    if not serial:
        return
    m = _NAME_CODE_RE.match(serial)
    if m:
        left = m.group(1).zfill(2)
        right = m.group(2).zfill(2)
        row = await db.get_counter_by_name_code(left, right)
        await _show_counter(message, row, serial)
        return
    row = await db.get_counter_by_serial(serial)
    await _show_counter(message, row, serial)


# ── Показ информации о счётчике ───────────────────────────────────────────────


async def _show_counter(message: Message, row: dict | None, query: str) -> None:
    if row is None:
        await message.answer(
            f"Счётчик <code>{query}</code> не найден.",
            parse_mode="HTML",
        )
        return

    # Ежедневная синхронизация кэша счётчиков
    if not await local_db.should_sync_today():
        counters = await db.get_all_counters()
        await local_db.sync_counters(counters)

    consumption = str(row["Consumption"]) if row["Consumption"] is not None else "—"
    state_val = str(row["State"]) if row["State"] is not None else "—"
    update_time = (
        row["UpdateTime"].strftime("%d.%m.%Y %H:%M") if row["UpdateTime"] else "—"
    )

    await local_db.update_counter_scan(row["SerialNumber"], consumption, state_val)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Записать в блокнот",
                    callback_data=SaveNotebookCb(serial=row["SerialNumber"]).pack(),
                )
            ]
        ]
    )
    await message.answer(
        f"<b>Счётчик найден</b>\n\n"
        f"<b>Серийный номер:</b> <code>{_fmt_serial(row['SerialNumber'])}</code>\n"
        f"<b>Название:</b> {row['Name']}\n"
        f"<b>Состояние:</b> {state_val}\n"
        f"<b>Потребление:</b> {consumption}\n"
        f"<b>Дата обновления:</b> {update_time}",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ── Callback: сохранить в блокнот ─────────────────────────────────────────────


@router.callback_query(SaveNotebookCb.filter())
async def cb_save_notebook(
    query: CallbackQuery, callback_data: SaveNotebookCb, state: FSMContext
) -> None:
    row = await db.get_counter_by_serial(callback_data.serial)
    if row is None:
        await query.answer("Счётчик не найден.", show_alert=True)
        return

    consumption = str(row["Consumption"]) if row["Consumption"] is not None else None
    state_val = str(row["State"]) if row["State"] is not None else None

    entry_id = await local_db.add_notebook_entry(
        user_id=query.from_user.id,
        serial=row["SerialNumber"],
        name=row["Name"],
        state=state_val,
        db_consumption=consumption,
    )
    _pending_entry[query.from_user.id] = entry_id

    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer("Записано!")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пропустить",
                    callback_data=SkipReadingCb(entry_id=entry_id).pack(),
                )
            ]
        ]
    )
    prompt_msg = await query.message.answer(
        "Введите <b>комментарий</b> к записи (показания, заметки и т.д.) "
        "или нажмите «Пропустить»:",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await state.update_data(prompt_msg_id=prompt_msg.message_id)
    await state.set_state(Form.waiting_comment)


# ── FSM: ввод реальных показаний ──────────────────────────────────────────────


@router.message(Form.waiting_comment, F.text)
async def handle_comment(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    entry_id = _pending_entry.pop(message.from_user.id, None)
    comment = message.text.strip()
    if entry_id and comment:
        await local_db.update_notebook_comment(entry_id, comment)
    await state.clear()
    if prompt_msg_id:
        try:
            await bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                reply_markup=None,
            )
        except Exception:
            pass
    await message.answer(
        f"Запись сохранена. Комментарий: <b>{comment or 'не указано'}</b>",
        parse_mode="HTML",
    )


@router.callback_query(SkipReadingCb.filter())
async def cb_skip_reading(query: CallbackQuery, state: FSMContext) -> None:
    _pending_entry.pop(query.from_user.id, None)
    await state.clear()
    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer("Пропущено.")
    await query.message.answer("Запись сохранена.")


# ── Вспомогательные ───────────────────────────────────────────────────────────


def _fmt_serial(s: str) -> str:
    return s.zfill(8)


def _diff_str(before: str, reading: str) -> str:
    """Целочисленная разница reading - before; '—' если одно из значений не число."""
    try:
        return str(int(reading) - int(before))
    except (ValueError, TypeError):
        return "—"


def _fmt_consumption(rec: dict | None) -> str:
    if not rec:
        return "нет"
    c = rec.get("Consumption")
    if c is None:
        return "нет"
    return str(round(c))


def _fmt_iso_date(iso_date: str) -> str:
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return iso_date


def _parse_input_date(text: str) -> str | None:
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _fmt_check_date(iso_str: str | None) -> str:
    if not iso_str:
        return "нет проверки"
    for fmt in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(iso_str, fmt).strftime("%d.%m.%Y")
        except ValueError:
            pass
    return iso_str


def _plural(n: int, one: str, few: str, many: str) -> str:
    if 11 <= n % 100 <= 19:
        return many
    r = n % 10
    if r == 1:
        return one
    if 2 <= r <= 4:
        return few
    return many


async def _send_long(message: Message, text: str) -> None:
    limit = 4096
    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        await message.answer(text[:split_at], parse_mode="HTML")
        text = text[split_at:].lstrip("\n")
    if text:
        await message.answer(text, parse_mode="HTML")


# ── main ──────────────────────────────────────────────────────────────────────


LOG_DIR = Path(__file__).parent / "logs"
LOG_KEEP_DAYS = 14
_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Консоль
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter(_FMT))
    root.addHandler(sh)

    # Файл с ежедневной ротацией и удалением старых логов
    fh = logging.handlers.TimedRotatingFileHandler(
        filename=LOG_DIR / "bot.log",
        when="midnight",
        interval=1,
        backupCount=LOG_KEEP_DAYS,
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter(_FMT))
    root.addHandler(fh)


async def main() -> None:
    _setup_logging()

    await local_db.init_local_db()
    await db.init_pool()
    logger.info("DB pool ready")

    if not await local_db.should_sync_today():
        counters = await db.get_all_counters()
        await local_db.sync_counters(counters)
        logger.info("Counter cache synced: %d entries", len(counters))

    bot = Bot(token=settings.BOT_TOKEN)
    await bot.set_my_commands([
        BotCommand(command="help",     description="Справка"),
        BotCommand(command="notebook", description="Блокнот"),
        BotCommand(command="clear",    description="Очистить блокнот"),
        BotCommand(command="checkup",  description="Режим проверки"),
        BotCommand(command="checkups", description="Журнал проверок"),
        BotCommand(command="reading",  description="Показания на дату"),
        BotCommand(command="period",   description="Показания за период"),
        BotCommand(command="monthly",  description="Помесячный отчёт"),
    ])
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(AccessMiddleware())
    dp.callback_query.middleware(AccessMiddleware())
    dp.include_router(router)

    try:
        logger.info("Bot started")
        await dp.start_polling(bot)
    finally:
        await db.close_pool()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
