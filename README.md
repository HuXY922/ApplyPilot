# ApplyPilot

**AI Job Search & Application Agent — 面向中国校招的可追溯求职工作台。**

> **虚构演示；匿名会话约24小时后过期，过期数据会清理，不用于真实长期求职记录。** 请仅使用虚构资料。

ApplyPilot 把经过用户确认的求职档案、真实岗位核验、资格检查、证据匹配、简历建议和投递记录连成一个可运行的 Web 产品。它保留 Career Desk 的 Python API 与 SQLite 状态机，独立运行，不依赖开发助手的对话或私人上下文。

> 当前交付状态（2026-10-10）：源码与本地 API 闭环已实现；[公开 GitHub 仓库](https://github.com/HuXY922/ApplyPilot)已于 2026-10-09 验证，首次发布的 99 个文件与已审核源码逐文件哈希一致。腾讯云公开可信 HTTPS、规则模式真实浏览器流程及已部署修复的回归已通过。所有者已安全配置 Key；已用虚构资料逐次授权完成 4 次真实 DeepSeek 调用，匹配返回 6 条有证据支持的优势，修复后的最终简历调用返回 1 条保留原文限定的完整段落重排建议，均未回退规则。TXT/PDF/DOCX 的真实浏览器导入闭环已通过。受控模拟投递表单的独立浏览器 E2E 仍待完成；未进行真实招聘投递。不要把规则模式或单元测试当作真实 AI/网申验收。

> 扩源实际状态（2026-10-10）：代码已部署，但长鑫属于**实验性来源，当前正式网页验收受阻**。缩页和最后的有限预算调整（全局 45 秒、长鑫每请求 12 秒）后，最终查询仍在来源初始化超时；字节也出现网络超时。旧岗位和人工记录保留，不补造结果。独立来源读取曾成功，不能替代正式网页稳定性验收；本次扩源可用性未完成。完整证据见[扩源报告](docs/OFFICIAL_SOURCE_EXPANSION.md)。

## 为什么做

招聘公告不等于可投递岗位，匹配分不等于资格，点击提交不等于投递成功。ApplyPilot 优先保留事实、来源、用户决定与不确定性，让求职者知道下一步为什么值得做。

## 核心功能

- **Candidate Profile**：编辑教育、经历、技能与偏好；导入 TXT/PDF/DOCX 文本为待审核草稿；确认后生成新版本，保留历史。
- **Job Radar**：从固定官方来源执行有界搜索；逐岗位核验身份、开放状态和 JD；保存来源、时间与观察记录；重复发现不会重置用户决定。
- **Job Match**：学历、届别、地点、岗位方向先做确定性 pass/fail/unknown 检查；匹配理由链接到已确认经历证据，不输出“录用概率”。
- **Resume Studio**：展示原文、建议、原因和证据；保留基础简历及岗位/档案版本。当前保守策略以选择、前置和重排已有完整段落为主，不自由编造改写。
- **Application Tracker**：收藏、跳过、准备、填写中、等待用户、提交未知、用户确认已提交、笔试、面试、Offer、拒绝；事件幂等，岗位有效性与求职阶段分开。
- **Apply Assistant（受控演示）**：独立模拟表单、Playwright 填写与 readback、内容绑定确认、取消与幂等模拟回执。不会提交真实招聘申请。

## 真实来源与限制

源码默认配置为字节跳动 + 实验性长鑫存储（APPLYPILOT_SOURCES=bytedance,cxmt）；已有部署须明确更新此配置后才启用新源。字节仍从官方详情及关联图谱发现岗位，长鑫使用官网明确链接的北森招聘站，关键词参与上游搜索，最多读取 2 页、每页 10 条（最多 20 条）。全局每轮最多 6 次请求 / 45 秒，按剩余时间公平分配且每源最多 30 秒；长鑫每请求硬上限 12 秒、字节 7 秒、可选 Ashby 12 秒，不自动重试。实际页数受剩余预算限制；这不是全网或全量搜索。

长鑫核验来自当前官方完整 JD 列表，逐条要求稳定 GUID、完整职责/要求与明确开放状态；不是每个详情页面都单独访问。具体核验方式、已读页数、本轮核验数、来源报告总量和停止原因会显示。仅 Status=1 为开放，Status=2 为失效，其余未知。缺少毕业届别时保持未知，不套用招聘首页的项目窗口。

长鑫收到用户输入的岗位关键词和所选招聘分类，字节仍本地过滤关键词，城市均在本地过滤；不会从档案或简历中自动提取信息发送给招聘来源。显式输入的关键词会发送至官方招聘网站，请勿填写个人资料、联系方式或凭据；常见联系方式会被自动拦截，但检测并非完全保证。网站接口未提供稳定开发者契约；访问失败、字段变化与限流会明确显示，不生成占位岗位，不因抓取失败把历史岗位全部关闭。401/403 后该进程暂停该源，需服务端复核；429 遵守完整 Retry-After，未提供时至少暂停 60 秒。当前单 worker 的线程共享暂停状态，重启会清空，运维不得用重启绕过访问限制。

可选 Ashby 官方公开职位 Feed，必须显式配置雇主 board。全球职位不自动被当作中国校招职位。来源核验和候选人资格始终分开。参见 [来源说明](docs/SOURCE_PROVENANCE.md)。

## 快速启动

Python 3.10+、SQLite 3.38+。推荐 Python 3.12。Linux/macOS 为完整支持路径；保留的旧生产模块依赖 Unix `fcntl`，Windows 可使用 WSL 或 Docker。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m applypilot_core.server --host 127.0.0.1 --port 8787 --data-dir ./local-data
```

浏览器打开 http://127.0.0.1:8787 。无需生产账户或旧服务，首次访问创建隔离的虚构演示档案和三个明确标注的示例岗位。先审核并确认档案，再运行分析/简历建议。

匿名演示会话默认从创建起约 24 小时后过期；同一浏览器 Cookie 在期限内可继续访问。刷新及服务重启不会清空期限内已保存的数据，也不会延长会话期限。过期后无法继续访问该会话；服务在下一次创建会话时清理已过期会话的 SQLite 数据文件。它不是永久账户，不用于真实长期求职记录。只上传虚构资料。

## 模型配置

本次部署选择 **DeepSeek**；已实现 DeepSeek Chat Completions 适配器，并保留可选 OpenAI Responses 兼容路径。服务端配置与 `.env.example` 一致：

```bash
export APPLYPILOT_MODEL_PROVIDER=deepseek
export APPLYPILOT_DEEPSEEK_MODEL=deepseek-flash
# 由所有者在安全的服务端环境配置 APPLYPILOT_DEEPSEEK_API_KEY。
# 不要把 Key 发到聊天、前端表单或提交到 GitHub。
```

DeepSeek 固定请求 `https://api.deepseek.com/chat/completions`，使用非思考、非流式 JSON 输出。当前适配器允许 `deepseek-flash` 和 `deepseek-v4-pro`，部署模板选择前者。模型名与接口参数已于 2026-10-09 对照 [DeepSeek 官方 API 文档](https://api-docs.deepseek.com/api/create-chat-completion/)核对。2026-10-10 已完成 4 次经逐次授权的真实 DeepSeek 认证调用，均使用虚构资料：1 次匹配返回 6 条有证据支持的优势，前 2 次简历调用返回结构有效的空建议列表（0 条）；提示修复后，08:54:15 UTC 的最终简历调用返回 1 条有证据支持的完整段落重排建议，保留虚构、非生产及无增长成果等限定，未回退规则。空列表本身不算效果验收；最终非空建议的保守重排与证据对应已单独核验。验收后未继续调用。未审计账户余额，也不保证后续调用的权限或额度。

缺少所选提供方的 Key、模型配置无效或提供方不受支持时，明确运行规则模式。配置齐全只显示 `configured_not_live_verified`，不代表真实模型验收通过。DeepSeek 只读取 `APPLYPILOT_DEEPSEEK_API_KEY`，绝不回退使用 OpenAI 或通用 Key，也不会在调用失败后自动改用其他提供方。

每次分析/简历建议前，需要用户同意将最少的已确认经历、项目、技能证据及岗位标题/JD/要求发送给**当前提供方**；简历建议只发送经历/项目证据。请求中的 `model_consent` 必须与当前配置相同（DeepSeek 为 `deepseek`，OpenAI 为 `openai`），提供方变更后须重新确认。不会直接发送姓名、联系方式字段或完整简历文件；文本中的常见联系方式会尽力移除，但这不是完全匿名化保证，公开演示仍只允许虚构资料。

JSON 模式不等于证据正确或 schema 合规。两条路径都执行本地结构与证据校验，模型不能改变资格、权限或提交状态。成功模型结果保留请求所用的提供方和模型标记；本次请求模型为 `deepseek-flash`。该标记是请求参数，不是供应商实际解析到的模型版本 ID；当前未保存供应商返回的实际解析模型 ID，不能据此声称已核验具体底层版本。模型调用失败时返回明确标注的规则降级结果，运行记为 `partial`；默认每日全局最多 20 次模型请求、每会话 5 次，失败请求也计入预算。调用次数限额不是账单金额保证；请结合 [DeepSeek 当前价格](https://api-docs.deepseek.com/quick_start/pricing/)和账号预算核实成本。

如明确选择 OpenAI，设置 `APPLYPILOT_MODEL_PROVIDER=openai`、`APPLYPILOT_OPENAI_MODEL` 和 `APPLYPILOT_OPENAI_API_KEY`。仅在 OpenAI 路径中保留旧 `OPENAI_MODEL` / `OPENAI_API_KEY` 变量兼容；未设置提供方变量的旧环境仍默认 OpenAI。不能把 DeepSeek 的 Key 填入 OpenAI 配置。Claude 尚不支持。

## 架构

```text
Web UI → Service（受控任务编排）
          ├─ Runtime（搜索/官方核验/规范化/资格规则）
          ├─ Model Adapter（最小数据、结构与证据校验）
          └─ Store → 原 Career Desk 核心表 + 增量 ap_* 表 → SQLite
```

- 原有 StateAPI/审批/执行状态机保留；公开 Demo 不暴露旧 `/v1`、`/radar` 或角色密钥。
- 每个匿名访客有独立 SQLite 文件，服务器端会话、CSRF、精确 Origin 和有界数据配额。
- 手动求职进度记录使用增量表，关联原 applications；不会把手动“已提交”伪造成浏览器执行成功。
- 无额外向量数据库、RAG 服务或多 Agent 集群。

目录：`applypilot_core/` 新增服务与运行时，`applypilot_web/` Web UI，`migrations/005_applypilot.sql` 增量模型，原顶层 Python 模块与 `tests/` 保留兼容性。

## 测试

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s tests_applypilot -v
python scripts/smoke_flow.py
# 额外执行真实官方来源读取，不会投递：
python scripts/smoke_flow.py --live
# 独立中国官方关键词/分页读取，不用模型、不投递：
python scripts/smoke_official_sources.py --source cxmt --query AI
node --check applypilot_web/app.js
```

受控浏览器演示（需要独立安装开发依赖与 Chromium）：

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python scripts/demo_fill.py
```

默认只填表并校验，停止在最终确认前。`--submit --confirm-local-simulation` 仅允许自有 loopback 模拟页面，绝不访问外部招聘站。该独立受控模拟表单仍需另行完成真实浏览器 E2E；已验收的主工作台流程不替代这项测试，参见 [测试报告](docs/TEST_REPORT.md)。

## 部署与交付

使用 `Dockerfile.applypilot` 和 `railway.applypilot.toml`，不要误选保留的旧 Career Desk 配置。SQLite 采用单实例和独立 `/data` 持久化卷，不复用旧生产卷。Railway 只是可选托管方案，本项目不依赖其私有功能。

2026-10-09 发布检查：[HuXY922/ApplyPilot](https://github.com/HuXY922/ApplyPilot) 已公开，首次远端提交为 [`df51cc9499de1a6bf4092c0e65f280c857625087`](https://github.com/HuXY922/ApplyPilot/commit/df51cc9499de1a6bf4092c0e65f280c857625087)。未登录的 Git 克隆成功，99 个文件的路径和 SHA-256 均与已审核本地提交 `9685da3b4af72452d0bb53269cc7165c3f91fbaf` 一致，无缺失、额外文件或内容差异。该记录验证源码发布；后续 Web 部署验收状态见下文。

Railway 创建独立项目时返回试用已到期、需选择套餐的错误；未创建新项目，也未获准升级付费套餐。后续已改用腾讯云：截至 2026-10-10，公开可信 HTTPS、规则模式真实浏览器流程、已部署修复回归及真实 DeepSeek 认证调用已验证；验收范围和待完成项如下。参见 [腾讯云部署说明](docs/tencent-deployment.md)；[早期部署记录](docs/DEPLOYMENT.md#current-delivery-status-checked-2026-10-09)保留此前的 Railway 阻塞背景。

### 已部署 P0 验收（2026-10-10 08:54 UTC）

- **P0 代码与线上部署**：公网 IP 的可信 HTTPS 已验证。08:54 验收所对应的修复部署后，服务端通过 139 项旧测试（5.873 秒）及 161 项 ApplyPilot 测试（5.913 秒）；本地同样通过 139 + 161 项 Python 测试、132 项 DOM/API 模拟检查及真实 loopback HTTP/SQLite smoke。DOM 模拟不等于浏览器 E2E。
- **独立的真实浏览器验收**：规则模式搜索、档案、匹配、追踪与持久化，以及证据展示和无变化保存回归通过。TXT/PDF/DOCX 均完成选择、提取、载入编辑、核对确认与保存；文件名跨导航保留，新来源清除旧草稿，取消不丢失已有编辑，载入后聚焦首个字段。来源字段继续保守提取；PDF 不含 OCR。
- **真实模型验收**：总计 4 次 DeepSeek 调用（1 次匹配、3 次简历），全部使用虚构资料并逐次授权。匹配得到 6 条有据优势；前 2 次简历为空，最终修复后的调用得到 1 条有用的保守段落重排建议，证据指向 `projects/0/description`，原文限定未丢失，最终结果无规则回退。请求模型名为 `deepseek-flash`，供应商实际解析模型 ID 未保存。
- **仍未覆盖**：独立受控模拟投递表单的真实浏览器 E2E 为后续 P1 项；未执行真实招聘投递。本次验收是指定虚构案例和已列流程的通过，不代表任意简历、岗位或设备的效果保证。
- 匿名演示会话约 24 小时后过期，过期数据会清理，不用于真实长期求职记录。完整范围及历史记录见 [测试报告](docs/TEST_REPORT.md)。

后续扩源与时限/缩页修正另有 139 + 214 项 Python 测试、152 项 DOM/95 次 WSGI 检查及服务器验证；正式来源请求曾超时，最新线上可用性以[扩源报告](docs/OFFICIAL_SOURCE_EXPANSION.md)为准。

- [部署说明](docs/DEPLOYMENT.md)
- [API 与数据模型](docs/API_AND_DATA.md)
- [演示操作](docs/DEMO_GUIDE.md)
- [测试与未完成验收](docs/TEST_REPORT.md)
- [三天计划与 Roadmap](docs/ROADMAP.md)
- [后端审计](docs/audit/backend.md) / [UI 与 Agent 审计](docs/audit/ui-agent.md)
- [依赖许可证](docs/THIRD_PARTY_NOTICES.md)

## 已知限制

1. 新版本默认覆盖两个有界官方源：字节详情关联图 + 长鑫关键词分页，不能代表全市场岗位；线上启用状态以部署记录为准。
2. 正则资格判断采用保守 unknown；复杂专业、经验、毕业窗口需要用户核实。
3. PDF 仅提取已有文本，不包含 OCR；加密、过大或格式异常文件会拒绝。
4. 简历建议为证据约束的保守提炼/重排，尚不等于完整简历编辑器。
5. 暂无生产多用户账户、永久资料保管、自动定时搜索或真实招聘站提交。
6. 演示会话数量、保存期限、历史条数和存储受限；网络/模型预算耗尽时会明确停止。
7. 浏览器、模型、部署、实际网络与 Mock 测试分别记录，未通过项目不会标作已完成。

## 许可证

原项目由项目所有者提供，未附项目级开源许可证。公开可读不自动授予再分发许可；确定项目许可证后再邀请开源复用。第三方依赖保留各自许可证。岗位内容归原权利人所有。
