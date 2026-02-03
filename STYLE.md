# DailyNewsDigest 设计规范

本项目的 UI/UX 设计语言与可复用 token，便于新增或修改页面时一致应用。实现见 `static/base.css`。

## 设计 Token

### 字体

| 用途 | 取值 | 使用场景 |
|------|------|----------|
| 中文主 | `PingFang SC`, `Microsoft YaHei`, sans-serif | index、digest、podcast、japanese_points |
| 日语主 | `Hiragino Sans`, `Yu Gothic`, sans-serif | sync_reader 或日文正文块 |
| h1 | 1.35rem（首页可 1.5rem） | 页面标题 |
| h2 | 1.1rem | 区块标题 |
| 正文 | 0.9rem–0.95rem | 正文、表格 |
| 提示 | 0.9rem | .hint、说明文字 |

### 色彩

| 用途 | 取值 |
|------|------|
| 主链/强调 | `#06c` |
| 正文 | `#333` / `#444` / `#555` |
| 辅助/提示 | `#666` |
| 错误 | `#c00` |
| 边框/分割 | `#ccc`、`#eee`、`#f0f0f0` |
| 背景（浅） | `#f5f5f5` |
| 高亮（同步朗读） | `rgba(255, 220, 100, 0.45)` |

### 间距与布局

| 用途 | 取值 |
|------|------|
| 页面 padding | 1rem（首页可 1.5rem） |
| 首页 max-width | 640px |
| 内容/表格页 max-width | 900px |
| 阅读列（同步朗读） | 42rem |
| 通用 gap | 0.5rem、0.75rem、1rem、1.5rem |
| 圆角（卡片/按钮/弹窗） | 8px |
| 高亮圆角 | 2px |

### 组件约定

- **返回首页**：蓝字 `#06c`、0.9rem、无下划线，hover 下划线；使用类 `.back-link`。
- **工具栏**：flex、align-items: center、gap: 0.75rem、flex-wrap: wrap；使用类 `.toolbar`。
- **提示/错误**：`.hint` 灰 #666、0.9rem；`.error` 红 #c00。
- **主按钮/卡片**：border-radius: 8px；卡片 border: 1px solid #ccc。
- **同步朗读高亮**：半透明黄、圆角 2px、transition 0.15s。

### 响应式 (Responsive)

- **断点**：`768px`（平板/小屏笔记本）、`480px`（手机）；媒体查询使用 `max-width`。
- **触控**：小屏下导航链接、工具栏按钮等可点击区域至少 44px 高度，便于手指点按。
- **布局**：宽度 &lt; 768px 时头部竖排（品牌在上、导航在下）；页面 padding 由 base.css 中的变量在小屏下自动缩小。
- **新页面**：继续使用 `var(--page-padding-home)` 及共享的 `.header` / `.nav-top`，即可自动应用 base.css 的响应式规则。

## 使用说明

1. 新增页面时在 `<head>` 中引入：`<link rel="stylesheet" href="/static/base.css">`。
2. 优先使用 base.css 中的 CSS 变量（`var(--color-link)` 等）和通用类（`.back-link`、`.toolbar`、`.hint`、`.error`）。
3. 页面特有样式仍写在页面内 `<style>` 中，但颜色、字体、间距尽量用 token（变量或与 STYLE.md 一致）。
4. 改版时先改 `static/base.css` 中的变量或通用类，即可带动多页一致更新。
