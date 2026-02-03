"""
统一网页应用：Flask 静态 + /reports/ + 温故知新 API。
运行：python app.py，在浏览器打开 http://127.0.0.1:5001/ 或 http://localhost:5001/ 进入首页。
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, send_from_directory

from config import DIGEST_TIMEZONE

# 项目根目录、reports 与 static
ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
STATIC = ROOT / "static"

REVIEW_WORDS = REPORTS / "review_words.json"
REVIEW_GRAMMAR = REPORTS / "review_grammar.json"
REVIEW_PROGRESS = REPORTS / "review_progress.json"
USER_SETTINGS = REPORTS / "user_settings.json"

app = Flask(__name__, static_folder=str(ROOT), static_url_path="")


@app.route("/")
def index():
    return send_from_directory(ROOT, "index.html")


@app.route("/digest")
@app.route("/digest.html")
def digest():
    return send_from_directory(ROOT, "digest.html")


@app.route("/podcast")
@app.route("/podcast.html")
def podcast():
    return send_from_directory(ROOT, "podcast.html")


@app.route("/sync_reader")
@app.route("/sync_reader.html")
def sync_reader():
    return send_from_directory(ROOT, "sync_reader.html")


@app.route("/japanese_points")
@app.route("/japanese_points.html")
def japanese_points():
    return send_from_directory(ROOT, "japanese_points.html")


@app.route("/settings")
@app.route("/settings.html")
def settings():
    return send_from_directory(ROOT, "settings.html")


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.route("/static/<path:path>")
def serve_static(path: str):
    """提供 static 目录下的 CSS 等静态文件（如 base.css）。"""
    return send_from_directory(STATIC, path)


@app.route("/reports/<path:path>")
def serve_reports(path: str):
    """提供 reports 目录下的 JSON 等静态文件。"""
    return send_from_directory(REPORTS, path)


def _yesterday_yyyymmdd():
    """按 DIGEST_TIMEZONE 计算「昨天」的日期，返回 YYYY-MM-DD。"""
    tz = ZoneInfo(DIGEST_TIMEZONE)
    now = datetime.now(tz)
    yesterday = (now - timedelta(days=1)).date()
    return yesterday.strftime("%Y-%m-%d")


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    """GET：返回用户设定。POST：body { update_cycle: "minute"|"ten_minutes"|"day" } 保存。"""
    default = {"update_cycle": "day"}
    if request.method == "GET":
        data = _read_json(USER_SETTINGS, default)
        return jsonify(data)
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "body must be JSON object"}), 400
    cycle = data.get("update_cycle", "day")
    if cycle not in ("minute", "ten_minutes", "day"):
        cycle = "day"
    out = {"update_cycle": cycle}
    _write_json(USER_SETTINGS, out)
    return jsonify(out)


@app.route("/api/japanese_points/latest")
def api_japanese_points_latest():
    """返回「昨天」的 japanese_points_YYYY-MM-DD.json 的完整内容（单词与文法）。"""
    print("用户点击「最新を読み込む」")
    date_str = _yesterday_yyyymmdd()
    path = REPORTS / f"japanese_points_{date_str}.json"
    if not path.exists():
        print(f"[japanese_points] 最新を読み込む: {date_str} のファイルがありません。")
        return jsonify({"error": f"no japanese_points report for {date_str} (yesterday)"}), 404
    data = _read_json(path, None)
    if data is None:
        print(f"[japanese_points] 最新を読み込む: {path.name} の読み込みに失敗しました。")
        return jsonify({"error": "failed to read report"}), 500
    if "report_date" not in data:
        data["report_date"] = date_str
    words = data.get("words") or []
    grammar = data.get("grammar") or []
    print(f"[japanese_points] 最新を読み込む: japanese_points_{date_str}.json を表示中。語彙 {len(words)} 件、文法 {len(grammar)} 件。")
    return jsonify(data)


@app.route("/api/review/words", methods=["GET", "POST"])
def api_review_words():
    """GET：返回温故知新-单词列表。POST：body 为完整数组，覆盖写入。"""
    if request.method == "GET":
        data = _read_json(REVIEW_WORDS, [])
        return jsonify(data)
    data = request.get_json(force=True, silent=True) or []
    if not isinstance(data, list):
        return jsonify({"error": "body must be JSON array"}), 400
    _write_json(REVIEW_WORDS, data)
    return jsonify(data)


@app.route("/api/review/grammar", methods=["GET", "POST"])
def api_review_grammar():
    """GET：返回温故知新-文法列表。POST：body 为完整数组，覆盖写入。"""
    if request.method == "GET":
        data = _read_json(REVIEW_GRAMMAR, [])
        return jsonify(data)
    data = request.get_json(force=True, silent=True) or []
    if not isinstance(data, list):
        return jsonify({"error": "body must be JSON array"}), 400
    _write_json(REVIEW_GRAMMAR, data)
    return jsonify(data)


@app.route("/api/review/progress", methods=["GET", "POST"])
def api_review_progress():
    """GET：返回进度 { mode, index }。POST：body { mode, index } 保存。"""
    default = {"mode": "words", "index": 0}
    if request.method == "GET":
        data = _read_json(REVIEW_PROGRESS, default)
        return jsonify(data)
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "body must be JSON object"}), 400
    out = {"mode": data.get("mode", default["mode"]), "index": int(data.get("index", 0))}
    _write_json(REVIEW_PROGRESS, out)
    return jsonify(out)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
