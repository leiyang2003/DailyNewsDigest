"""
每日流水线：按顺序执行 digest → podcast → tts --sync → japanese_points，
并将每步的开始/成功/失败写入当日日志 logs/{report_date}.log。
供 cron 等在每天固定时间（如北京时间 1:00）调用。
需在项目根目录执行：python run_daily.py 或 python -m run_daily
"""
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config import DIGEST_TIMEZONE

DEBUG_LOG_PATH = Path(__file__).resolve().parent.parent / ".cursor" / "debug.log"

def _debug_log(location: str, message: str, data: dict, hypothesis_id: str) -> None:
    import json
    line = json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": hypothesis_id, "location": location, "message": message, "data": data, "timestamp": datetime.now().timestamp() * 1000}) + "\n"
    try:
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"
MAX_STDERR_LINES = 20


def report_date_yesterday() -> str:
    """按配置时区计算「昨日」日期 YYYY-MM-DD。"""
    tz = ZoneInfo(DIGEST_TIMEZONE)
    yesterday = (datetime.now(tz) - timedelta(days=1)).date()
    return yesterday.strftime("%Y-%m-%d")


def log_line(log_file: Path, step: str, status: str, message: str = "") -> None:
    """写一行日志：{iso_timestamp} STEP status [message]"""
    tz = ZoneInfo(DIGEST_TIMEZONE)
    ts = datetime.now(tz).isoformat()
    line = f"{ts} {step} {status}"
    if message:
        # 单行化，避免多行破坏一行一条的格式
        line += " " + message.replace("\n", " ").strip()
    line += "\n"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line)


def run_step(
    step_name: str,
    args: list[str],
    log_file: Path,
    cwd: Path,
) -> None:
    """执行一步，写 start，成功写 success，失败写 fail 并退出进程。"""
    # #region agent log
    _debug_log("run_daily.py:run_step", "step_start", {"step": step_name, "args": args}, "A")
    # #endregion
    log_line(log_file, step_name, "start")
    result = subprocess.run(
        [sys.executable, "-m", step_name] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # #region agent log
    _debug_log("run_daily.py:run_step", "step_exit", {"step": step_name, "returncode": result.returncode}, "A" if result.returncode != 0 else "B")
    # #endregion
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        last_lines = "\n".join(err.splitlines()[-MAX_STDERR_LINES:]) if err else "non-zero exit"
        # #region agent log
        _debug_log("run_daily.py:run_step", "pipeline_exit_fail", {"step": step_name, "err_preview": last_lines[:200]}, "A")
        # #endregion
        log_line(log_file, step_name, "fail", last_lines[:500])
        sys.exit(1)
    log_line(log_file, step_name, "success")


def main() -> None:
    report_date = report_date_yesterday()
    # #region agent log
    _debug_log("run_daily.py:main", "main_start", {"report_date": report_date}, "E")
    # #endregion
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"{report_date}.log"

    run_step("digest", [], log_file, ROOT)
    run_step("podcast", ["--date", report_date], log_file, ROOT)
    run_step("tts", ["--date", report_date, "--sync"], log_file, ROOT)
    run_step("japanese_points", ["--date", report_date], log_file, ROOT)

    log_line(log_file, "pipeline", "completed")


if __name__ == "__main__":
    main()
