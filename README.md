# 每日新闻摘要 (Daily News Digest)

使用 **OpenAI Responses API** 与 **web_search** 每天自动搜集并总结以下四个主题的 **昨日** 新闻：

1. **金融**：金融市场、银行、证券、保险、监管等  
2. **AI**：人工智能、大模型、AI 公司与监管等  
3. **中美关系**：中美贸易、外交、科技与政策动态  
4. **日本政治**：日本国内政治、内阁与重大政策  

每条新闻会包含：**标题**、**原文链接**、**2–4 句中文总结**，并汇总到一份 Markdown 报告中。

## 环境要求

- Python 3.9+
- [OpenAI API Key](https://platform.openai.com/api-keys)

## 安装

```bash
cd DailyNewsDigest
pip install -r requirements.txt
```

复制环境变量示例并填入你的 OpenAI API Key：

```bash
cp .env.example .env
# 编辑 .env，设置 OPENAI_API_KEY=你的密钥
```

## 网页应用（统一入口）

运行 Flask 后，在浏览器打开 **http://127.0.0.1:5000/** 进入首页，从首页可进入以下模块：

- **新闻摘要**：按日期查看当日摘要（需先运行 `digest.py`）
- **播客稿**：按日期查看当日日语播客稿（需先运行 `podcast.py`）
- **播客音频 / 同步朗读**：按句高亮同步朗读（需先运行 `tts.py --sync`）
- **日语要点抽取**：N1-N2 单词与文法表格、新要点学习、温故知新（需先运行 `japanese_points.py`）

```bash
cd DailyNewsDigest
python app.py
```

浏览器访问：http://127.0.0.1:5000/

## 使用（命令行生成数据）

**手动运行一次（并保存到 `reports/`）：**

```bash
python digest.py
```

**只打印到终端、不写文件：**

```bash
python digest.py --no-save
```

**减少终端提示：**

```bash
python digest.py --quiet
```

报告会保存到 `reports/` 目录（日期用下划线，如 `daily_digest_2026_02_02.json`）：
- `daily_digest_YYYY_MM_DD.md`：正文总结 + 文末「引用链接汇总」
- `daily_digest_YYYY_MM_DD_urls.txt`：仅链接列表（一行一个 URL），便于复制或脚本处理
- `daily_digest_YYYY_MM_DD.json`：**URL 与 Summary 的 JSON**，含 `report_date`、`items`（每条含 `title`、`url`、`summary`、`category` 分类）、`urls` 数组，便于程序读取与后期访问

## Podcast 脚本生成（原子新闻 / 主播美香）

在已有当日摘要 JSON 的前提下，可生成播客稿（日语）：

```bash
python podcast.py              # 默认昨日日期
python podcast.py --date 2026-02-02
python podcast.py --no-save    # 只打印，不写文件
```

脚本包含四部分：
1. **开场**：简单介绍「原子新闻」、主播美香、播客目的  
2. **各分类**：金融 / AI / 中美关系 / 日本政治，每类根据该分类下的全部新闻写一段约 200 字日语总结（一个类别一个类别做完）  
3. **本日总结**：把各分类总结合在一起写一段「本日总结」  
4. **结束语**：简短的播客结束语（易记、有辨识度）  

输出保存为 `reports/podcast_script_YYYY-MM-DD.md`。

## 生成播客音频（TTS）

在已有播客脚本的前提下，可将脚本转为日语朗读音频（OpenAI TTS + pydub 拼接）：

```bash
python tts.py              # 默认昨日日期
python tts.py --date 2026-02-02
python tts.py --no-save    # 只做预处理与分块并打印，不调用 TTS、不写文件
```

- 脚本会按 4096 字符分块调用 OpenAI TTS，再将多段 mp3 拼接为单文件。
- 输出保存为 `reports/podcast_YYYY-MM-DD.mp3`。
- **依赖**：除 `pip install -r requirements.txt` 外，需在系统安装 [ffmpeg](https://ffmpeg.org/)（pydub 处理 mp3 用）。macOS 可 `brew install ffmpeg`。
- 可选 `.env`：`TTS_MODEL=tts-1-hd`、`TTS_VOICE=nova`、`TTS_SPEED=1.0`。

### 按句高亮（同步朗读）

若需要「播放音频时按句高亮」的同步朗读页，请先带 `--sync` 生成音频与句级时间数据：

```bash
python tts.py --date 2026-02-02 --sync
```

会额外生成 `reports/podcast_YYYY-MM-DD_sync.json`。启动网页应用（`python app.py`）后，从首页点击「播客音频/同步朗读」或访问 `http://127.0.0.1:5000/sync_reader.html?date=2026-02-02`（将日期改为你生成的日期）。页面会加载该日期的 mp3 与 sync JSON，播放时按句高亮当前句；点击某句可跳转到该句播放。

## 日语要点抽取（N1-N2 单词与文法）

从当日播客稿中抽取 20 个较难 N1-N2 单词、20 个较难 N1-N2 文法，并在网页上展示表格与卡片学习（新要点学习 / 温故知新）。

**1. 生成要点 JSON**

在已有播客脚本的前提下运行：

```bash
python japanese_points.py              # 默认昨日日期
python japanese_points.py --date 2026-02-02
```

会生成 `reports/japanese_points_YYYY-MM-DD.json`（含 `words` 与 `grammar` 数组）。

**2. 启动网页应用**

从首页（http://127.0.0.1:5000/）点击「日语要点抽取」，或直接访问 `http://127.0.0.1:5000/japanese_points.html`。

**3. 页面功能**

- **日语要点抽取页**：选择日期并点击「加载」，展示单词表与文法表；按钮「新要点学习」「温故知新」。
- **新要点学习**：弹窗选择「单词」或「文法」后进入卡片学习；正面为单词/平假名或文法/中文解释，背面为中文解释与例句/翻译；点击卡片翻转；按钮「熟悉」「不熟悉」「结束」。按「不熟悉」会将当前条追加到温故知新列表（`reports/review_words.json` 或 `reports/review_grammar.json`）。
- **温故知新**：弹窗选择「单词」或「文法」后进入复习；卡片从上述 JSON 读取；按「熟悉」会从列表中删除该条，「不熟悉」保留；「结束」返回要点页。进度（当前索引）会保存到 `reports/review_progress.json`，下次进入可从上次位置继续。

## 每天自动运行（可选）

在 macOS/Linux 上用 cron 每天固定时间跑一次。**推荐**使用 `run_daily.py` 执行完整流水线（摘要 → 播客稿 → 音频与同步 JSON → 日语要点），完成情况会写入 `logs/YYYY-MM-DD.log`。

### 北京时间凌晨 1:00 跑完整流水线

请把 `/path/to/DailyNewsDigest` 换成你本机项目路径，`/usr/bin/python3` 可改为你的 Python 解释器路径：

```bash
crontab -e
```

若服务器时区为 **Asia/Shanghai**（北京时间），加入：

```
0 1 * * * cd /path/to/DailyNewsDigest && /usr/bin/python3 run_daily.py >> logs/cron_stdout.log 2>&1
```

若服务器为 **UTC**，则需对应 01:00 北京时间的 UTC 时刻（冬令时 17:00 前一天），例如：

```
0 17 * * * cd /path/to/DailyNewsDigest && /usr/bin/python3 run_daily.py >> logs/cron_stdout.log 2>&1
```

或在 crontab 顶部设置 `TZ=Asia/Shanghai` 后使用 `0 1 * * *`。

### 仅跑摘要（可选）

若只需每天生成新闻摘要、不生成播客与要点，可沿用单脚本，例如每天 20:00：

```
0 20 * * * cd /path/to/DailyNewsDigest && /usr/bin/python3 digest.py >> logs/cron.log 2>&1
```

也可用系统自带的「定时任务」或其它调度工具，在指定时间执行：

```bash
cd /path/to/DailyNewsDigest && python run_daily.py   # 完整流水线
cd /path/to/DailyNewsDigest && python digest.py      # 仅摘要
```

## 费用说明

- 使用 OpenAI Responses API 与 web_search 会按 [OpenAI 定价](https://platform.openai.com/docs/pricing) 计费（按 token 与工具调用次数）。
- 每天跑一次、每次约数分钟推理，通常消耗在可接受范围内；可在 [OpenAI Usage](https://platform.openai.com/usage) 查看用量。

## 项目结构

```
DailyNewsDigest/
├── config.py         # 配置（API Key、输出目录、超时、代理、TTS）
├── run_daily.py      # 每日流水线：digest → podcast → tts --sync → japanese_points，日志写入 logs/YYYY-MM-DD.log
├── digest.py         # 主逻辑：调用 OpenAI Responses API + web_search，生成报告
├── podcast.py        # 播客稿生成（日语）
├── tts.py            # TTS 播客音频（OpenAI TTS + pydub 拼接）
├── japanese_points.py # 日语要点抽取（N1-N2 单词与文法）
├── app.py            # Flask：统一入口 + 各模块页 + 温故知新 API
├── logs/             # 每日流水线日志 logs/YYYY-MM-DD.log
├── index.html        # 首页（新闻摘要、播客稿、同步朗读、日语要点入口）
├── digest.html       # 新闻摘要展示页
├── podcast.html      # 播客稿展示页
├── japanese_points.html # 日语要点抽取页（表格 + 新要点学习 + 温故知新）
├── sync_reader.html  # 同步朗读页（按句高亮，需先 python tts.py --sync）
├── requirements.txt
├── .env.example
├── README.md
└── reports/        # 生成的每日报告、播客稿与音频（Markdown / JSON / mp3）
```

## 时区与「昨日」说明

- 默认以 **北京时间（Asia/Shanghai）** 的「昨日」为准。脚本会按该时区计算昨天日期（如 2026年02月02日），在 prompt 中写明「昨日」即该日期，并说明新闻来源可能用其他时区，以该日期为基准筛选。
- 报告文件名按**新闻日期**命名（日期用下划线），如 `daily_digest_2026_02_02.md` 表示 2 月 2 日的新闻摘要。
- 可在 `.env` 中设置 `DIGEST_TIMEZONE=Asia/Shanghai`（或其它 IANA 时区）覆盖默认。

## 故障排查

- **未设置 OPENAI_API_KEY**：在项目根目录创建 `.env`，写入 `OPENAI_API_KEY=你的密钥`，或导出环境变量 `export OPENAI_API_KEY=...`。
- **请求超时**：单次请求默认超时 1 小时（`DIGEST_TIMEOUT=3600`），超时后会自动重试最多 2 次。若仍不够，可在 `.env` 中设置 `DIGEST_TIMEOUT=7200`（2 小时）。若网络受限，可设置 `HTTP_PROXY` / `HTTPS_PROXY` 使用代理。
- **某天没有某主题新闻**：模型会如实写「当日该主题报道较少」并少列几条，属正常情况。
# DailyNewsDigest