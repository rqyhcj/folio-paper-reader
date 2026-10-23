# 开源发布检查记录

检查日期：2026-10-23；KaTeX、字体及第三方专利信息补充检查：2026-10-24。范围为本项目自有源代码、静态资源、依赖声明及发布边界；不读取或公开用户论文库和加密凭据。此记录给出工程检查和许可证判断，不构成对所有地区、所有部署方式的全面法律保证。

## 结论及发布条件

当前状态：用户已授权将代码保存到 **GitHub 私有仓库**，暂不公开。下列依赖许可和隐私检查只能支持工程发布准备，不能据此认定不存在第三方专利、外观设计、版权或商标风险。公开前仍须处理后文所列的专利核查未决事项，并满足以下条件：

1. 项目采用 **AGPL-3.0-only**，包含完整 `LICENSE`、版权及无担保声明，并保留第三方许可说明。当前直接调用 PyMuPDF，不能只添加 MIT 许可证就认为整套程序成为宽松许可软件。
2. 新用户首次启动不带任何 API Key、默认模型或默认连接地址。服务商预设可以作为主动选择后的填写辅助，实际模型和密钥由使用者配置。
3. 只提交源代码、原创示例、测试和文档。`data/`、原 PDF、译文数据库、设置、截图输出、日志、环境变量文件、虚拟环境、缓存和发布临时文件均不进入仓库及源码发布包。`.gitignore` 不会移除已被 Git 跟踪的文件，提交前还须检查暂存区和历史。
4. 文档明确说明：翻译文本会发送给用户配置的第三方 API；用户负责选择有权使用的材料及符合其要求的服务。源码许可证不授予原论文、论文插图或译文的传播权。
5. 若未来发布包含 Python/依赖的安装包或容器，应按确切版本重新整理完整依赖清单、许可证、版权声明及相应源码的获取方式。本次源码发布不包含 `.venv` 或 Python 第三方二进制库；静态 KaTeX 发行文件及字体按下述单独许可随源代码提供。

## 依赖许可核对

| 组件 | 官方许可 | 发布注意事项 |
| --- | --- | --- |
| PyMuPDF / MuPDF | GNU AGPL v3 或 Artifex 商业许可 | 本项目选择 AGPL 路径；闭源集成须另行评估商业许可或更换引擎。 |
| FastAPI | MIT | 分发第三方代码时保留版权和许可。 |
| HTTPX | BSD-3-Clause | 保留版权、许可及免责声明，不暗示原作者为本产品背书。 |
| Uvicorn | BSD-3-Clause | 同上。 |
| python-multipart | Apache-2.0 | 保留许可和适用声明；如果分发修改版本，还应标记修改并保留适用 NOTICE。 |
| KaTeX 0.19.0 JavaScript / CSS / auto-render | MIT | 将官方发行文件置于 `static/vendor/katex/`；保留该目录中的上游 `LICENSE`。 |
| KaTeX 字体（TTF / WOFF / WOFF2） | SIL Open Font License 1.1 | 字体单独遵循 OFL，保留字体版权、Reserved Font Names 和完整 `fonts/OFL.txt`；不能只保留包根目录 MIT。 |

官方依据：[PyMuPDF 文档](https://pymupdf.readthedocs.io/en/latest/about.html)、[Artifex 许可说明](https://pymupdf.io/licensing)、[FastAPI LICENSE](https://raw.githubusercontent.com/fastapi/fastapi/master/LICENSE)、[HTTPX LICENSE](https://raw.githubusercontent.com/encode/httpx/master/LICENSE.md)、[Uvicorn LICENSE](https://raw.githubusercontent.com/Kludex/uvicorn/main/LICENSE.md)、[python-multipart LICENSE](https://raw.githubusercontent.com/Kludex/python-multipart/master/LICENSE.txt)。

KaTeX 的包级许可依据：[v0.19.0 LICENSE](https://raw.githubusercontent.com/KaTeX/KaTeX/v0.19.0/LICENSE)、[对应 package.json](https://raw.githubusercontent.com/KaTeX/KaTeX/v0.19.0/package.json)。字体的直接依据是官方 npm 包中全部 20 个 TTF 文件内嵌的 `name` 表：许可字段明确为 OFL 1.1，版权为 Design Science（2009–2010）及 Khan Academy（2014–2018），并列出各 `KaTeX_*` Reserved Font Name。已将实际元数据及来源说明写入 `static/vendor/katex/fonts/NOTICE.txt`，并加入[许可发布方的完整 OFL 1.1 正文](https://openfontlicense.org/documents/OFL.txt)。字体文件保持原样；不因项目整体采用 AGPL 而将字体改授 AGPL。

本机检查到的直接依赖版本分别为 PyMuPDF 1.28.2、FastAPI 0.161.1、HTTPX 0.28.1、Uvicorn 0.54.0、python-multipart 0.0.32。依赖声明允许版本范围，因此这些不是所有未来安装结果的保证。Python 间接依赖含 MIT、BSD、PSF、Apache 和 MPL 等许可；例如 certifi 为 MPL-2.0。MuPDF 的内置第三方组件也需随其具体发行物保留原始许可材料。

AGPL 的远程网络条款与分发要求见 [PyMuPDF 上游所附完整 AGPL 文本](https://raw.githubusercontent.com/pymupdf/PyMuPDF/1.26.7/COPYING)。如将本项目修改成联网服务，须向远程使用者显著提供取得对应版本源码的机会；若分发二进制，还须满足对应源码的提供要求。AGPL 不禁止商业使用，但要求履行相应义务。

## 数据、凭据和素材

- 初次审阅的生产源代码中未发现真实密钥常量；测试所用密钥为明确的虚构测试值。原配置的默认天津接口和“本机已配置用户密钥”说明需在公开版本中清理。原密钥曾通过聊天提供，建议在其服务商后台轮换，公开仓库时不复用或重发该密钥。
- API Key 仅用于请求头；公开设置响应不返回完整密钥。Windows 版本使用当前系统用户的 DPAPI 加密本机保存值。这不取代操作系统账户安全，整个 `data/` 仍应排除在发布物之外。
- 服务监听回环地址并检查浏览器跨站写请求，定位为本机个人工具。直接暴露端口或改成多人联网服务需要重新设计认证、访问控制、资源限制和数据隔离。
- 界面正文采用系统字体栈；公式另使用本地随附的 KaTeX 字体，共 20 种字形的 TTF / WOFF / WOFF2 发行文件。KaTeX 的 JS、CSS、auto-render 和字体均从本机静态目录加载，公式显示不需要 CDN 或远程字体请求。源代码中的 SVG 为项目内图形。示例文字明确标记为原创产品演示，没有作为已发表论文展示。不要将用户提供的参考截图、真实论文页面或含个人信息的调试截图自动加入开源仓库。
- 原 PDF、提取的文字、插图、译文及导出物可能受原作者或出版者权利约束。个人阅读用途与公开再发布须分别判断，不能将软件开源理解为论文内容可以任意公开。

《中华人民共和国著作权法》第十条涉及翻译等权利，第二十四条规定的个人学习、科研等限制有适用条件，并非无条件公开传播的授权。依据：[现行法政府网站文本](https://fjca.miit.gov.cn/zwgk/zcwj/wjfb/art/2020/art_3fd5e3c5e6624d95af229036f95e20d3.html)。

## 模型接口说明

DeepSeek 官方支持 OpenAI 兼容 Chat Completions、Bearer 认证和用户自行申请的 API Key，可作为本项目的可配置服务商。使用服务商 API 与分发其模型权重属于不同事项，本项目不打包模型权重，也不承诺任何机构接口对所有用户开放。

依据：[DeepSeek 官方首次调用文档](https://api-docs.deepseek.com/api/responses)、[DeepSeek API 认证文档](https://api-docs.deepseek.com/api/deepseek-api/)。模型名称和服务条款会变化，应以账户当前可用模型和官方文档为准。

## 靠岸学术及专利核查：公开前仍未决

2026-10-24 按用户要求检查靠岸学术 / Scholaread 的公开一手资料。此处的产品识别基于其官方域名及用户所示产品名称；不将其他名称中含“靠岸”或“靠岸 AI”的产品自动视为同一主体。

| 核查事项 | 可以确认的事实 | 仍不能确认的事项 |
| --- | --- | --- |
| 国内运营主体 | 官方《服务协议》明确列出上海亦答网络科技有限公司，页面更新/生效日期为 2024-05-21。 | 不据该页面推断所有关联公司、现时专利受让人或被许可人。 |
| 官方权利声明 | 同一协议第 4.1 条概括主张产品、技术、软件、界面等知识产权，并提及专利权。 | 该概括声明没有列出可用于本次比对的具体专利申请号、公开号、授权号、权利要求及法律状态。 |
| 国外发行线索 | Apple 官方商店的 Scholaread 产品条目显示供应商 Astronet Technology PTE LTD；中国开发者页面显示上海亦答网络科技有限公司及靠岸学术产品。 | 这只是发行主体线索，不是两家公司相互持股、专利权属或某项专利许可的证明。 |
| 专利文献 | 检索了产品中英文名、上海亦答公司全称、Astronet Technology 及“专利 / patent / patented”等组合，包含官网与公开专利索引的定向查询。 | 在本次可访问资料中未取得能够可靠归属于该产品、且附完整权利要求与法律状态的具体专利文献，故不能进行逐项权利要求比对。 |

主体和声明依据：[靠岸学术官方服务协议](https://www.scholaread.cn/help/terms)、[Apple 中国开发者页面](https://apps.apple.com/cn/developer/%E4%B8%8A%E6%B5%B7%E4%BA%A6%E7%AD%94%E7%BD%91%E7%BB%9C%E7%A7%91%E6%8A%80%E6%9C%89%E9%99%90%E5%85%AC%E5%8F%B8/id1644015288)、[Apple 产品供应商信息](https://apps.apple.com/it/app/scholaread-translator-reader/id1643446657?platform=ipad)。官网提到阅读和分析专利文献的文章，不等于其自身取得某项专利的证明。

“未取得可核实的具体文献”不等于“没有专利”。国家知识产权局检索系统本次直接访问返回 HTTP 412，未完成该库的交互式检索、完整申请人关系扩展、专利家族、审查档案和现行法律状态核验；公开网页索引亦存在覆盖和时效限制，尚未公开的申请也可能无法检到。本记录不冒充已完成专业自由实施（FTO）调查。官方后续核查入口：[国家知识产权公共服务平台](https://ggfw.cnipa.gov.cn/)、[专利检索及分析系统](https://pss-system.cponline.cnipa.gov.cn/conventionalSearch)。

中国《专利法》第六十四条规定：发明、实用新型保护范围以权利要求内容为准，说明书和附图用于解释；外观设计范围则以图片或照片中的产品外观设计为准。因此“字体、颜色不同”“独立写代码”“免费或开源”均不能单独形成不侵权结论，图形用户界面相关外观设计也须另行核对。发明/实用新型侵权比对还涉及全部技术特征及其相同或等同情形，不能仅凭功能名称、产品宣传或截图完成。

法律依据：[国家知识产权局《专利法（2020 年修正）》第十一条、第六十四条](https://www.cnipa.gov.cn/art/2020/11/23/art_97_155167.html)、[最高人民法院关于审理侵犯专利权纠纷案件的解释，第七至十一条](https://www.court.gov.cn/zixun/xiangqing/1.html)、[最高人民法院 2020 年相关司法解释修正决定](https://www.court.gov.cn/xinshidai/xiangqing/282641.html)。

当前可做的是保持自有品牌、自有界面与实现，不复制对方程序、标识、文案或资源，保存独立开发及设计来源记录，并保持仓库私有。它们有助于说明来源与减少混淆，但不替代专利判断。准备公开、分发或运营前，应基于可取得的确切专利号、完整权利要求、外观设计图及有效状态，由具有相关技术领域经验的专利专业人士完成中国及目标市场的检索和逐项比对；取得具体文献后才有条件讨论针对技术特征的设计规避。本记录当前不给出“符合对方专利要求”“已经规避专利”或“不会被诉”的保证。

## 每次公开发布前

检查 Git 暂存区、提交历史和实际压缩包文件列表，运行测试及密钥扫描，确认没有用户配置、用户文献和个人路径。只在上述条件通过后将此记录作为有条件的发布依据；后续新增依赖、素材、云端功能或联网部署需要重新检查。
