"""Построители клавиатур: календарь, выбор года, пресеты периода, быстрые даты,
постраничная навигация и статичные клавиатуры."""

import calendar
from datetime import datetime, timedelta

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from callbacks import (
    CalendarDayCb,
    CalendarIgnoreCb,
    CalendarNavCb,
    CalendarOpenCb,
    CheckupPageCb,
    CheckupsDateCb,
    CheckupsPageCb,
    ExitCheckupsCb,
    ExitUncheckedCb,
    PeriodPresetCb,
    ReadingDateCb,
    YearNavCb,
    YearPickCb,
)
from constants import CAL_WEEKDAYS, MONTH_NAMES, YEAR_COLS, YEAR_ROWS
from formatting import fmt_iso_date

# Статичная reply-клавиатура режима проверки.
CHECKUP_KB = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Непроверенные"),
            KeyboardButton(text="Выйти из режима"),
        ]
    ],
    resize_keyboard=True,
)


# ── Календарь ─────────────────────────────────────────────────────────────────


def calendar_quick_rows() -> list[list[InlineKeyboardButton]]:
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    first_current = today.replace(day=1)

    def _btn(label: str, d) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=f"{label} · {d.strftime('%d.%m.%Y')}",
            callback_data=ReadingDateCb(date=d.strftime("%Y-%m-%d")).pack(),
        )

    return [
        [_btn("На первое", first_current)],
        [_btn("Вчера", yesterday)],
        [_btn("Сейчас", today)],
    ]


def calendar_exit_row() -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(text="Выйти", callback_data=ExitCheckupsCb().pack())]]


def calendar_period_end_rows(start_iso: str) -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(
        text=f"Начало: {fmt_iso_date(start_iso)}",
        callback_data=CalendarIgnoreCb().pack(),
    )]]


def build_calendar_kb(
    year: int, month: int, scope: str,
    extra_rows: list[list[InlineKeyboardButton]] | None = None,
    ctx: str = "",
) -> InlineKeyboardMarkup:
    prev_m = month - 1 or 12
    prev_y = year - (1 if month == 1 else 0)
    next_m = month % 12 + 1
    next_y = year + (1 if month == 12 else 0)

    _ign = CalendarIgnoreCb().pack()
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="◀", callback_data=CalendarNavCb(year=prev_y, month=prev_m, scope=scope, ctx=ctx).pack()),
            InlineKeyboardButton(text=f"{MONTH_NAMES[month]} {year}", callback_data=_ign),
            InlineKeyboardButton(text="▶", callback_data=CalendarNavCb(year=next_y, month=next_m, scope=scope, ctx=ctx).pack()),
        ],
        [InlineKeyboardButton(text=d, callback_data=_ign) for d in CAL_WEEKDAYS],
    ]
    ign_btn = InlineKeyboardButton(text=" ", callback_data=_ign)
    for week in calendar.monthcalendar(year, month):
        rows.append([
            InlineKeyboardButton(
                text=str(day),
                callback_data=CalendarDayCb(year=year, month=month, day=day, scope=scope).pack(),
            ) if day else ign_btn
            for day in week
        ])
    if extra_rows:
        rows.extend(extra_rows)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reading_date_kb() -> InlineKeyboardMarkup:
    today = datetime.now().date()
    return build_calendar_kb(today.year, today.month, "reading", extra_rows=calendar_quick_rows())


# ── Период ──────────────────────────────────────────────────────────────────


def period_preset_kb() -> InlineKeyboardMarkup:
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
        [InlineKeyboardButton(
            text="📅 Выбрать период в календаре",
            callback_data=CalendarOpenCb(scope="period_start").pack(),
        )],
    ])


# ── Выбор года ────────────────────────────────────────────────────────────────


def year_extra_rows_end(start_year: int) -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(
        text=f"Начало: {start_year}",
        callback_data=CalendarIgnoreCb().pack(),
    )]]


def build_year_kb(
    base: int, scope: str,
    extra_rows: list[list[InlineKeyboardButton]] | None = None,
    ctx: str = "",
    max_year: int | None = None,
) -> InlineKeyboardMarkup:
    count = YEAR_COLS * YEAR_ROWS
    last = base + count - 1
    _ign = CalendarIgnoreCb().pack()
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="◀", callback_data=YearNavCb(base=base - count, scope=scope, ctx=ctx).pack()),
            InlineKeyboardButton(text=f"{base}–{last}", callback_data=_ign),
            InlineKeyboardButton(text="▶", callback_data=YearNavCb(base=base + count, scope=scope, ctx=ctx).pack()),
        ]
    ]
    year = base
    for _ in range(YEAR_ROWS):
        row = []
        for _ in range(YEAR_COLS):
            if max_year is not None and year > max_year:
                row.append(InlineKeyboardButton(text=" ", callback_data=_ign))
            else:
                row.append(InlineKeyboardButton(
                    text=str(year),
                    callback_data=YearPickCb(year=year, scope=scope, ctx=ctx).pack(),
                ))
            year += 1
        rows.append(row)
    if extra_rows:
        rows.extend(extra_rows)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Журнал проверок ─────────────────────────────────────────────────────────


def build_checkups_date_kb(dates: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        text=fmt_iso_date(d),
        callback_data=CheckupsDateCb(date=d).pack(),
    )] for d in dates]
    rows.append([InlineKeyboardButton(
        text="📅 Выбрать в календаре",
        callback_data=CalendarOpenCb(scope="checkups").pack(),
    )])
    rows.append([InlineKeyboardButton(
        text="Выйти",
        callback_data=ExitCheckupsCb().pack(),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def checkups_nav_kb(iso_date: str, page: int, total_pages: int) -> InlineKeyboardMarkup | None:
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


# ── Непроверенные счётчики ───────────────────────────────────────────────────


def unchecked_nav_kb(page: int, total_pages: int) -> InlineKeyboardMarkup:
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
