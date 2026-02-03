"""
TTS 播客音频生成：读取 podcast_script_YYYY-MM-DD.md，按 4096 字符分块调用 OpenAI TTS，
将多段 mp3 用 pydub 拼接为 reports/podcast_YYYY-MM-DD.mp3。
加 --sync 时按句分块并输出 podcast_YYYY-MM-DD_sync.json，供同步朗读页按句高亮。
依赖：pip install pydub，且系统需安装 ffmpeg（pydub 处理 mp3 用）。
"""
import json
import re
import time
from pathlib import Path

# #region agent log
DEBUG_LOG_PATH = "/Users/leiyang/Desktop/Coding/.cursor/debug.log"
def _dbg(msg: str, data: dict, hypothesis_id: str = "") -> None:
    try:
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": int(time.time() * 1000), "location": "tts.py:main", "message": msg, "data": data, "sessionId": "debug-session", "hypothesisId": hypothesis_id}) + "\n")
    except Exception:
        pass
# #endregion

import requests

from config import (
    OPENAI_API_KEY,
    OUTPUT_DIR,
    PROXIES,
    TTS_MODEL,
    TTS_SPEED,
    TTS_VOICE,
)

OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
TTS_TIMEOUT = 120
MAX_CHARS = 4096


def normalize_script(text: str) -> str:
    """去掉 Markdown 标题行（以 # 开头），规整空行为单个 \\n\\n。"""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(line)
    # 合并连续空行为一个 \n\n
    normalized = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return normalized.strip()


def split_into_sentences(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """
    按句号（。）与换行切分，保留分隔符在句末；若某句超过 max_chars 则硬切为多块。
    用于 --sync 时的按句 TTS。
    """
    if not text.strip():
        return []
    parts = re.split(r"(。|\n)", text)
    sentences = []
    buf = ""
    for i, p in enumerate(parts):
        buf += p
        if (p in ("。", "\n") or i == len(parts) - 1) and buf.strip():
            sentences.append(buf.strip())
            buf = ""
    out = []
    for s in sentences:
        while len(s) > max_chars:
            out.append(s[:max_chars])
            s = s[max_chars:]
        if s:
            out.append(s)
    return out


def split_into_chunks(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """
    按段落（\\n\\n）组合成块，每块 ≤ max_chars。
    若单段超过 max_chars，再按句号（。）或换行切分。
    """
    if not text.strip():
        return []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    current = []

    def flush_current() -> None:
        if current:
            chunks.append("\n\n".join(current))
            current.clear()

    def split_long_paragraph(para: str) -> list[str]:
        if len(para) <= max_chars:
            return [para]
        # 按句号。或换行切分，保留分隔符在句末
        parts = re.split(r"(。|\n)", para)
        sentences = []
        buf = ""
        for i, p in enumerate(parts):
            buf += p
            if (p in ("。", "\n") or i == len(parts) - 1) and buf.strip():
                sentences.append(buf.strip())
                buf = ""
        # 将句子合并成 ≤ max_chars 的块
        result = []
        current = ""
        for s in sentences:
            if not current:
                current = s
            elif len(current) + 1 + len(s) <= max_chars:
                current += "\n" + s
            else:
                result.append(current)
                current = s
        if current:
            result.append(current)
        # 若单句仍超长，硬切
        out = []
        for s in result:
            while len(s) > max_chars:
                out.append(s[:max_chars])
                s = s[max_chars:]
            if s:
                out.append(s)
        return out

    for para in paragraphs:
        if len(para) > max_chars:
            flush_current()
            for sub in split_long_paragraph(para):
                chunks.append(sub)
            continue
        current_len = sum(len(p) for p in current) + (2 * (len(current) - 1) if len(current) > 1 else 0)
        if current and current_len + 2 + len(para) > max_chars:
            flush_current()
        current.append(para)

    flush_current()
    return chunks


def text_to_speech_chunk(text: str, out_path: Path) -> None:
    """单块文本调用 OpenAI TTS，写入 out_path。失败时重试一次。"""
    if not OPENAI_API_KEY:
        raise ValueError("未设置 OPENAI_API_KEY，请在 .env 中配置")
    payload = {
        "model": TTS_MODEL,
        "voice": TTS_VOICE,
        "input": text,
        "response_format": "mp3",
        "speed": TTS_SPEED,
    }
    last_err = None
    for attempt in range(2):
        try:
            resp = requests.post(
                OPENAI_SPEECH_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=TTS_TIMEOUT,
                proxies=PROXIES,
            )
            resp.raise_for_status()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(resp.content)
            return
        except Exception as e:
            last_err = e
    raise RuntimeError(f"TTS 请求失败（已重试）: {last_err}") from last_err


def get_chunk_duration_sec(path: Path) -> float:
    """返回单段 mp3 的时长（秒）。"""
    import shutil
    if not shutil.which("ffprobe"):
        raise FileNotFoundError(
            "未找到 ffprobe（ffmpeg 的一部分）。pydub 处理 mp3 需要安装 ffmpeg，"
            "macOS 可运行: brew install ffmpeg"
        )
    from pydub import AudioSegment
    seg = AudioSegment.from_mp3(str(path))
    return len(seg) / 1000.0


def concat_audio(chunk_paths: list[Path], out_path: Path) -> None:
    """用 pydub 按顺序拼接 mp3，导出到 out_path，并删除临时 chunk 文件。"""
    import shutil

    if not shutil.which("ffprobe"):
        raise FileNotFoundError(
            "未找到 ffprobe（ffmpeg 的一部分）。pydub 处理 mp3 需要安装 ffmpeg，"
            "macOS 可运行: brew install ffmpeg"
        )
    from pydub import AudioSegment

    if not chunk_paths:
        raise ValueError("没有音频片段可拼接")
    combined = AudioSegment.empty()
    for p in chunk_paths:
        combined += AudioSegment.from_mp3(str(p))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.export(str(out_path), format="mp3")
    for p in chunk_paths:
        try:
            p.unlink()
        except OSError:
            pass


def main() -> None:
    import argparse
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from config import DIGEST_TIMEZONE

    parser = argparse.ArgumentParser(
        description="从播客脚本生成 TTS 音频（OpenAI TTS + pydub 拼接）"
    )
    parser.add_argument("--date", default=None, help="脚本日期 YYYY-MM-DD，默认昨日")
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="只做预处理与分块并打印，不调用 TTS、不写文件",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="按句分块 TTS 并输出 sync JSON，供同步朗读页按句高亮",
    )
    args = parser.parse_args()
    # #region agent log
    _dbg("args after parse", {"sync": args.sync, "date": args.date, "no_save": args.no_save}, "A")
    # #endregion

    if args.date:
        report_date = args.date
    else:
        tz = ZoneInfo(DIGEST_TIMEZONE)
        report_date = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")

    script_path = OUTPUT_DIR / f"podcast_script_{report_date}.md"
    if not script_path.exists():
        raise FileNotFoundError(
            f"未找到播客脚本: {script_path}，请先运行 podcast.py 生成该日期的脚本。"
        )

    with open(script_path, "r", encoding="utf-8") as f:
        raw = f.read()

    normalized = normalize_script(raw)
    # #region agent log
    _dbg("after normalize", {"len_raw": len(raw), "len_normalized": len(normalized), "double_newlines": normalized.count("\n\n"), "report_date": report_date}, "B,C,D")
    # #endregion
    if args.sync:
        chunks = split_into_sentences(normalized)
    else:
        chunks = split_into_chunks(normalized)
    # #region agent log
    _dbg("after split", {"branch": "sync" if args.sync else "chunks", "num_chunks": len(chunks), "first_chunk_len": len(chunks[0]) if chunks else 0}, "A,E")
    # #endregion
    if not chunks:
        raise ValueError("脚本经预处理后无有效正文，无法生成音频。")

    if args.no_save:
        print(f"日期: {report_date}, 分块数: {len(chunks)}")
        for i, c in enumerate(chunks):
            print(f"--- chunk {i + 1} ({len(c)} 字符) ---")
            print(c[:200] + "..." if len(c) > 200 else c)
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    chunk_paths = []
    total = len(chunks)
    for i, chunk_text in enumerate(chunks):
        path = OUTPUT_DIR / f".tts_chunk_{i}.mp3"
        print(f"TTS {i + 1}/{total} ...")
        text_to_speech_chunk(chunk_text, path)
        chunk_paths.append(path)

    segments = None
    if args.sync:
        # 在 concat 前读取每段时长，构建句级 sync 数据
        t = 0.0
        segments = []
        for i, (path, text) in enumerate(zip(chunk_paths, chunks)):
            dur = get_chunk_duration_sec(path)
            segments.append({"text": text, "start": t, "end": t + dur})
            t += dur

    out_path = OUTPUT_DIR / f"podcast_{report_date}.mp3"
    print("拼接音频 ...")
    concat_audio(chunk_paths, out_path)
    print(f"已保存: {out_path}")

    if args.sync and segments is not None:
        sync_path = OUTPUT_DIR / f"podcast_{report_date}_sync.json"
        with open(sync_path, "w", encoding="utf-8") as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)
        print(f"已保存: {sync_path}")


if __name__ == "__main__":
    main()
