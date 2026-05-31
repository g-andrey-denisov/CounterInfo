# CounterInfo — Telegram-бот поиска счётчиков электроэнергии

Бот позволяет:
- сканировать штрих-код счётчика (фото) или вводить серийный номер вручную;
- получать данные из корпоративной БД (MariaDB): название, состояние, показания;
- сохранять записи в личный блокнот с произвольным комментарием;
- работать только для авторизованных пользователей (список Telegram ID).

## Требования

- Python 3.11+
- Доступ к MariaDB (`resource@192.168.50.101/resource`)
- Telegram Bot Token от [@BotFather](https://t.me/BotFather)

## Настройка `.env`

```env
BOT_TOKEN=токен_от_BotFather
DB_HOST=192.168.50.101
DB_PORT=3306
DB_USER=resource
DB_PASSWORD=resource
DB_NAME=resource

# Telegram ID через запятую. Пусто = доступ для всех.
ALLOWED_USER_IDS=123456789,987654321
```

Узнать свой Telegram ID можно у [@userinfobot](https://t.me/userinfobot).

---

## Деплой на Windows

### Разработка (авто-перезапуск при изменении файлов)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
python run.py
```

### Продакшен — Windows Service через NSSM

[NSSM](https://nssm.cc) оборачивает любой процесс в Windows Service с авто-перезапуском.

```powershell
# Скачать nssm.exe и положить в PATH, затем:
nssm install CounterInfo "C:\path\to\CounterInfo\.venv\Scripts\python.exe" "C:\path\to\CounterInfo\bot.py"
nssm set CounterInfo AppDirectory "C:\path\to\CounterInfo"
nssm set CounterInfo AppStdout    "C:\path\to\CounterInfo\logs\service.log"
nssm set CounterInfo AppStderr    "C:\path\to\CounterInfo\logs\service.log"
nssm set CounterInfo Start        SERVICE_AUTO_START
nssm start CounterInfo
```

Управление:
```powershell
nssm stop CounterInfo
nssm restart CounterInfo
nssm status CounterInfo
```

---

## Деплой на Linux (Debian/Ubuntu)

### Подготовка

```bash
sudo apt install python3 python3-venv python3-pip git

cd /opt
sudo git clone <repo-url> CounterInfo
sudo chown -R $USER:$USER /opt/CounterInfo

cd /opt/CounterInfo
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env .env.bak   # сделать резервную копию
nano .env          # вписать BOT_TOKEN и ALLOWED_USER_IDS
```

---

### Вариант 1 — Системный сервис (рекомендуется для серверов)

Запускается при старте системы от имени отдельного пользователя. Управляется через `sudo`.

**Создать пользователя:**
```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin counterinfo
sudo chown -R counterinfo:counterinfo /opt/CounterInfo
```

**Создать файл сервиса** `/etc/systemd/system/counterinfo.service`:
```ini
[Unit]
Description=CounterInfo Telegram Bot
After=network.target

[Service]
Type=simple
User=counterinfo
WorkingDirectory=/opt/CounterInfo
ExecStart=/opt/CounterInfo/.venv/bin/python bot.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Включить и запустить:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable counterinfo
sudo systemctl start counterinfo
```

**Управление:**
```bash
sudo systemctl status counterinfo
sudo systemctl stop counterinfo
sudo systemctl restart counterinfo
sudo journalctl -u counterinfo -f        # живые логи
sudo journalctl -u counterinfo --since today
```

---

### Вариант 2 — Пользовательский сервис (без sudo)

Запускается от имени текущего пользователя. Не требует прав root для управления.
Подходит для VPS, где нет необходимости в изоляции пользователей.

**Разрешить запуск без активной сессии:**
```bash
loginctl enable-linger $USER
```

**Создать файл сервиса** `~/.config/systemd/user/counterinfo.service`:
```bash
mkdir -p ~/.config/systemd/user
nano ~/.config/systemd/user/counterinfo.service
```

```ini
[Unit]
Description=CounterInfo Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/CounterInfo
ExecStart=/opt/CounterInfo/.venv/bin/python bot.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

**Включить и запустить:**
```bash
systemctl --user daemon-reload
systemctl --user enable counterinfo
systemctl --user start counterinfo
```

**Управление:**
```bash
systemctl --user status counterinfo
systemctl --user stop counterinfo
systemctl --user restart counterinfo
journalctl --user -u counterinfo -f
```

> **Системный vs пользовательский:**
> | | Системный | Пользовательский |
> |---|---|---|
> | Файл сервиса | `/etc/systemd/system/` | `~/.config/systemd/user/` |
> | Управление | `sudo systemctl` | `systemctl --user` |
> | Запуск при загрузке | Автоматически | Нужен `loginctl enable-linger` |
> | Изоляция | Отдельный системный пользователь | Текущий пользователь |
> | Когда выбрать | Сервер, несколько сервисов | VPS/личная машина |

---

## Структура проекта

```
CounterInfo/
├── bot.py          — основной файл бота (handlers, FSM, middleware)
├── db.py           — запросы к MariaDB (пул соединений aiomysql)
├── local_db.py     — SQLite: блокнот пользователей + кэш счётчиков
├── config.py       — настройки из .env
├── run.py          — запуск с авто-перезапуском (только для разработки)
├── requirements.txt
├── .env            — секреты (не коммитить!)
├── data/
│   └── local.db    — SQLite-база (создаётся автоматически)
└── logs/
    └── bot.log     — лог-файлы с ротацией (создаётся автоматически)
```

## Команды бота

| Команда / слово | Действие |
|---|---|
| `/start`, `/help` | Приветствие и справка |
| `/notebook`, `/note`, `Блокнот` | Показать содержимое блокнота |
| `/clear`, `Очисти` | Очистить блокнот (с подтверждением) |
| Фото со штрих-кодом | Поиск по считанному серийному номеру |
| Число в формате `N.М` | Поиск по коду в названии счётчика (например `26.1` → ищет `(…26.01…)`) |
| Любой другой текст | Поиск по введённому серийному номеру |

## Поиск по коду адреса (формат N.М)

Если ввести текст вида `26.1`, `028.37` или `1,2` (допускаются разделители `.`, `,`, `ю`), бот ищет счётчик по полю `Name`, в котором внутри скобок должен находиться соответствующий код.

Правила нормализации: однозначное число дополняется до двух цифр (`1` → `01`), двух- и трёхзначные используются как есть. Запросы `26.1` и `26.01` эквивалентны, `028.112` и `28.112` — тоже.

## Как работает штрих-код

Штрих-код может содержать серийный номер как подстроку (например, `0385210653426000` содержит серийный `52106534`). Поиск по фото использует `INSTR`, а ручной ввод — точное совпадение.

## Кэш счётчиков

При первом обращении за сутки бот синхронизирует список всех счётчиков из MariaDB в локальный SQLite-файл (`data/local.db`). Список поддерживается актуальным: счётчики, удалённые из основной БД, удаляются и из кэша. Данные о последнем сканировании (дата, показания, состояние) сохраняются пока счётчик существует в БД.

## Зависимости

| Пакет | Назначение |
|---|---|
| aiogram 3.x | Telegram Bot API |
| aiomysql | Async-подключение к MariaDB |
| aiosqlite | Async SQLite (блокнот, кэш) |
| zxing-cpp | Декодирование штрих-кодов (без внешних DLL) |
| Pillow | Работа с изображениями |
| python-dotenv | Загрузка `.env` |
| watchfiles | Авто-перезапуск при изменении файлов (dev) |
