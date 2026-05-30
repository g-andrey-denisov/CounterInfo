import sys
import subprocess
from pathlib import Path
from watchfiles import watch

WATCH_DIR = Path(__file__).parent
WATCH_EXTENSIONS = {".py", ".env"}
STOP_TIMEOUT = 5  # секунд ждём завершения процесса


def start() -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-u", "bot.py"],
        cwd=str(WATCH_DIR),
    )
    print(f"Bot started (PID {proc.pid})", flush=True)
    return proc


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.kill()
    try:
        proc.wait(timeout=STOP_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"Warning: process {proc.pid} did not exit in {STOP_TIMEOUT}s", flush=True)


def main() -> None:
    proc = start()
    try:
        for changes in watch(str(WATCH_DIR)):
            relevant = [p for _, p in changes if Path(p).suffix in WATCH_EXTENSIONS]
            if not relevant:
                continue
            print(f"Changed: {relevant}", flush=True)
            stop(proc)
            proc = start()
    except KeyboardInterrupt:
        stop(proc)
        print("Bot stopped.")


if __name__ == "__main__":
    main()
