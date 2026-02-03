"""
Podcast 脚本生成：基于当日摘要 JSON，生成「原子新闻」播客稿。
包含：1. 开场（原子新闻与主播美香介绍） 2. 有新闻的各分类 200 字日语总结 3. 本日总结 4. 结束语。
无新闻的分类不出现。成稿后交由大模型润色，语气更接近年轻女性主持人自然口播。
"""
import json
from pathlib import Path

import requests

from config import OPENAI_API_KEY, OUTPUT_DIR, PROXIES

# 与 digest 中分类一致
CATEGORIES = ("金融", "AI", "中美关系", "日本政治")
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o"
TIMEOUT = 120
REFINE_TIMEOUT = 180


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
        },
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        timeout=timeout,
        proxies=PROXIES,
    )
    resp.raise_for_status()
    data = resp.json()
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return text.strip()


def load_digest_json(report_date: str) -> dict:
    """加载指定日期的摘要 JSON。文件名格式为 daily_digest_YYYY_MM_DD.json。"""
    file_prefix = report_date.replace("-", "_")
    path = OUTPUT_DIR / f"daily_digest_{file_prefix}.json"
    if not path.exists():
        raise FileNotFoundError(f"未找到摘要文件: {path}，请先运行 digest.py 生成该日期的摘要。")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def generate_intro(report_date: str) -> str:
    """1. 开场：简单介绍原子新闻、主播美香、播客目的。"""
    return _chat(
        "你是一位日语播客撰稿人，用自然、简洁的日语写稿。",
        f"""「原子新闻」播客的开场白（约 80–120 字日语）需要包含：
1. 节目名「原子新闻」的简短介绍（一句话即可）
2. 主播是美香（美香です）
3. 本播客的目的：用简短时间了解昨日重要新闻（金融、AI、中美关系、日本政治）

今日播报的新闻日期为 {report_date}。只输出开场白正文，不要加标题或说明。""",
    )


def generate_category_summary_ja(category: str, items: list[dict]) -> str:
    """2. 某一分类：根据该分类下的全部新闻信息，写一段约 200 字日语总结。调用方应保证 items 非空。"""
    parts = []
    for i, it in enumerate(items, 1):
        title = it.get("title") or ""
        summary = it.get("summary") or ""
        parts.append(f"[{i}] {title}\n{summary}")
    block = "\n\n".join(parts)
    return _chat(
        "你是一位日语播客撰稿人。根据给出的多条新闻标题与中文摘要，用日语写一段约 200 字（约 200 字符）的汇总，用于播客口播。要求：连贯、口语化、不列点。",
        f"""以下为「{category}」分类下的新闻（标题与摘要）。请用日语写一段约 200 字的汇总，把要点串成一段话，便于主播朗读。只输出日语正文，不要加标题或编号。\n\n{block}""",
    )


def generate_daily_summary(category_summaries: list[tuple[str, str]]) -> str:
    """3. 本日总结：把各分类的总结合在一起，写一段「本日总结」。"""
    if not category_summaries:
        return "（本日のサマリーはありません。）"
    block = "\n\n".join(f"【{cat}】\n{text}" for cat, text in category_summaries)
    return _chat(
        "你是一位日语播客撰稿人。根据各分类的日语总结，写一段简短的「本日总结」（約 150–250 字日语），概括今日播报的要点，便于主播收束各分类。只输出日语正文。",
        f"""以下为各分类的日语总结。请据此写一段「本日总结」，把整体要点概括成一段话。\n\n{block}""",
    )


def generate_outro() -> str:
    """4. 结束语：简短、易记的播客结束语。"""
    return _chat(
        "你是一位日语播客撰稿人。写一句简短的播客结束语，易记、有辨识度，约 20–40 字日语。例如感谢收听、下期再见等，风格可轻松或专业。只输出结束语正文。",
        "「原子新闻」、主播美香的播客结束语（一句，约 20–40 字日语）。",
    )


def build_script(report_date: str, data: dict) -> str:
    """根据摘要 JSON 生成完整播客稿（四部分）。无新闻的分类不出现。"""
    items = data.get("items") or []
    by_cat = {c: [x for x in items if (x.get("category") or "").strip() == c] for c in CATEGORIES}

    intro = generate_intro(report_date)
    category_summaries = []
    parts = [intro, "\n\n"]

    for cat in CATEGORIES:
        cat_items = by_cat.get(cat) or []
        if not cat_items:
            continue
        summary_ja = generate_category_summary_ja(cat, cat_items)
        category_summaries.append((cat, summary_ja))
        parts.append(summary_ja)
        parts.append("\n\n")

    daily = generate_daily_summary(category_summaries)
    parts.append(daily)
    parts.append("\n\n")

    outro = generate_outro()
    parts.append(outro)
    parts.append("\n")

    return "".join(parts)


def refine_script(script: str) -> str:
    """将整篇播客稿交给大模型润色：语气更接近自然播客中年轻女性主持人说话。"""
    return _chat(
        "你是一位年轻的女性日语播客主持人（美香）。请用自然、口语化、像真实主持人在录音室里说话的语气，优化以下播客稿。"
        "要求：保持原意和整体结构（开场、各分类、本日总结、结束语），只润色文字，让句子更顺口、更有亲和力、更像年轻女性主持人的口吻。"
        "不要添加或删除段落，不要添加标题或编号。只输出优化后的完整稿子（纯正文，便于后期生成音频）。",
        script,
        timeout=REFINE_TIMEOUT,
    )


def main():
    import argparse
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from config import DIGEST_TIMEZONE

    parser = argparse.ArgumentParser(description="生成原子新闻 Podcast 稿（基于当日摘要 JSON）")
    parser.add_argument("--date", default=None, help="新闻日期 YYYY-MM-DD，默认昨日")
    parser.add_argument("--no-save", action="store_true", help="只打印到终端，不保存文件")
    args = parser.parse_args()

    if args.date:
        report_date = args.date
    else:
        tz = ZoneInfo(DIGEST_TIMEZONE)
        report_date = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")

    data = load_digest_json(report_date)
    script = build_script(report_date, data)
    script = refine_script(script)

    print(script)

    if not args.no_save:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTPUT_DIR / f"podcast_script_{report_date}.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(script)
        print(f"\n脚本已保存: {path}")


if __name__ == "__main__":
    main()
