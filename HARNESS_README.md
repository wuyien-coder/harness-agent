# Agent Harness（照抄原项目 + 在原项目基础上增 harness）

本目录是 `简历项目_学位论文知识问答_主流架构版` 的**完整副本**，在其之上新增了一层 agent harness。
原项目本体（`D:/找工作文件夹/简历项目_学位论文知识问答_主流架构版/`）**零改动**。

## 0. 框架定位与编排范式（面试速记）

**它不是套用现成的 Agent 框架**——LangGraph / AutoGen / CrewAI 均未使用；`langgraph` 只出现在原项目 `pyproject.toml` 的 `prod` 可选依赖里，harness 代码里一次都没用到。它是**用 Python 标准库自研的轻量 Agent Harness 编排层**：`contracts.py` 用 `dataclass` / `Enum` 定义契约、刻意不 import 原项目任何类型；LLM 调用走自己写的 `OpenAICompatClient`（OpenAI 兼容 SSE，不绑定厂商 SDK）。

编排范式是 **Plan-and-Execute（先规划后执行）**，与底层问答链路的 **ReAct** 是两层嵌套关系，不是二选一：

| 维度 | Plan-and-Execute（harness 编排层） | ReAct（底层 ChatService） |
|---|---|---|
| 决策时机 | 一次性规划全部任务，执行中不变 | 每轮根据观察重新决定下一步 |
| 执行顺序 | 拓扑排序后串行，可预判 | 模型动态决定，灵活但不可预判 |
| 可审计性 | 强：计划固定、每步 trace、可重放 | 弱：轨迹是事后记录的结果 |
| 抗错性 | 依赖失败则下游 `skipped`，不随机应变 | 观察异常可当场换工具重试 |
| 成本/延迟 | 规划 1 次，成本可控 | 每轮都要一次 LLM 推理，轮数不可控 |

- 面板上两条轨迹分开展示：harness 的 `调度轨迹 plan→dispatch→observe→finish` 和底层问答的 `ReAct 工具调用 N 步`。讲的时候别把 ReAct 的能力挂到 harness 头上。
- **为什么这么选**：任务可预先拆解（检索 / 图谱 / 问答三类能力固定），把可审计、可重放、预算可控放在灵活性前面；需要灵活推理的部分下沉给 ReAct。
- **各自缺陷与对策**：Plan-and-Execute 怕规划错（第一步拆错后面全废）→ 规划器可替换（规则 / LLM）；ReAct 怕绕圈（轮数与成本失控）→ 预算闸门（`max_steps` / `max_tool_calls` / `timeout_ms`）。

## 1. 设计目标

- **照抄原项目**：`src/mathkb/`、`.env`、配置、数据、索引、前端、脚本等按原样复制（排除 `.venv`、`.runtime`、`node_modules`、`__pycache__` 等可再生/缓存目录）。
- **在原项目基础增 harness**：新增 `harness/` 包作为编排层，直接调用原项目的真实能力（检索、图谱、问答），而不是另起 HTTP 服务或造合成数据。
- **明确边界**：agent 输入输出、任务调度、工具/知识库调用边界全部由端口（Port）契约约束，可替换、可测试。
- **隔离**：harness 代码全部在新增目录；原项目源码/配置/数据/运行方式不改；运行期的密钥与环境覆盖只写入当前进程环境变量，不落盘。

## 2. 目录结构

```
agent-harness-project/                ← 副本根目录（照抄原项目）
├── src/mathkb/                       ← 原项目源码（原样复制，未改）
├── .env / .index/ / data/ / frontend/ / docs/ / scripts/ / tests/  ← 原项目配置与数据（原样复制）
├── harness/                          ← 【新增】agent harness 编排层
│   ├── contracts.py                  ← 输入输出契约：AgentRequest / AgentResponse / TaskSpec / PlanDecision ...
│   ├── ports.py                      ← 四类边界：Tool / KnowledgeBase / Planner / Responder
│   ├── registry.py                   ← 工具注册表：发现、白名单、参数校验、异常隔离
│   ├── scheduler.py                  ← 任务调度：拓扑排序、依赖短路、有限重试、预算
│   ├── loop.py                       ← 主循环：plan → dispatch → observe → finish（全步 trace）
│   ├── tools.py                      ← 把知识库能力投影为 6 个只读工具（kb_search/kb_answer/...）
│   ├── native_kb.py                  ← 【核心】直连原项目 AppContext/ChatService/GraphStore 的适配器
│   ├── bootstrap.py                  ← 运行期装配：进程内环境变量覆盖，装配 mathkb 运行时
│   ├── seed_db.py                    ← 在副本内重建 harness 专用 SQLite 库（不碰原索引）
│   ├── runner.py                     ← 顶层入口 + CLI（native 为默认模式）
│   ├── storage.py                    ← 默认不落盘；落盘仅限本副本目录
│   ├── adapters/                     ← 可替换适配器：LLM 规划/回答、规则规划、离线/HTTP KB（降级）
│   └── panel/                        ← 本地问答交互面板（server.py + index.html）
├── tests_harness/                    ← 【新增】harness 测试（mock，无真实网络）
├── HARNESS_README.md                 ← 本文档
└── .index/harness.db                 ← 【新增】harness 专用 SQLite 索引（原 mathkb.db 保留只读）
```

## 3. 输入输出（contracts.py）

**输入 `AgentRequest`**
- `goal`：任务目标（必填，≤1000 字）
- `constraints`：约束列表
- `history`：历史对话 `[{"role":..., "content":...}]`
- `tool_allowlist`：工具白名单（`None` = 不额外限制）
- 预算：`max_tasks`(≤32) / `max_tool_calls`(≤64) / `max_steps`(≤12) / `timeout_ms`(≤120000)

**输出 `AgentResponse`**
- `status`：`completed` / `partial` / `failed` / `budget_exhausted`
- `answer`：最终回答
- `evidence[]`：`Evidence(doc_id, text, score, source, metadata)` 可引用证据
- `tasks[]`：每个调度任务的状态、尝试次数、产出、耗时
- `trace[]`：每一步 plan/dispatch/observe/finish 的审计记录
- `tool_calls_count` / `errors[]` / `timings_ms` / `usage`
- `to_dict()` / `to_json()` 全字段可 JSON 序列化

## 4. 任务调度（scheduler.py）

- 规划器产出 `dispatch / finish / abort` 三类决策；
- 调度器先做拓扑排序（检测重复 id、未知依赖、自依赖、环），再按序执行；
- 依赖失败的下游直接 `skipped`；单任务最多重试 3 次；
- 时间 / 工具调用数 / 任务数任一触顶即跳过剩余任务，状态标记 `budget_exhausted`；
- 刻意串行（不做并发），保证可重放、可审计。

## 5. 工具与知识库边界

`KnowledgeBasePort` 声明能力集合 `capabilities`，harness 据此注册工具；所有调用必经 `ToolRegistry`（Schema 校验 + allowlist + 异常降级为观察结果）。

| 工具 | 原项目真实能力 |
|---|---|
| `kb_search` | `AppContext.retriever.retrieve`（BGE/哈希向量 + BM25 + RRF 融合） |
| `kb_answer` | `ChatService.ask`（原项目完整问答链路，含 ReAct、引用、成本统计） |
| `kb_profile` | `GraphStore.profile_text` |
| `kb_neighbors` | `GraphStore.graph` 有向边（teacher/student/advisor，区分方向） |
| `kb_path` | `GraphStore.path`（无向最短路径） |
| `kb_find_people` | `GraphStore.by_field` / `by_hometown` |

`NativeKnowledgeBase` 只读：只调用查询方法，不调用 ingest/seed/delete。问答会话落库到 harness 专用库 `.index/harness.db`，不写原项目自带的 `.index/mathkb.db`。

## 6. 与原项目的隔离方式

1. **目录隔离**：所有新增文件都在 `harness/`、`tests_harness/`、`HARNESS_README.md` 及新增的 `.index/harness.db`；`src/mathkb/` 与其余原文件逐字节未改。
2. **运行时隔离**：`bootstrap.apply_offline_env()` 只写 `os.environ`（进程内），不修改 `.env`；mathkb 的 `Settings` 从副本自己的 `.env` 读取，`ROOT` 指向副本自身。**关键加固**：副本 `.env` 里写死了若干指向原项目的绝对路径（`MATHKB_INDEX_DIR`、`MATHKB_KEYWORD__PERSIST_DIR`、`MATHKB_DATA_DIR`、`MATHKB_BIOGRAPHIES_DIR`、`MATHKB_EMBEDDING__*CACHE_DIR`、`MATHKB_VECTOR__PERSIST_DIR`），`apply_offline_env()` 会把它们逐一覆盖为副本内路径，防止 `BM25Store` / `EmbeddingCache` 在 seed 时把缓存写回原项目。`tests_harness/test_boundaries.py` 有专门用例守护这条规则。
3. **数据隔离**：harness 只读原索引的拷贝；写操作（会话记录）落到新库 `harness.db`；原库 `mathkb.db` 全程只读。
4. **密钥隔离**：API key 仅从环境变量 `MATHKB_LLM__API_KEY` 或面板临时输入读取，仅存服务端进程内存；不写入任何文件、不进日志/trace、响应中脱敏。
5. **可回退**：不启用大模型时，`LLM` 开关关闭即走零成本规则规划 + 摘录回答；harness 支持 `offline`（合成语料）与 `http`（外部服务）降级模式，均不影响原项目。

## 7. 运行方式

```bash
# 从副本根目录
cd D:/找工作文件夹/agent-harness-project

# 1) 重建 harness 专用库（可选，已建好；仅在数据变更时重跑）
PYTHONPATH=src:. D:/code/python.exe -m harness.seed_db

# 2) CLI（native = 直连原项目真实能力，默认离线 stub，零费用）
PYTHONPATH=src:. D:/code/python.exe -m harness.runner --goal "陈景润的老师是谁？" --mode native

# 3) CLI + 真实大模型（密钥从环境变量注入，不落盘）
MATHKB_LLM__API_KEY=sk-xxx MATHKB_LLM__MODEL=deepseek-chat \
PYTHONPATH=src:. D:/code/python.exe -m harness.runner --goal "..." --mode native

# 4) 交互面板（默认 127.0.0.1:8765，默认 native 模式）
PYTHONPATH=src:. D:/code/python.exe -m harness.panel.server --port 8765
# 浏览器打开 http://127.0.0.1:8765 ，左侧可切换 native / 离线 / HTTP，勾选工具白名单，查看调度轨迹
# 问答默认走 POST /api/stream（SSE 流式）；原 POST /api/ask 同步接口保留兼容
# 若要接真实大模型：启动时注入环境变量（仅进程内，不落盘）
MATHKB_LLM__API_KEY=sk-xxx PYTHONPATH=src:. \
  D:/code/python.exe -m harness.panel.server --port 8765 --llm-enabled --llm-model deepseek-chat

# 5) 测试（mock，无真实网络；不影响原项目 tests/）
PYTHONPATH=src:. D:/code/python.exe -m pytest tests_harness -q
```

## 8. 引用与响应呈现（借鉴原交互面板）

面板 `harness/panel/index.html` 的**呈现层**借鉴了原项目 React 面板（`frontend/src/App.tsx`）的引用/响应设计，但用纯静态 HTML/CSS/JS 实现，无 React、无外网 CDN 依赖，仍只绑定 127.0.0.1：

- **响应**：Markdown 轻量渲染（标题/列表/代码块/引用/加粗）+ 消息标签「Harness · 基于知识库回答 · 引擎」+ 操作条「N 条参考依据 ↗ / 引擎 / 检索强度 / 耗时」；回答中的 `[n]` 引用标记可点击，滚动并高亮右侧对应引用卡片。有序列表项之间夹空行时保持同一 `<ol>`，编号连续（不会出现每段都从 1 开始）。
- **状态语义**：预算/截止时间导致的任务跳过只记录在 `tasks[].status=skipped`，不再混入 `errors`（`errors` 只表示真正的执行失败）；前端以中性标签「N 项任务按预算跳过」提示，不再把正常调度标红。预算用尽后主循环直接收尾，不再调用规划器产生注定跳过的任务；规划器 `abort`/异常时若已有证据，回答阶段仍会基于证据产出答案（状态 `partial`），不会返回空白回答。
- **参考依据栏**：右侧独立侧栏，含 `参考依据 NN` 计数、置信度中文（较低/中等/较高）、citation 卡片（序号 / 人物 / 图谱·原文徽标 / 摘录 / 章节页码 / 来源）、ReAct 工具轨迹中文化（工具名与参数名中文映射，兼容原项目 `text_search` 等与 harness `kb_*` 两套命名）、timings 与索引统计。图谱/原文按 `chunk_id` 前缀（`graph:` / `react-graph:`）判定，与原面板一致。
- **后端归一化**：`harness/panel/server.py` 的 `extract_presentation()` 把 `kb_answer` 证据 metadata 里嵌的 `citations / confidence / agent_trace / agent_stop / engine / cost_yuan` 提到响应顶层（原字段全部保留，向后兼容）；非 native 模式返回空结构，前端自动降级。
- 保留 harness 独有的「调度轨迹」（plan → dispatch → observe → finish），与原面板 ReAct 轨迹分开展示，互不覆盖。
- **交互增强（借鉴原面板）**：① 停止生成——发送中显示「停止生成」按钮，`AbortController` 中断 SSE；停止后标注「已停止生成」并给「重新提问」入口，不把半截输出伪装成有效答案（后端流式端点本就静默处理断连 `BrokenPipe`，前端中断安全）。② 中文输入法保护——`Enter` 发送时跳过 `isComposing`/`keyCode 229`，避免确认候选词被误提交。③ 空输入禁用发送按钮，输入后自动启用。④ 点击任意历史回答（或「N 条参考依据 ↗」）时，右侧参考依据栏切换到那一条；新回答自动选中，多轮问答可逐条回看引用。⑤ 顶部通知条（成功/错误）；⑥ 回答操作条增加真实成本标签 `¥x.xxxx`（`cost_yuan>0` 时）；⑦ 欢迎页预设问题带序号标签与免责声明、流式占位改三点动画。全部纯静态实现，无 React / 无外网 CDN。
- 守护测试：`tests_harness/test_panel_presentation.py`（归一化字段 + 前端呈现元素 + 交互接线静态扫描 + 无外网依赖）+ `test_markdown_render.js`（Markdown 序号回归）。

## 9. 流式响应

- **端点**：`POST /api/stream`，请求体与 `/api/ask` 完全一致；返回 `text/event-stream`，事件类型：
  - `stage` — 处理阶段（规划 / 调用工具 / 检索并生成 / 回答完成），前端显示为状态条；
  - `token` — 模型输出的真实文本增量，逐字追加到回答区；
  - `done` — 完整 `AgentResponse` + 归一化呈现字段（`citations` / `confidence` / `agent_trace` 等），前端据此替换成最终带引用的卡片；
  - `error` — 只带脱敏后的简短错误文本。
- **真实增量来源**：native 模式复用原项目 `ChatService.stream()`，底层是 OpenAI 兼容 provider 的 `stream_events()`，吐出上游真实 token；harness 自己的 `LLMResponder` 也支持 `OpenAICompatClient.stream_chat()` 的 SSE 增量。注入 API key 时 `bootstrap` 会把原项目 `MATHKB_LLM__PROVIDER` 一并切到 `openai`（并随配置变化重建 runtime），避免"面板显示接了大模型、原生问答仍是 stub"的错配。离线/无流式能力时退化为在 `done` 一次给出完整答案，不会返回空白。
- **隔离**：流式只靠 `harness/stream_events.py` 的 `ContextVar` sink 在运行期绑定，不改 `AgentRequest` / `ToolResult` 等端口契约，也不改原项目代码；无 sink 时 `emit()` 是空操作，CLI 与既有测试行为不变。密钥仅驻留服务端进程内存，异常文本统一脱敏。
- **守护**：`tests_harness/test_stream_events.py`、`test_llm.py` 的 SSE 增量用例、`test_native_kb.py` 的 token 转发用例、`test_panel_presentation.py` 的 SSE 帧与前端流式接线静态校验（含停止生成/输入法保护/消息切换引用/重问/空输入禁用/预设问题接线）。当前 harness 测试共 71 项全绿。

## 10. 非目标

- 不改原项目的任何代码、配置、数据、前端或启动脚本；
- 不把 harness 部署为公共服务（面板仅绑定 127.0.0.1）；
- 不复制受限语料的版权内容之外的新数据（全部来自副本自带的 data/）。
