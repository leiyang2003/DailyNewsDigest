"""
每日新闻摘要：使用 OpenAI Responses API + web_search 搜集并总结
金融、AI、中美关系、日本政治 四大主题的昨日新闻，输出总结与链接。
"""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
import threading
import time
from zoneinfo import ZoneInfo

import requests

from config import OUTPUT_DIR, OPENAI_API_KEY, DIGEST_TIMEOUT, DIGEST_TIMEZONE, PROXIES


def _print_progress(msg: str, verbose: bool = True) -> None:
    """Print a process message to terminal (with flush so it shows immediately)."""
    if verbose:
        print(msg, flush=True)


def _start_elapsed_printer(verbose: bool) -> threading.Event:
    """Start a background thread that prints elapsed time every 30s. Returns stop event."""
    stop = threading.Event()

    def run():
        start = time.monotonic()
        while not stop.wait(30):
            elapsed = int(time.monotonic() - start)
            _print_progress(f"  … 已等待 {elapsed} 秒，仍在请求中…", verbose)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return stop

# OpenAI Responses API
OPENAI_BASE = "https://api.openai.com/v1"
OPENAI_MODEL = "gpt-4.1"  # 支持 web_search 的模型，可改为 gpt-4o、gpt-5 等
# 超时与重试
MAX_RETRIES = 2  # 超时后最多再试 2 次（共 3 次请求）
RETRY_BACKOFF = 30  # 重试前等待秒数


def get_yesterday_label() -> tuple[str, str, str]:
    """按配置时区得到「昨日」日期与时区标签。返回 (日期字符串如 2026年02月02日, 时区说明, 报告用日期 YYYY-MM-DD)。"""
    tz = ZoneInfo(DIGEST_TIMEZONE)
    now = datetime.now(tz)
    yesterday = now - timedelta(days=1)
    date_str = yesterday.strftime("%Y年%m月%d日")
    report_date = yesterday.strftime("%Y-%m-%d")
    tz_labels = {
        "Asia/Shanghai": "北京时间",
        "Asia/Hong_Kong": "香港时间",
        "Asia/Tokyo": "日本时间",
        "America/New_York": "美东时间",
        "America/Los_Angeles": "美西时间",
        "UTC": "UTC",
    }
    tz_label = tz_labels.get(DIGEST_TIMEZONE, DIGEST_TIMEZONE)
    return date_str, tz_label, report_date


def build_prompt(yesterday: str, tz_label: str) -> str:
    return f"""请搜集并总结 **{tz_label} {yesterday}**（昨日）的新闻，涵盖以下四个主题：

1. **金融**：金融市场、银行、证券、保险、货币政策、监管、并购、财报等
2. **AI**：人工智能、大模型、机器学习、AI 公司、监管与政策、应用与产品等
3. **中美关系**：中美贸易、外交、科技竞争、政策与双边动态
4. **日本政治**：日本国内政治、选举、内阁、外交与重大政策

说明：「昨日」即 **{tz_label} {yesterday}**。新闻来源可能使用其他时区（如美东、UTC），请以该日期为基准筛选（即该日 0 点至 24 点在 {tz_label} 下的报道或与之对应的其他时区报道均可）。

要求：
- 通过搜索找到符合上述「昨日」日期的可靠新闻源（主流媒体、财经媒体、通讯社）报道
- 每个主题至少 2–4 条新闻，每条包含：**新闻标题**、**原文链接**、**2–4 句中文总结**（概括要点）
- 总结需基于原文内容，不要编造；若某主题昨日新闻较少，可如实说明并少列几条
- 输出格式为 Markdown，按上述四个大标题分节，每条新闻用「标题 + 链接 + 总结」的形式，并在文中用 [[N]](url) 形式标注引用来源
"""


def _get_text_from_output(output: list) -> str:
    """从 Responses API output 中提取助理回复正文。"""
    text_parts = []
    for item in output or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "output_text" and content.get("text"):
                text_parts.append(content["text"])
    return "\n".join(text_parts)


def _get_citations_from_output(output: list) -> list[str]:
    """从 response 中收集引用链接（OpenAI 为 url_citation，兼容 url 类型）。"""
    urls = []
    for item in output or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") != "output_text" or not content.get("annotations"):
                continue
            for ann in content["annotations"]:
                # OpenAI: type "url_citation" 含 url、title
                if ann.get("type") == "url_citation" and ann.get("url"):
                    urls.append(ann["url"])
                elif ann.get("type") == "url" and ann.get("url"):
                    urls.append(ann["url"])
    return urls


def _extract_urls_from_content(content: str) -> list[str]:
    """从正文 Markdown 中提取所有 URL（](url) 或 裸 https?://），便于单独存盘。"""
    urls = []
    # Markdown 链接: [text](url) 或 [[n]](url)
    for m in re.finditer(r"\]\s*\(\s*(https?://[^\s\)]+)\s*\)", content):
        urls.append(m.group(1).rstrip(".,;:)"))
    # 裸 URL
    for m in re.finditer(r"https?://[^\s\)\]\<]+", content):
        u = m.group(0).rstrip(".,;:)")
        if u not in urls:
            urls.append(u)
    return urls


def _merge_and_dedupe_urls(citations: list[str], from_content: list[str]) -> list[str]:
    """合并 API 引用与正文提取的 URL，去重并保持顺序。"""
    seen = set()
    out = []
    for u in citations + from_content:
        u = (u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


# 报告中的分类标题（与 prompt 中四个主题一致），用于解析时匹配
DIGEST_CATEGORIES = ("金融", "AI", "中美关系", "日本政治")


def _parse_items_from_content(content: str) -> list[dict]:
    """从报告正文解析出每条新闻的 title、url、summary、category，便于写入 JSON。"""
    items = []
    # 按 ## 分节，保留节标题作为分类
    sections = re.split(r"\n##\s+", content)
    for i, section in enumerate(sections):
        section = section.strip()
        if not section:
            continue
        # 第一节为开头说明（无 ##），无分类；其余第一节行为分类名
        lines = section.split("\n", 1)
        category = ""
        if i > 0:
            category = lines[0].strip().strip("#").strip()
            # 兼容旧格式「金融 AI」等，归一化为已知分类
            for cat in DIGEST_CATEGORIES:
                if cat in category or category in cat:
                    category = cat
                    break
        body = lines[1] if len(lines) > 1 else section
        # 在 body 中找所有 bullet 项（- 标题：** 或 - **）
        block_pat = re.compile(
            r"-\s*(?:标题：\s*)?\*\*([^*]+)\*\*[\s\S]*?"
            r"\]\s*\(\s*(https?://[^\s\)]+)\s*\)[\s\S]*?"
            r"(?:总结|中文总结)[：:]\s*([\s\S]+?)(?=\n\s*-\s*(?:标题：\s*)?\*\*|\n##|\n---|\Z)",
            re.DOTALL,
        )
        for m in block_pat.finditer(body):
            title = m.group(1).strip().strip('"')
            url = m.group(2).rstrip(".,;:)")
            summary = re.sub(r"\s+", " ", m.group(3).strip()).strip()[:2000]
            items.append({"title": title or "", "url": url, "summary": summary, "category": category or ""})
        # 兼容：无「总结：」的 bullet（如仅 **标题** ... (url)）
        if not block_pat.search(body):
            for m in re.finditer(
                r"-\s*\*\*[\"']?([^*]+)[\"']?\*\*[^\n]*\n\s*链接[^\n]*\]\s*\(\s*(https?://[^\s\)]+)\s*\)[^\n]*\n\s*(?:总结|中文总结)[：:]\s*([\s\S]+?)(?=\n\s*-\s*\*\*|\n##|\n---|\Z)",
                body,
                re.DOTALL,
            ):
                title = m.group(1).strip()
                url = m.group(2).rstrip(".,;:)")
                summary = re.sub(r"\s+", " ", m.group(3).strip()).strip()[:2000]
                items.append({"title": title, "url": url, "summary": summary, "category": category or ""})
    return items


def run_digest_http(verbose: bool = True) -> tuple[str, list[str]]:
    """
    通过 HTTP REST 调用 OpenAI Responses API（带 web_search），
    返回 (报告正文 Markdown, 引用链接列表)。
    """
    if not OPENAI_API_KEY:
        raise ValueError("未设置 OPENAI_API_KEY，请在 .env 中配置或导出环境变量")

    yesterday_str, tz_label, report_date = get_yesterday_label()
    instructions = "你是一名专业新闻编辑。你擅长用 web 搜索找到权威来源，阅读原文后给出准确、简洁的中文总结，并始终附上可点击的原文链接。"
    user_content = build_prompt(yesterday_str, tz_label)

    # OpenAI Responses API：instructions 为系统提示，input 为用户输入
    payload = {
        "model": OPENAI_MODEL,
        "instructions": instructions,
        "input": user_content,
        "tools": [{"type": "web_search"}],
        "max_tool_calls": 20,
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    if verbose:
        _print_progress("正在通过 OpenAI Responses API（web_search）搜索并总结昨日新闻，请稍候…")
        if PROXIES:
            _print_progress("已使用环境变量中的代理。")
        _print_progress(f"本次请求超时设为 {DIGEST_TIMEOUT} 秒（约 {DIGEST_TIMEOUT // 60} 分钟），超时将自动重试最多 {MAX_RETRIES} 次。")
        _print_progress("")

    url = f"{OPENAI_BASE}/responses"
    last_error = None
    resp = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            if verbose:
                _print_progress(f"[{attempt + 1}/{MAX_RETRIES + 1}] 正在发送请求到 OpenAI…")
            elapsed_stop = _start_elapsed_printer(verbose)
            try:
                resp = requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=DIGEST_TIMEOUT,
                    proxies=PROXIES,
                )
            finally:
                elapsed_stop.set()
            resp.raise_for_status()
            if verbose:
                _print_progress("请求已返回，正在解析响应…")
            break
        except requests.exceptions.Timeout as e:
            last_error = e
            if attempt < MAX_RETRIES and verbose:
                _print_progress(f"请求超时，{RETRY_BACKOFF} 秒后重试（第 {attempt + 2}/{MAX_RETRIES + 1} 次）…")
                time.sleep(RETRY_BACKOFF)
            continue
        except requests.exceptions.RequestException as e:
            raise SystemExit(f"请求失败: {e}\n请确认本机可访问 https://api.openai.com ；可设置 HTTP_PROXY/HTTPS_PROXY 使用代理。")
    else:
        raise SystemExit(
            f"请求在 {MAX_RETRIES + 1} 次尝试后仍超时（每次 {DIGEST_TIMEOUT} 秒）。\n"
            "建议：1) 在 .env 中设置 DIGEST_TIMEOUT=7200（2 小时）；2) 设置 HTTP_PROXY/HTTPS_PROXY 使用代理；3) 检查网络能否访问 api.openai.com 。"
        )

    data = resp.json()

    # 若返回异步任务，则轮询直到完成
    status = data.get("status")
    if status == "in_progress" or status == "queued":
        response_id = data.get("id")
        if not response_id:
            raise SystemExit("API 返回进行中但无 response id。")
        if verbose:
            _print_progress("任务进行中，开始轮询结果…")
        poll_start = time.monotonic()
        while status in ("in_progress", "queued"):
            time.sleep(5)
            elapsed = int(time.monotonic() - poll_start)
            if verbose:
                _print_progress(f"  轮询中… 已等待 {elapsed} 秒")
            get_resp = requests.get(
                f"{OPENAI_BASE}/responses/{response_id}",
                headers=headers,
                timeout=120,
                proxies=PROXIES,
            )
            get_resp.raise_for_status()
            data = get_resp.json()
            status = data.get("status")
            if status == "failed":
                raise SystemExit(f"任务失败: {data.get('error', data)}")
        if verbose:
            _print_progress("轮询完成，任务已结束。")

    if verbose:
        _print_progress("正在提取正文与引用链接…")
    content = _get_text_from_output(data.get("output"))
    citations = _get_citations_from_output(data.get("output"))

    # 部分实现可能把引用放在顶层
    if not citations and data.get("citations"):
        citations = list(data.get("citations", []))
    # 从正文中再提取 URL，与 API 引用合并去重，便于单独存盘与后期访问
    from_content = _extract_urls_from_content(content)
    all_urls = _merge_and_dedupe_urls(citations, from_content)

    if verbose:
        if all_urls:
            _print_progress(f"共引用 {len(all_urls)} 个来源。")
        _print_progress("处理完成。")

    return content or "(无正文)", all_urls, report_date


def run_digest(verbose: bool = True) -> tuple[str, list[str], str]:
    """对外统一入口：当前仅使用 HTTP 实现。返回 (正文, 引用列表, 报告日期 YYYY-MM-DD)。"""
    return run_digest_http(verbose=verbose)


def save_report(content: str, citations: list[str], report_date: str | None = None) -> Path:
    """将报告与引用链接写入 Markdown 文件，并单独保存 URL 列表便于后期访问。report_date 为新闻日期 YYYY-MM-DD（默认昨日）。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if report_date is None:
        tz = ZoneInfo(DIGEST_TIMEZONE)
        report_date = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")
    path_md = OUTPUT_DIR / f"daily_digest_{report_date}.md"
    path_urls_txt = OUTPUT_DIR / f"daily_digest_{report_date}_urls.txt"
    path_json = OUTPUT_DIR / f"daily_digest_{report_date}.json"

    with open(path_md, "w", encoding="utf-8") as f:
        f.write(content)
        f.write("\n\n---\n\n## 引用链接汇总\n\n")
        for i, url in enumerate(citations, 1):
            f.write(f"{i}. {url}\n")

    with open(path_urls_txt, "w", encoding="utf-8") as f:
        f.write(f"# 新闻日期: {report_date}\n")
        for url in citations:
            f.write(url + "\n")

    items = _parse_items_from_content(content)
    payload = {
        "report_date": report_date,
        "items": items,
        "urls": citations,
    }
    with open(path_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return path_md


def main():
    import argparse

    parser = argparse.ArgumentParser(description="每日新闻摘要（昨日）：金融、AI、中美关系、日本政治")
    parser.add_argument("--no-save", action="store_true", help="只打印到终端，不保存文件")
    parser.add_argument("--quiet", action="store_true", help="减少终端输出")
    args = parser.parse_args()

    content, citations, report_date = run_digest(verbose=not args.quiet)
    print("\n" + "=" * 60 + "\n")
    print(content)

    if not args.no_save:
        path = save_report(content, citations, report_date)
        if not args.quiet:
            print(f"\n报告已保存: {path}")
            print(f"链接列表已保存: {path.parent / f'daily_digest_{report_date}_urls.txt'}")
            print(f"URL+Summary JSON 已保存: {path.parent / f'daily_digest_{report_date}.json'}")
    elif not args.quiet and citations:
        print("\n引用链接:")
        for i, url in enumerate(citations, 1):
            print(f"  {i}. {url}")


if __name__ == "__main__":
    main()
