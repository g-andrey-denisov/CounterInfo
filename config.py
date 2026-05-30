import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _parse_ids(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip().lstrip("-").isdigit()]


@dataclass
class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    DB_HOST: str = os.getenv("DB_HOST", "192.168.50.101")
    DB_PORT: int = int(os.getenv("DB_PORT", "3306"))
    DB_USER: str = os.getenv("DB_USER", "resource")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "resource")
    DB_NAME: str = os.getenv("DB_NAME", "resource")
    ALLOWED_USER_IDS: list[int] = field(
        default_factory=lambda: _parse_ids(os.getenv("ALLOWED_USER_IDS", ""))
    )


settings = Settings()
