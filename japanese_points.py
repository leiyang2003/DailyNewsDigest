"""
日语要点抽取：从播客稿中抽取 N1-N2 单词与文法要点，按要求写入 JSON 文件。

输出文件：reports/japanese_points_YYYY-MM-DD.json

JSON 结构要求：
- report_date: str，报告日期 YYYY-MM-DD
- words: list，单词列表，每项含：word, hiragana, meaning_zh, example, example_translation, level（"N1"|"N2"）
- grammar: list，文法列表，每项含：grammar, meaning_zh, example1, example1_translation, example2, example2_translation, level（"N1"|"N2"）
"""
import json
import re
from pathlib import Path

import requests

from config import OPENAI_API_KEY, OUTPUT_DIR, PROXIES

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o"
TIMEOUT = 180


def _chat(system: str, user: str, timeout: int = TIMEOUT) -> str:
    """调用 OpenAI Chat Completions，返回助手回复正文。"""
    if not OPENAI_API_KEY:
        raise ValueError("未设置 OPENAI_API_KEY，请在 .env 中配置")
    resp = requests.post(
        OPENAI_CHAT_URL,
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        },
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        timeout=timeout,
        proxies=PROXIES,
    )
    resp.raise_for_status()
    data = resp.json()
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return text.strip()


def load_script(report_date: str) -> str:
    """加载指定日期的播客稿全文。"""
    path = OUTPUT_DIR / f"podcast_script_{report_date}.md"
    if not path.exists():
        raise FileNotFoundError(f"未找到播客脚本: {path}，请先运行 podcast.py 生成该日期的脚本。")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def extract_points(script: str) -> dict:
    """
    从播客稿中抽取 20 个 N1-N2 单词、20 个 N1-N2 文法。
    返回 { "words": [...], "grammar": [...] }，结构与 japanese_points_YYYY-MM-DD.json 一致。
    """
    system = """你是一位日语 N1/N2 教学专家。请根据给定的日语播客稿，严格按 JSON 格式输出两类内容：
1. words：恰好 20 个较难的 N1-N2 级别单词，每个对象必须包含：word（单词，汉字/假名）、hiragana（平假名）、meaning_zh（中文解释）、example（例句，日语）、example_translation（例句中文翻译）、level（"N1" 或 "N2"，根据难度二选一，每条必须填写）。
2. grammar：恰好 20 个较难的 N1-N2 级别文法，每个对象必须包含：grammar（文法表述）、meaning_zh（中文解释）、example1（例句1，日语）、example1_translation（例句1中文翻译）、example2（例句2，日语）、example2_translation（例句2中文翻译）、level（"N1" 或 "N2"，根据难度二选一，每条必须填写）。
只输出一个 JSON 对象，不要 markdown 代码块包裹。键名必须为 words 和 grammar，且均为数组。每条单词和文法的 level 字段必须为 "N1" 或 "N2" 之一。"""
    user = f"""请从以下日语播客稿中抽取恰好 20 个较难 N1-N2 单词和 20 个较难 N1-N2 文法，按上述结构输出 JSON。\n\n---\n\n{script}"""
    raw = _chat(system, user)
    # 去掉可能的 markdown 代码块
    if "```" in raw:
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    data = json.loads(raw)
    words = data.get("words") or []
    grammar = data.get("grammar") or []
    # 截断或补足到 20
    words = (words + [None] * 20)[:20]
    grammar = (grammar + [None] * 20)[:20]
    words = [w for w in words if w and isinstance(w, dict)]
    grammar = [g for g in grammar if g and isinstance(g, dict)]
    return {"words": words, "grammar": grammar}


# 单词项必须字段（含 level）
_WORD_KEYS = ("word", "hiragana", "meaning_zh", "example", "example_translation")
_GRAMMAR_KEYS = ("grammar", "meaning_zh", "example1", "example1_translation", "example2", "example2_translation")


def _normalize_word(w: dict) -> dict:
    """按 JSON 要求只保留规定字段；level 必填，缺省为 N2。"""
    out = {k: (w.get(k) or "") for k in _WORD_KEYS}
    out["level"] = w.get("level") if w.get("level") in ("N1", "N2") else "N2"
    return out


def _normalize_grammar(g: dict) -> dict:
    """按 JSON 要求只保留规定字段；level 必填，缺省为 N2。"""
    out = {k: (g.get(k) or "") for k in _GRAMMAR_KEYS}
    out["level"] = g.get("level") if g.get("level") in ("N1", "N2") else "N2"
    return out


def save_points_json(data: dict, report_date: str, out_dir: Path) -> Path:
    """
    将抽取的单词与文法按要求的 JSON 结构写入文件。
    data 需含 words、grammar 列表；会补全 report_date 并规范化每项字段。
    """
    words = [_normalize_word(w) for w in (data.get("words") or []) if isinstance(w, dict)]
    grammar = [_normalize_grammar(g) for g in (data.get("grammar") or []) if isinstance(g, dict)]
    payload = {
        "report_date": report_date,
        "words": words,
        "grammar": grammar,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"japanese_points_{report_date}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out_path


def main() -> None:
    import argparse
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from config import DIGEST_TIMEZONE

    parser = argparse.ArgumentParser(description="从播客稿抽取 N1-N2 单词与文法（日语要点）")
    parser.add_argument("--date", default=None, help="脚本日期 YYYY-MM-DD，默认昨日")
    args = parser.parse_args()

    if args.date:
        report_date = args.date
    else:
        tz = ZoneInfo(DIGEST_TIMEZONE)
        report_date = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"[1/4] 日期: {report_date}")
    print("[2/4] 正在加载播客稿...")
    script = load_script(report_date)
    print(f"      播客稿已加载，共 {len(script)} 字。")
    print("[3/4] 正在调用 API 抽取单词与文法（约 1–2 分钟）...")
    data = extract_points(script)
    n_words = len(data["words"])
    n_grammar = len(data["grammar"])
    print(f"      抽取完成：单词 {n_words} 条，文法 {n_grammar} 条。")
    print("[4/4] 正在写入 JSON...")
    out_path = save_points_json(data, report_date, OUTPUT_DIR)
    print(f"      已保存: {out_path}")
    print(f"完成。单词 {n_words} 条，文法 {n_grammar} 条。")


if __name__ == "__main__":
    main()
