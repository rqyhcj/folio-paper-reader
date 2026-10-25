# 开源发布检查记录

检查日期：2026-10-23；KaTeX、字体及第三方专利信息补充检查：2026-10-24。范围为本项目自有源代码、静态资源、依赖声明及发布边界；不读取或公开用户论文库和加密凭据。此记录给出工程检查和许可证判断，不构成对所有地区、所有部署方式的全面法律保证。

## 结论及发布条件

发布状态更新（2026-10-24）：用户已明确授权将仓库从私有改为公开，源码按 **AGPL-3.0-only** 发布。当前文件与全部 Git 历史的凭据及私有数据检查已通过。下述官网与专利核查保留为当时的记录，其中“保持私有”及“暂不公开”的历史决定已由本次明确授权替代；专利核查未决事项仍未获得新的结论。下列依赖许可和隐私检查不能据此认定不存在第三方专利、外观设计、版权或商标风险。源码发布遵循以下边界：

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
| Ziamath / Ziafont / latex2mathml | MIT | 2026-10-25 新增离线 PDF 数学排版依赖；通过 pip 安装，分发其代码或二进制时保留上游版权和许可。 |
| Ziamath 随附 STIX Two Math 字体 | SIL Open Font License 1.1 | 字体保留自身 OFL、版权和 Reserved Font Names；当前源码仓库不另行复制该字体。 |
| KaTeX 0.19.0 JavaScript / CSS / auto-render | MIT | 将官方发行文件置于 `static/vendor/katex/`；保留该目录中的上游 `LICENSE`。 |
| KaTeX 字体（TTF / WOFF / WOFF2） | SIL Open Font License 1.1 | 字体单独遵循 OFL，保留字体版权、Reserved Font Names 和完整 `fonts/OFL.txt`；不能只保留包根目录 MIT。 |

官方依据：[PyMuPDF 文档](https://pymupdf.readthedocs.io/en/latest/about.html)、[Artifex 许可说明](https://pymupdf.io/licensing)、[FastAPI LICENSE](https://raw.githubusercontent.com/fastapi/fastapi/master/LICENSE)、[HTTPX LICENSE](https://raw.githubusercontent.com/encode/httpx/master/LICENSE.md)、[Uvicorn LICENSE](https://raw.githubusercontent.com/Kludex/uvicorn/main/LICENSE.md)、[python-multipart LICENSE](https://raw.githubusercontent.com/Kludex/python-multipart/master/LICENSE.txt)。

KaTeX 的包级许可依据：[v0.19.0 LICENSE](https://raw.githubusercontent.com/KaTeX/KaTeX/v0.19.0/LICENSE)、[对应 package.json](https://raw.githubusercontent.com/KaTeX/KaTeX/v0.19.0/package.json)。字体的直接依据是官方 npm 包中全部 20 个 TTF 文件内嵌的 `name` 表：许可字段明确为 OFL 1.1，版权为 Design Science（2009–2010）及 Khan Academy（2014–2018），并列出各 `KaTeX_*` Reserved Font Name。已将实际元数据及来源说明写入 `static/vendor/katex/fonts/NOTICE.txt`，并加入[许可发布方的完整 OFL 1.1 正文](https://openfontlicense.org/documents/OFL.txt)。字体文件保持原样；不因项目整体采用 AGPL 而将字体改授 AGPL。

本机检查到的直接依赖版本分别为 PyMuPDF 1.28.2、FastAPI 0.161.1、HTTPX 0.28.1、Uvicorn 0.54.0、python-multipart 0.0.32。依赖声明允许版本范围，因此这些不是所有未来安装结果的保证。Python 间接依赖含 MIT、BSD、PSF、Apache 和 MPL 等许可；例如 certifi 为 MPL-2.0。MuPDF 的内置第三方组件也需随其具体发行物保留原始许可材料。

AGPL 的远程网络条款与分发要求见 [PyMuPDF 上游所附完整 AGPL 文本](https://raw.githubusercontent.com/pymupdf/PyMuPDF/1.26.7/COPYING)。如将本项目修改成联网服务，须向远程使用者显著提供取得对应版本源码的机会；若分发二进制，还须满足对应源码的提供要求。AGPL 不禁止商业使用，但要求履行相应义务。

## 数据、凭据和素材

2026-10-25 数学排版依赖补充依据：[Ziamath MIT](https://raw.githubusercontent.com/cdelker/ziamath/master/LICENSE.txt)、[Ziafont MIT](https://raw.githubusercontent.com/cdelker/ziafont/master/LICENSE.txt)、[latex2mathml MIT](https://raw.githubusercontent.com/roniemartinez/latex2mathml/master/LICENSE)、[STIX 字体 OFL](https://raw.githubusercontent.com/stipub/stixfonts/master/OFL.txt)。本次安装版本为 Ziamath 0.13、Ziafont 0.11、latex2mathml 3.81.1；依赖仍按声明范围安装，不将这些版本视为未来所有安装结果。

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

## 用户指定的 scholaread.com 官网补充核查

核查日期：2026-10-24。用户进一步明确官网为 <https://www.scholaread.com/>，因此补查该国际站的公开帮助文档和法律页面，并只读检查本项目受 Git 跟踪的源代码；不访问用户密钥或论文内容。

### 官网能够确认的内容

- 国际站[专业版购买协议](https://www.scholaread.com/help/purchase-agreement)将开发、运营及所有主体列为 **Astronet Technology PTE LTD**，协议标注更新及生效日期为 2024-11-12。这与上述 `.cn` 协议所列主体不同，不能仅凭品牌相同推定股权关系、专利归属或互相授权。
- 国际站[服务条款第 5 节](https://www.scholaread.com/help/terms)概括主张软件、内容、界面设计和布局框架等相关知识产权，包含版权、商标、专利和商业秘密；也限制复制、改编和逆向其软件。已读取的条款未列出可用于本次比对的具体专利号、权利要求或外观设计图。这类概括声明本身不能证明“所有双语阅读功能都由它独占”。条款对使用其服务的约束与法定知识产权保护范围须分别判断。
- 国际站[功能说明](https://www.scholaread.com/help/guides/getting-started/scholaread-integrated-pdf-reading-academic-translation-literature-management-and-note-taking)确实介绍逐段双语对照、双栏重排、PDF 阅读以及公式、图表等支持。本项目在功能层面存在重合，不能宣称完全没有参考用户提供的产品截图。

### 与当前项目的具体对照

| 项目 | 当前源码检查结果 | 判断边界 |
| --- | --- | --- |
| 品牌与外部素材 | 使用「纸间 Folio」；未发现 Scholaread / 靠岸品牌被嵌入生产界面，受跟踪文件中没有对方截图或论文 PDF。 | 不等于已做完商标清查或整个界面的实质性相似判断。 |
| 对方服务与实现 | 前端请求集中于同源 `/api`；翻译后端请求使用者配置的模型地址。未发现其接口、脚本、样式或资源域名被接入生产代码。 | 未取得对方源代码，不能完成两套代码的全面比对；字符串搜索也不能证明所有素材来源。 |
| 界面与功能 | 有独立名称、配色、译文块、外观面板、模型设置及布局控制；逐段上下对照、左目录和顶部工具栏仍有概念重合。 | 自行编程或换配色不能单独排除版权、GUI 外观设计或专利风险。 |
| 第三方组件 | 可识别的随附前端组件为 KaTeX 及字体，已保留 MIT / OFL 说明；PDF 引擎选择 AGPL 路径。 | 依赖许可合规不授予 Scholaread 或其他第三方的专利/品牌/内容权利。 |
| 开发来源证据 | 当前初始 Git 提交为 `da803a7`，可核对提交内容；需求中存在用户提供的参考截图。 | 单次初始提交不能单独证明提交前每段代码或图标的创作过程，不能将来源记录夸大成法律鉴定。 |

### 初步判断及开源影响

当前检查没有发现直接接入或分发对方程序、标识、截图或长宣传文案的明显证据，**但不作“已经确认不侵权”的结论**。相同功能或操作思想不自动等于软件著作权侵权：《计算机软件保护条例》第六条区分软件表达与开发思想、处理过程、操作方法、数学概念等；复制具体代码、文档或其他受保护表达则是另一问题。依据：[最高人民法院知识产权法庭公布的条例，第六条及第二十三至二十四条](https://ipc.court.gov.cn/zh-cn/news/view-407.html)。

本次以 Scholaread、Astronet Technology PTE LTD、上海亦答网络科技有限公司及专利相关词对官网、公开专利索引和官方入口作初步检索，仍未取得可靠对应本产品的完整有效专利文献。搜索中同名或近似名的其他 Astronet 通信类公司/专利，不能归为该产品的权利。没有检到不等于不存在；未完成申请人关联关系、受让/许可、专利家族、未公开申请及目标市场的全面调查。

专利判断须结合具体实施行为、适用地域、技术特征、权利要求/外观设计图和法律状态；《专利法》第十一条涉及生产经营目的的实施行为，第六十四条规定保护范围。**免费、开源或独立编写代码不是当然豁免，公开代码也不意味着当然构成某一项专利侵权。** 是否会被起诉不能由官网或代码扫描预测，能否胜诉也与是否被起诉不同。依据：[国家知识产权局公布的现行《专利法》](https://www.cnipa.gov.cn/art/2020/11/23/art_97_155167.html)。

结论：可以继续独立开发和保存当前私有仓库；保持私有只限制公开传播，并非侵权豁免。公开发布前，建议对具体有效专利及 GUI 外观设计进行专业检索和逐项比对，复核界面/素材来源，并保留持续开发记录。本次没有改变仓库可见性，没有联系对方或取得其授权，也没有对外发布用户论文或本项目源码。

访问范围限制：官网首页的交互式内容在本次浏览器读取中持续超时；已通过公开页面直接读取购买协议、服务条款和功能说明，但未登录其阅读器或完成完整逐屏视觉比对。工作区保存的取证页面仅作本机核查，不加入源码仓库，亦不将对方页面或图片作为本项目分发素材。

## 每次公开发布前

检查 Git 暂存区、提交历史和实际压缩包文件列表，运行测试及密钥扫描，确认没有用户配置、用户文献和个人路径。只在上述条件通过后将此记录作为有条件的发布依据；后续新增依赖、素材、云端功能或联网部署需要重新检查。
