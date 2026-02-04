"""
每日流水线：按顺序执行 digest → podcast → tts --sync → japanese_points，
并将每步的开始/成功/失败写入当日日志 logs/{report_date}.log。
供 cron 等在每天固定时间（如北京时间 1:00）调用。
需在项目根目录执行：python run_daily.py 或 python -m run_daily
可选 --themes "主题1,主题2,主题3" 传给 digest 使用自定义主题。
可选 --enable 控制执行哪些步骤；可选 --log-file 指定日志文件路径。
"""
import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config import DIGEST_TIMEZONE

ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"
MAX_STDERR_LINES = 20
ALL_STEPS = ("digest", "podcast", "tts_sync", "japanese_points")


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
    log_line(log_file, step_name, "start")
    result = subprocess.run(
        [sys.executable, "-m", step_name] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        last_lines = "\n".join(err.splitlines()[-MAX_STDERR_LINES:]) if err else "non-zero exit"
        log_line(log_file, step_name, "fail", last_lines[:500])
        sys.exit(1)
    log_line(log_file, step_name, "success")


def main() -> None:
    parser = argparse.ArgumentParser(description="每日流水线：digest → podcast → tts --sync → japanese_points")
    parser.add_argument("--themes", default=None, help='自定义摘要主题，逗号分隔，如 "动漫,游戏,科技"')
    parser.add_argument("--enable", default=",".join(ALL_STEPS), help="启用的步骤，逗号分隔：digest,podcast,tts_sync,japanese_points")
    parser.add_argument("--log-file", default=None, help="日志文件路径（默认 logs/YYYY-MM-DD.log）")
    parser.add_argument("--tts-sync-chunking", default="atomic", choices=("atomic", "sentence"), help="同步朗读分块策略：atomic（原子）或 sentence（按句）")
    args = parser.parse_args()

    report_date = report_date_yesterday()
    enable = {s.strip() for s in (args.enable or "").split(",") if s.strip()}
    for s in enable:
        if s not in ALL_STEPS:
            raise SystemExit(f"未知步骤: {s}，合法值: {','.join(ALL_STEPS)}")

    if args.log_file:
        log_file = Path(args.log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
    else:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOGS_DIR / f"{report_date}.log"

    digest_args = []
    if args.themes:
        digest_args = ["--themes", args.themes.strip()]
    if "digest" in enable:
        run_step("digest", digest_args, log_file, ROOT)
    else:
        log_line(log_file, "digest", "skipped")

    if "podcast" in enable:
        run_step("podcast", ["--date", report_date], log_file, ROOT)
    else:
        log_line(log_file, "podcast", "skipped")

    if "tts_sync" in enable:
        run_step("tts", ["--date", report_date, "--sync", "--sync-mode", args.tts_sync_chunking], log_file, ROOT)
    else:
        log_line(log_file, "tts_sync", "skipped")

    if "japanese_points" in enable:
        run_step("japanese_points", ["--date", report_date], log_file, ROOT)
    else:
        log_line(log_file, "japanese_points", "skipped")

    log_line(log_file, "pipeline", "completed")


if __name__ == "__main__":
    main()
