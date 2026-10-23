# 纸间 · Folio

本地论文双语阅读器：导入 PDF，将每段英文与中文上下对照，使用目录跳转章节，并保留原文插图。

**Windows · Python 3.10+ · AGPL-3.0-only**

当前仅保存于私有仓库，尚未公开发布。界面采用本项目的独立品牌和设计；关于第三方专利的核查尚无“不侵权”结论，详见 [独立设计与发布边界](DESIGN_PROVENANCE.md)。

## 功能

- PDF 分段、常见双栏顺序、章节目录与文内搜索。
- 一段英文、一段中文；逐段保存、暂停继续、失败重试。
- DeepSeek、OpenAI、阿里云百炼、天津大学、Ollama 和自定义 OpenAI 兼容 Chat Completions 接口。
- 首次启动不选择模型、地址或密钥，全部由使用者自行配置。服务商预设仅在主动选择后填充建议，不自动发送请求。
- 浅色、护眼和深色主题；自由调整背景、侧栏、强调色、中英文颜色、字体、字号、粗细、行距、内容宽度和界面字号；即时预览并本机保存。
- 原文位图及可识别的矢量图表区域，可查看大图；旧文献会在打开时补提取插图。
- 本地 KaTeX 渲染 `$...$`、`$$...$$`、`\(...\)`、`\[...\]` 数学公式。翻译提示要求保留有效 LaTeX，不凭空重建损坏公式；无需 CDN。
- 桌面侧栏可拖动调宽或折叠，顶部可收紧并进入专注模式，正文宽度可自适应。
- 导出 **双语 PDF、独立 HTML、Markdown**。PDF 内嵌中文字体，保留插图、书签和页码；HTML 内嵌图片可离线打开；Markdown 为文字导出。

## 启动

在 Windows 上双击 **启动纸间.cmd**。首次启动自动创建虚拟环境并安装依赖，随后打开 <http://127.0.0.1:8765>。需要 Python 3.10+ 加入 PATH；首次安装和远程翻译需要联网，无需 Node.js。

1. 选择服务商，主动选择或填写模型，再输入自己的 API Key；也可以关闭初次配置窗口体验原创示例。
2. 「测试连接」检查当前表单后「保存设置」。测试会发送一条短请求，可能消耗服务商额度。
3. 导入有文字层的 PDF，点击「全文翻译」，可以边译边读，或随时暂停。
4. 左侧切换文献库与目录，工具栏可搜索、打开原 PDF、调节外观和导出。

## API 设置

填写完整的 `chat/completions` 地址。模型名称、地域、权限和费用以官方文档及账户为准。切换服务商或地址时，需要重新填写对应密钥。

| 服务 | 完整地址示例 | 说明 |
| --- | --- | --- |
| DeepSeek | `https://api.deepseek.com/chat/completions` | 主动选择或填写账户可用模型。 |
| OpenAI | `https://api.openai.com/v1/chat/completions` | 使用支持 Chat Completions 的模型。 |
| 阿里云百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` | 可调整为业务空间专属域名；密钥需与地域匹配。 |
| 天津大学 | `https://ai.tju.edu.cn/api/v3/chat/completions` | 仅供有使用权限的用户自行配置。 |
| 本机 Ollama | `http://localhost:11434/v1/chat/completions` | 先启动服务并拉取模型，填本机模型名；回环地址无需密钥。 |
| 自定义 | 由使用者填写 | 兼容 OpenAI Chat Completions 文本接口。 |

接口参考：[DeepSeek](https://api-docs.deepseek.com/)、[阿里云百炼](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)、[Ollama](https://docs.ollama.com/api/openai-compatibility)。本项目不分发模型、账号、密钥或付费额度，也不支持所有服务商专有协议。

## 数据与隐私

导入和图片提取在本机完成。**翻译时会将段落文字发送至用户配置的 API 服务**；图片仅在本地显示，不发给模型。

- `data/papers/`：原始 PDF 及提取插图。
- `data/folio.sqlite3`：文字、译文、目录与翻译进度。
- `data/settings.json`：接口配置与 Windows 当前用户 DPAPI 加密的密钥。
- 浏览器本地存储：外观及上次打开的文献。

设置响应不返回完整密钥，导出物不含密钥。备份数据应保密，换电脑后须重填密钥。文献库「移除」只删除程序内副本、译文和插图，不修改原先导入位置的 PDF。服务只监听回环地址，适合本机个人使用；不要直接暴露为多人服务。详情见 [SECURITY.md](SECURITY.md)。

## PDF 支持范围

单份上限 50 MB、500 页；纯扫描件需先 OCR。复杂公式、跨页表格、图表和图注的识别可能不完整；最多提取 300 个图像区域并限制用量，区域可能包含少量周边文字。可随时打开原 PDF 核对。

PDF/HTML 导出使用固定浅色打印排版；外观设置只影响屏幕。缺失译文和丢失图片会明确标记。重排 PDF 不等于原版布局的逐像素复刻，也不会自动翻译图片内部文字。

阅读界面和离线 HTML 可显示排版后的 LaTeX；本版 PDF 与 Markdown 导出保留 LaTeX 源码，未将数学表达式另行渲染为公式图形。PDF 中原始公式如果不是可提取的数学文字，也不会自动恢复成准确的 LaTeX。

## 开发与验证

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m backend.app

.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest tests -q
# 可选开发校验，需要 Node.js（程序运行无需 Node.js）
node tests/test_math.cjs
```

端口冲突时设置 `$env:FOLIO_PORT = '8766'`，再运行 `python launcher.py`。后端位于 `backend/`，前端位于 `static/`，无外部字体或 CDN 依赖。

发布前运行 `python scripts/check_release.py`，再用 `git archive --format=zip -o dist/folio-source.zip HEAD` 生成仅含提交源码的包（先创建 `dist/`）。不要公开整个工作目录。

## 开源与内容权利

本项目采用 **GNU AGPL v3.0 only**，完整条款见 [LICENSE](LICENSE)，依赖见 [NOTICE.md](NOTICE.md)。PyMuPDF/MuPDF 使用 AGPL，因此本项目采用兼容许可。该许可允许商业使用，同时有对应源码、许可告知及适用的网络交互义务；闭源或联网服务化前须重新评估。

源码开源不代表论文、插图或译文可以公开传播，使用者须有权处理内容并遵守 API 条款。[开源检查记录](OPEN_SOURCE_REVIEW.md)说明了范围和条件，不能替代针对具体内容、部署方式或地区的法律判断。
