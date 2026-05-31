"""CallbackData-классы для inline-кнопок."""

from aiogram.filters.callback_data import CallbackData


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


class CalendarDayCb(CallbackData, prefix="cal"):
    year: int
    month: int
    day: int
    scope: str  # "reading" | "checkups"


class CalendarNavCb(CallbackData, prefix="cln"):
    year: int
    month: int
    scope: str
    ctx: str = ""  # для scope="period_end": ISO-дата начала периода


class CalendarIgnoreCb(CallbackData, prefix="cli"):
    pass


class CalendarOpenCb(CallbackData, prefix="clo"):
    scope: str


class YearPickCb(CallbackData, prefix="yrp"):
    year: int
    scope: str  # "year_start" | "year_end"
    ctx: str = ""  # для year_end: год начала в виде строки


class YearNavCb(CallbackData, prefix="yrn"):
    base: int   # первый год в гриде
    scope: str
    ctx: str = ""


class CheckupsPageCb(CallbackData, prefix="csp"):
    date: str  # ISO YYYY-MM-DD
    page: int


class ExitCheckupsCb(CallbackData, prefix="excs"):
    pass


# ── Доступ ─────────────────────────────────────────────────────────────────────


class RequestAccessCb(CallbackData, prefix="req"):
    """Кнопка «Запросить доступ» для неизвестного пользователя."""
    pass


class AccessDecisionCb(CallbackData, prefix="acd"):
    """Решение администратора по заявке."""
    user_id: int
    approve: int  # 1 = принять, 0 = отклонить


class UserDeleteCb(CallbackData, prefix="usd"):
    """Удаление пользователя из списка доступа (admin)."""
    user_id: int


class UserAddCb(CallbackData, prefix="usa"):
    """Кнопка «Добавить вручную» (admin)."""
    pass
