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


def get_yesterday_label(report_date_override: str | None = None) -> tuple[str, str, str]:
    """按配置时区得到「昨日」日期与时区标签；若传入 report_date_override (YYYY-MM-DD) 则使用该日期。返回 (日期字符串如 2026年02月02日, 时区说明, 报告用日期 YYYY-MM-DD)。"""
    tz_labels = {
        "Asia/Shanghai": "北京时间",
        "Asia/Hong_Kong": "香港时间",
        "Asia/Tokyo": "日本时间",
        "America/New_York": "美东时间",
        "America/Los_Angeles": "美西时间",
        "UTC": "UTC",
    }
    tz_label = tz_labels.get(DIGEST_TIMEZONE, DIGEST_TIMEZONE)
    if report_date_override:
        try:
            dt = datetime.strptime(report_date_override, "%Y-%m-%d")
            date_str = dt.strftime("%Y年%m月%d日")
            return date_str, tz_label, report_date_override
        except ValueError:
            raise ValueError(f"无效的 --date 格式，应为 YYYY-MM-DD: {report_date_override}")
    tz = ZoneInfo(DIGEST_TIMEZONE)
    now = datetime.now(tz)
    yesterday = now - timedelta(days=1)
    date_str = yesterday.strftime("%Y年%m月%d日")
    report_date = yesterday.strftime("%Y-%m-%d")
    return date_str, tz_label, report_date


def _report_date_to_file_prefix(report_date: str) -> str:
    """将报告日期 YYYY-MM-DD 转为文件名用 YYYY_MM_DD。"""
    return report_date.replace("-", "_")


def build_prompt(yesterday: str, tz_label: str, themes: list[str] | None = None) -> str:
    if themes:
        n = len(themes)
        theme_list = "\n".join(f"{i}. **{t}**：このテーマに関する昨日のニュース" for i, t in enumerate(themes, 1))
        categories_ja = "、".join(themes)
        example_cat = themes[0]
    else:
        n = 4
        theme_list = """1. **金融**：金融市場、銀行、証券、保険、金融政策、規制、M&A、決算など
2. **AI**：人工知能、大規模言語モデル、機械学習、AI企業、規制・政策、応用と製品など
3. **中美关系**（米中関係）：米中貿易、外交、技術競争、政策と二国間の動き
4. **日本政治**：日本の国内政治、選挙、内閣、外交、重要政策"""
        categories_ja = "金融、AI、中美关系、日本政治"
        example_cat = "金融"
    return f"""**{tz_label} {yesterday}**（昨日）のニュースを収集し要約してください。以下の{n}個のテーマをカバーします。

{theme_list}

説明：「昨日」とは **{tz_label} {yesterday}** のことです。ニュースソースは別のタイムゾーン（米東部、UTCなど）の日付で出ている場合がありますが、この日付を基準に選んでください（{tz_label} のその日 0:00〜24:00 の報道、またはそれに相当する他タイムゾーンの報道で可）。

要件：
- 上記「昨日」に該当する信頼できるニュースソース（主流メディア、経済メディア、通信社）の記事を検索で見つけること
- **ニュースソースは米国・日本のメディアを優先**：米国は Reuters、AP、Bloomberg、The New York Times、Wall Street Journal、CNN など；日本は NHK、日経新聞（Nikkei）、朝日新聞、読売新聞、共同社など；他地域の主流メディア・通信社も可
- **items は合計 8 件以上**：上記テーマで合計少なくとも 8 件、各テーマできれば 2 件以上；各件に **ニュース見出し**、**原文URL**、**2〜4文の日本語要約**（要点をまとめる）を含めること
- 要約は原文に基づき、捏造しないこと；あるテーマで昨日のニュースが少ない場合は、他テーマから多めに選んで少なくとも 8 件を満たすこと
- 各ニュースの category は次のいずれかのみ：{categories_ja}
- **回答の最後に**、プログラムで解析できるよう**純粋な JSON オブジェクト**のみを出力してください（他説明は不要）。形式は以下：
  {{"report_date":"YYYY-MM-DD","items":[{{"title":"見出し","url":"https://...","summary":"要約（日本語）","category":"{example_cat}"}}, ...]}}
  report_date は昨日の日付 {yesterday.replace("年","-").replace("月","-").replace("日","")}、items は上記すべてのニュースの配列。各要素は title、url、summary、category を含むこと。
"""


def _extract_json_from_content(content: str) -> dict | None:
    """从正文中提取 JSON 对象（如模型在文末输出的 report_date + items）。若无效则返回 None。"""
    if not content or not content.strip():
        return None
    text = content.strip()
    # 1. 尝试 ```json ... ``` 或 ``` ... ``` 代码块
    for pattern in (r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```"):
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                data = json.loads(m.group(1).strip())
                if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
                    return data
            except (json.JSONDecodeError, TypeError):
                pass
    # 2. 尝试单行 JSON（模型常输出一整行 {"report_date":"...","items":[...]}）
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('{"report_date"') or line.startswith('{"items"'):
            try:
                data = json.loads(line)
                if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
                    return data
            except (json.JSONDecodeError, TypeError):
                pass
    # 3. 尝试最后一个 { ... } 块（模型常在文末输出 JSON）
    start = text.rfind("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(text[start : i + 1])
                        if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
                            return data
                    except (json.JSONDecodeError, TypeError):
                        pass
                    break
    return None


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
    """从正文 Markdown 中提取所有 URL（](url) 或 裸 https?://），便于单独存盘。不在 JSON 内多截（遇 \" 即停）。"""
    urls = []
    # Markdown 链接: [text](url) 或 [[n]](url)
    for m in re.finditer(r"\]\s*\(\s*(https?://[^\s\)]+)\s*\)", content):
        u = m.group(1).rstrip(".,;:)")
        if u and u not in urls:
            urls.append(u)
    # 裸 URL，遇 " 停止以免把 JSON 中 url 后的 \"summary\" 等截进来
    for m in re.finditer(r"https?://[^\s\)\]\<\"]+", content):
        u = m.group(0).rstrip(".,;:)\"")
        if u and u not in urls:
            urls.append(u)
    return urls


def _merge_and_dedupe_urls(citations: list[str], from_content: list[str]) -> list[str]:
    """合并 API 引用与正文提取的 URL，去重并保持顺序。丢弃含 \" 的脏 URL（多为 JSON 碎片）。"""
    seen = set()
    out = []
    for u in citations + from_content:
        u = (u or "").strip()
        if not u or u in seen or '"' in u:
            continue
        seen.add(u)
        out.append(u)
    return out


# 报告中的分类标题（与 prompt 中四个主题一致），用于解析时匹配
DIGEST_CATEGORIES = ("金融", "AI", "中美关系", "日本政治")


def _parse_items_from_content(content: str, allowed_categories: tuple[str, ...] | None = None) -> list[dict]:
    """从报告正文解析出每条新闻的 title、url、summary、category，便于写入 JSON。
    allowed_categories: 合法分类列表，用于归一化节标题；若为 None 则用默认 DIGEST_CATEGORIES。"""
    categories = allowed_categories if allowed_categories is not None else DIGEST_CATEGORIES
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
            for cat in categories:
                if cat in category or category in cat:
                    category = cat
                    break
        body = lines[1] if len(lines) > 1 else section
        n_before = len(items)
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
        if len(items) == n_before and body:
            for m in re.finditer(
                r"-\s*\*\*[\"']?([^*]+)[\"']?\*\*[^\n]*\n\s*链接[^\n]*\]\s*\(\s*(https?://[^\s\)]+)\s*\)[^\n]*\n\s*(?:总结|中文总结)[：:]\s*([\s\S]+?)(?=\n\s*-\s*\*\*|\n##|\n---|\Z)",
                body,
                re.DOTALL,
            ):
                title = m.group(1).strip()
                url = m.group(2).rstrip(".,;:)")
                summary = re.sub(r"\s+", " ", m.group(3).strip()).strip()[:2000]
                items.append({"title": title, "url": url, "summary": summary, "category": category or ""})
        # 兼容：格式为「- **标题**\n  链接：...](url)\n  摘要段落」（无「总结：」标签）
        if len(items) == n_before and (body or section):
            for m in re.finditer(
                r"-\s*\*\*([^*]+)\*\*[^\n]*\n\s*链接[^\n]*\]\s*\(\s*(https?://[^\s\)]+)\s*\)[^\n]*\n\s*([\s\S]+?)(?=\n\s*-\s*\*\*|\n##|\n---|\Z)",
                body or section,
                re.DOTALL,
            ):
                title = m.group(1).strip().strip('"')
                url = m.group(2).rstrip(".,;:)")
                summary = re.sub(r"\s+", " ", m.group(3).strip()).strip()[:2000]
                if title and url:
                    items.append({"title": title, "url": url, "summary": summary, "category": category or ""})
    return items


def run_digest_http(
    verbose: bool = True,
    report_date_override: str | None = None,
    themes: list[str] | None = None,
) -> tuple[str, list[str], str]:
    """
    通过 HTTP REST 调用 OpenAI Responses API（带 web_search），
    返回 (报告正文 Markdown, 引用链接列表, 报告日期 YYYY-MM-DD)。
    若传入 report_date_override (YYYY-MM-DD)，则使用该日期作为「昨日」。
    若传入 themes，则用这些主题替代默认四类（金融、AI、中美关系、日本政治）。
    """
    if not OPENAI_API_KEY:
        raise ValueError("未设置 OPENAI_API_KEY，请在 .env 中配置或导出环境变量")

    yesterday_str, tz_label, report_date = get_yesterday_label(report_date_override)
    theme_note = "指定したテーマ" if themes else "四つのテーマ"
    instructions = f"あなたはプロのニュース編集者です。Web検索で権威ある情報源を見つけ、原文を読んだ上で正確・簡潔な要約（日本語）を作成し、常にクリック可能な原文リンクを付けてください。米国・日本の主流メディア（Reuters、AP、Bloomberg、NYT、WSJ、NHK、日経、朝日、読売など）を優先して使用してください。回答の items は{theme_note}合計で8件以上必須です。"
    user_content = build_prompt(yesterday_str, tz_label, themes=themes)

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


def run_digest(
    verbose: bool = True,
    report_date_override: str | None = None,
    themes: list[str] | None = None,
) -> tuple[str, list[str], str]:
    """对外统一入口：当前仅使用 HTTP 实现。返回 (正文, 引用列表, 报告日期 YYYY-MM-DD)。"""
    return run_digest_http(verbose=verbose, report_date_override=report_date_override, themes=themes)


def _md_from_payload(payload: dict) -> str:
    """从 JSON payload 生成可读的 Markdown 报告（供人类阅读或备份）。"""
    report_date = payload.get("report_date") or ""
    items = payload.get("items") or []
    urls = payload.get("urls") or [it.get("url") for it in items if it.get("url")]
    cats = list(dict.fromkeys(it.get("category") or "" for it in items if (it.get("category") or "").strip()))
    intro = f"以下为新闻日期 **{report_date}** 的摘要，涵盖各主题。" if cats else f"以下为新闻日期 **{report_date}** 的摘要，涵盖金融、AI、中美关系、日本政治。"
    lines = [intro, "", "---", ""]
    current_cat = ""
    for it in items:
        cat = (it.get("category") or "其他").strip()
        if cat != current_cat:
            current_cat = cat
            lines.append(f"## {cat}")
            lines.append("")
        title = (it.get("title") or "").strip()
        url = (it.get("url") or "").strip()
        summary = (it.get("summary") or "").strip()
        lines.append(f"- **{title}**")
        lines.append(f"  链接：[{title}]({url})")
        lines.append(f"  {summary}")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 引用链接汇总")
    lines.append("")
    for i, u in enumerate(urls, 1):
        if u:
            lines.append(f"{i}. {u}")
    return "\n".join(lines)


def save_report_json_primary(payload: dict, report_date: str) -> Path:
    """以 JSON 为主：先写 .json，再根据 payload 生成 .md 和 _urls.txt。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_date = report_date or (payload.get("report_date") or "")
    if not report_date:
        tz = ZoneInfo(DIGEST_TIMEZONE)
        report_date = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")
    payload["report_date"] = report_date
    items = payload.get("items") or []
    urls = payload.get("urls") or list(dict.fromkeys(it.get("url") for it in items if it.get("url")))
    payload["urls"] = urls

    prefix = _report_date_to_file_prefix(report_date)
    path_json = OUTPUT_DIR / f"daily_digest_{prefix}.json"
    with open(path_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    path_md = OUTPUT_DIR / f"daily_digest_{prefix}.md"
    path_md.write_text(_md_from_payload(payload), encoding="utf-8")

    path_urls_txt = OUTPUT_DIR / f"daily_digest_{prefix}_urls.txt"
    with open(path_urls_txt, "w", encoding="utf-8") as f:
        f.write(f"# 新闻日期: {report_date}\n")
        for u in urls:
            f.write(u + "\n")

    return path_json


def save_report(
    content: str,
    citations: list[str],
    report_date: str | None = None,
    allowed_categories: tuple[str, ...] | list[str] | None = None,
) -> Path:
    """将报告与引用链接写入 Markdown 文件，并单独保存 URL 列表便于后期访问。report_date 为新闻日期 YYYY-MM-DD（默认昨日）。当模型未输出有效 JSON 时使用。allowed_categories 为自定义主题时传入，用于解析节标题。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if report_date is None:
        tz = ZoneInfo(DIGEST_TIMEZONE)
        report_date = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")
    prefix = _report_date_to_file_prefix(report_date)
    path_md = OUTPUT_DIR / f"daily_digest_{prefix}.md"
    path_urls_txt = OUTPUT_DIR / f"daily_digest_{prefix}_urls.txt"
    path_json = OUTPUT_DIR / f"daily_digest_{prefix}.json"

    with open(path_md, "w", encoding="utf-8") as f:
        f.write(content)
        f.write("\n\n---\n\n## 引用链接汇总\n\n")
        for i, url in enumerate(citations, 1):
            f.write(f"{i}. {url}\n")

    with open(path_urls_txt, "w", encoding="utf-8") as f:
        f.write(f"# 新闻日期: {report_date}\n")
        for url in citations:
            f.write(url + "\n")

    cats = tuple(allowed_categories) if allowed_categories else None
    items = _parse_items_from_content(content, allowed_categories=cats)
    payload = {
        "report_date": report_date,
        "items": items,
        "urls": citations,
    }
    with open(path_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return path_md


def reparse_md_to_json(report_date: str) -> Path:
    """从已有 daily_digest_YYYY_MM_DD.md 重新解析并只更新同日的 .json（不调 API、不覆盖 .md）。"""
    prefix = _report_date_to_file_prefix(report_date)
    path_md = OUTPUT_DIR / f"daily_digest_{prefix}.md"
    path_json = OUTPUT_DIR / f"daily_digest_{prefix}.json"
    if not path_md.exists():
        raise FileNotFoundError(f"未找到 {path_md}，请先运行 digest.py 生成该日期的摘要。")
    content = path_md.read_text(encoding="utf-8")
    # 去掉文末「引用链接汇总」段，只保留正文
    if "## 引用链接汇总" in content:
        content = content.split("## 引用链接汇总")[0].strip()
    # 优先从正文中提取模型输出的 JSON（含 items），避免 Markdown 格式不一致导致 items 为空
    payload = _extract_json_from_content(content)
    if payload and payload.get("items"):
        report_date = payload.get("report_date") or report_date
        urls = payload.get("urls") or list(dict.fromkeys(it.get("url") for it in payload["items"] if it.get("url")))
        payload["report_date"] = report_date
        payload["urls"] = [u for u in urls if u and '"' not in u]
    else:
        items = _parse_items_from_content(content)
        urls = []
        if path_json.exists():
            try:
                data = json.loads(path_json.read_text(encoding="utf-8"))
                urls = [u for u in (data.get("urls") or []) if u and '"' not in u]
            except (json.JSONDecodeError, OSError):
                pass
        if not urls:
            urls = _extract_urls_from_content(content)
            urls = list(dict.fromkeys(urls))
        payload = {"report_date": report_date, "items": items, "urls": urls}
    path_json.parent.mkdir(parents=True, exist_ok=True)
    with open(path_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path_json


def main():
    import argparse

    parser = argparse.ArgumentParser(description="每日新闻摘要（昨日）：可选自定义主题或默认金融、AI、中美关系、日本政治")
    parser.add_argument("--date", default=None, help="新闻日期 YYYY-MM-DD，不填则用昨天")
    parser.add_argument("--themes", default=None, help='自定义主题，逗号分隔，如 "动漫,游戏,科技"；不填则用默认四类')
    parser.add_argument("--reparse", action="store_true", help="从已有 .md 重新解析并只更新 .json（不调 API）")
    parser.add_argument("--no-save", action="store_true", help="只打印到终端，不保存文件")
    parser.add_argument("--quiet", action="store_true", help="减少终端输出")
    args = parser.parse_args()

    themes = None
    if args.themes:
        themes = [t.strip() for t in args.themes.split(",") if t.strip()][:4]

    if args.reparse:
        report_date = args.date
        if not report_date:
            tz = ZoneInfo(DIGEST_TIMEZONE)
            report_date = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")
        path = reparse_md_to_json(report_date)
        if not args.quiet:
            prefix = _report_date_to_file_prefix(report_date)
            print(f"已从 daily_digest_{prefix}.md 重新解析并更新: {path}")
        return

    content, citations, report_date = run_digest(
        verbose=not args.quiet, report_date_override=args.date, themes=themes
    )
    print("\n" + "=" * 60 + "\n")
    print(content)

    if not args.no_save:
        payload = _extract_json_from_content(content)
        if payload and payload.get("items"):
            n_items = len(payload["items"])
            if n_items < 8 and not args.quiet:
                _print_progress(f"注意：本次仅解析到 {n_items} 条新闻（建议至少 8 条），可重新运行或检查 prompt。", verbose=True)
            path = save_report_json_primary(payload, report_date)
            if not args.quiet:
                prefix = _report_date_to_file_prefix(report_date)
                print(f"\nJSON 已保存（主输出）: {path}")
                print(f"Markdown 已生成: {path.parent / f'daily_digest_{prefix}.md'}")
                print(f"链接列表已保存: {path.parent / f'daily_digest_{prefix}_urls.txt'}")
        else:
            path = save_report(content, citations, report_date, allowed_categories=themes)
            if not args.quiet:
                prefix = _report_date_to_file_prefix(report_date)
                print(f"\n报告已保存（未解析到 JSON，使用 Markdown 解析）: {path}")
                print(f"链接列表已保存: {path.parent / f'daily_digest_{prefix}_urls.txt'}")
                print(f"URL+Summary JSON 已保存: {path.parent / f'daily_digest_{prefix}.json'}")
    elif not args.quiet and citations:
        print("\n引用链接:")
        for i, url in enumerate(citations, 1):
            print(f"  {i}. {url}")


if __name__ == "__main__":
    main()
