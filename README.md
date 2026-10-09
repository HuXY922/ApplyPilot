# ApplyPilot

**AI Job Search & Application Agent — 面向中国校招的可追溯求职工作台。**

ApplyPilot 把经过用户确认的求职档案、真实岗位核验、资格检查、证据匹配、简历建议和投递记录连成一个可运行的 Web 产品。它保留 Career Desk 的 Python API 与 SQLite 状态机，独立运行，不依赖开发助手的对话或私人上下文。

> 当前交付状态：源码与本地 API 闭环已实现；[公开 GitHub 仓库](https://github.com/HuXY922/ApplyPilot)已于 2026-10-09 验证，首次发布的 99 个文件与已审核源码逐文件哈希一致。公开 Web 托管、真实模型调用和完整浏览器 E2E 仍需分别验证。不要把规则模式或单元测试当作真实 AI/网申验收。

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

默认接入字节跳动官方岗位详情及关联岗位图谱，覆盖范围有限，不是全网搜索。关键词和城市在本地过滤，不将求职者资料发送给招聘来源。网站接口未提供稳定开发者契约，访问失败、字段变化与限流会明确显示。

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

演示会话默认保留 24 小时；同一浏览器 Cookie 在期限内可继续访问，刷新及服务重启不会清空已保存数据。它不是永久账户。只上传虚构资料。

## 模型配置

本次部署选择 **DeepSeek**；已实现 DeepSeek Chat Completions 适配器，并保留可选 OpenAI Responses 兼容路径。服务端配置与 `.env.example` 一致：

```bash
export APPLYPILOT_MODEL_PROVIDER=deepseek
export APPLYPILOT_DEEPSEEK_MODEL=deepseek-flash
# 由所有者在安全的服务端环境配置 APPLYPILOT_DEEPSEEK_API_KEY。
# 不要把 Key 发到聊天、前端表单或提交到 GitHub。
```

DeepSeek 固定请求 `https://api.deepseek.com/chat/completions`，使用非思考、非流式 JSON 输出。当前适配器允许 `deepseek-flash` 和 `deepseek-v4-pro`，部署模板选择前者。模型名与接口参数已于 2026-10-09 对照 [DeepSeek 官方 API 文档](https://api-docs.deepseek.com/api/create-chat-completion/)核对；账号是否有权限、余额是否充足及真实认证调用均未验证。

缺少所选提供方的 Key、模型配置无效或提供方不受支持时，明确运行规则模式。配置齐全只显示 `configured_not_live_verified`，不代表真实模型验收通过。DeepSeek 只读取 `APPLYPILOT_DEEPSEEK_API_KEY`，绝不回退使用 OpenAI 或通用 Key，也不会在调用失败后自动改用其他提供方。

每次分析/简历建议前，需要用户同意将最少的已确认经历、项目、技能证据及岗位标题/JD/要求发送给**当前提供方**；简历建议只发送经历/项目证据。请求中的 `model_consent` 必须与当前配置相同（DeepSeek 为 `deepseek`，OpenAI 为 `openai`），提供方变更后须重新确认。不会直接发送姓名、联系方式字段或完整简历文件；文本中的常见联系方式会尽力移除，但这不是完全匿名化保证，公开演示仍只允许虚构资料。

JSON 模式不等于证据正确或 schema 合规。两条路径都执行本地结构与证据校验，模型不能改变资格、权限或提交状态。成功模型结果保留实际请求的提供方和模型标记。模型调用失败时返回明确标注的规则降级结果，运行记为 `partial`；默认每日全局最多 20 次模型请求、每会话 5 次，失败请求也计入预算。调用次数限额不是账单金额保证；请结合 [DeepSeek 当前价格](https://api-docs.deepseek.com/quick_start/pricing/)和账号预算核实成本。

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
node --check applypilot_web/app.js
```

受控浏览器演示（需要独立安装开发依赖与 Chromium）：

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python scripts/demo_fill.py
```

默认只填表并校验，停止在最终确认前。`--submit --confirm-local-simulation` 仅允许自有 loopback 模拟页面，绝不访问外部招聘站。必须另行验证真实浏览器执行，参见 [测试报告](docs/TEST_REPORT.md)。

## 部署与交付

使用 `Dockerfile.applypilot` 和 `railway.applypilot.toml`，不要误选保留的旧 Career Desk 配置。SQLite 采用单实例和独立 `/data` 持久化卷，不复用旧生产卷。Railway 只是可选托管方案，本项目不依赖其私有功能。

2026-10-09 发布检查：[HuXY922/ApplyPilot](https://github.com/HuXY922/ApplyPilot) 已公开，首次远端提交为 [`df51cc9499de1a6bf4092c0e65f280c857625087`](https://github.com/HuXY922/ApplyPilot/commit/df51cc9499de1a6bf4092c0e65f280c857625087)。未登录的 Git 克隆成功，99 个文件的路径和 SHA-256 均与已审核本地提交 `9685da3b4af72452d0bb53269cc7165c3f91fbaf` 一致，无缺失、额外文件或内容差异。该记录验证源码发布，不代表 Web Demo 已部署。

Railway 创建独立项目时返回试用已到期、需选择套餐的错误；未创建新项目，也未获准升级付费套餐。公开 Web Demo URL、已部署实例出网及真实认证模型调用均未验收，详见 [部署状态与阻塞](docs/DEPLOYMENT.md#current-delivery-status-checked-2026-10-09)。

- [部署说明](docs/DEPLOYMENT.md)
- [API 与数据模型](docs/API_AND_DATA.md)
- [演示操作](docs/DEMO_GUIDE.md)
- [测试与未完成验收](docs/TEST_REPORT.md)
- [三天计划与 Roadmap](docs/ROADMAP.md)
- [后端审计](docs/audit/backend.md) / [UI 与 Agent 审计](docs/audit/ui-agent.md)
- [依赖许可证](docs/THIRD_PARTY_NOTICES.md)

## 已知限制

1. 默认来源覆盖单一雇主的有限关联图谱，不能代表全市场岗位。
2. 正则资格判断采用保守 unknown；复杂专业、经验、毕业窗口需要用户核实。
3. PDF 仅提取已有文本，不包含 OCR；加密、过大或格式异常文件会拒绝。
4. 简历建议为证据约束的保守提炼/重排，尚不等于完整简历编辑器。
5. 暂无生产多用户账户、永久资料保管、自动定时搜索或真实招聘站提交。
6. 演示会话数量、保存期限、历史条数和存储受限；网络/模型预算耗尽时会明确停止。
7. 浏览器、模型、部署、实际网络与 Mock 测试分别记录，未通过项目不会标作已完成。

## 许可证

原项目由项目所有者提供，未附项目级开源许可证。公开可读不自动授予再分发许可；确定项目许可证后再邀请开源复用。第三方依赖保留各自许可证。岗位内容归原权利人所有。
