# MathKnow · 领域知识库 GraphRAG 问答平台

将学位论文重构为可运行的 AI 工程项目。完整版本已接通 **React / TypeScript → FastAPI / LangGraph → PostgreSQL / pgvector → Redis / Celery → 真实模型接口**，使用全量语料与真实 BGE 编码。

## 直接使用

当前电脑的运行环境、数据库、模型缓存和前端已准备好。

1. 双击 **启动完整版.cmd**，等待组件检查通过，浏览器会打开 http://127.0.0.1:8001/web/ 。启动演示.cmd 也指向完整版本。
2. 提问“华罗庚在数论方面有哪些贡献？”或“苏步青师从谁？”，查看流式回答和右侧证据；历史会话可回看、删除。
3. 在“知识库”上传 TXT、Markdown 或可提取文本的 PDF，Celery 完成任务后即可检索；支持失败重试与文档索引删除。扫描 PDF 会提示需先 OCR。
4. 双击运行状态.cmd 查看进程；用停止完整版.cmd 停止新版及其专用数据库、Redis。服务在后台运行，关闭启动窗口不会停止服务。

原项目 8000 端口独立保留。启动离线样例.cmd 在 8002 运行 SQLite / hash / stub 样例。

真实问答会将检索片段发往本地配置的模型服务，并可能产生服务商费用。密钥保存在被 Git 忽略的 .runtime/private.env；页面连接设置只接受应用访问令牌，当前本机留空即可。

## ReAct Agent（v2.2）

默认真实模型问答已切换为 ReAct：模型决定工具与参数 → 执行只读工具 → 观察结果 → 再次决策，直到 finish 或达到调用上限。循环由 LangGraph 实现，使用 JSON Action/Observation 协议，最终答案继续通过真实 SSE 输出。

工具：text_search、person_profile、graph_neighbors、graph_path、find_people。默认最多 4 次工具调用、12 条证据；重复调用和非法参数会作为错误观察返回模型，避免无限循环。页面显示工具名、参数与状态，不请求或展示模型内部思维链。工具轨迹保存在会话中，刷新后可回看。

配置：MATHKB_AGENT__MAX_STEPS（1～8）、MATHKB_AGENT__MAX_EVIDENCE（1～20）、MATHKB_AGENT__MAX_OBSERVATION_CHARS。使用 mode=agent、engine=auto/ react；旧 langgraph / sequential 工作流保留作对照，mode=rag 为基础检索。离线 stub 模式保留规则工作流，不假装运行模型决策。

详见 docs/ReAct机制.md；新验收证据为 reports/react_validation.json、reports/browser_react_validation.json、reports/tests-react.xml。

## 实际架构

| 模块 | 完整版本实际实现 |
| --- | --- |
| 前端 | React 19、TypeScript、Vite；SSE、引用面板、Markdown / KaTeX、响应式页面 |
| API / 会话 | FastAPI、Pydantic、SQLAlchemy；PostgreSQL 持久化会话、引用与反馈 |
| 工作流 | LangGraph ReAct 决策 / 工具 / 观察循环；完成后生成及检查引用；保留基础 RAG 与旧工作流对照 |
| 向量 | BGE-small-zh-v1.5（512 维）、PostgreSQL 16、pgvector HNSW、内容寻址 embedding 缓存 |
| 混合检索 | BM25 + 稠密召回，默认加权融合，支持 RRF；可选 Cross-Encoder 精排 |
| 图谱 | SQL 关系表 + NetworkX，支持人物关系、路径与领域聚合 |
| 摄取 | Celery + Redis，持久化任务状态；索引版本让 API 感知 worker 更新 |
| 缓存 | Redis；键包含检索配置与索引签名，数据更新后失效 |
| 交付 | Alembic、Docker Compose、Nginx、pytest、评测及进程管理脚本 |

全量数据：**326 份传记、9,921 个文本块、335 个图谱节点、355 条关系**。传记覆盖 265 个去重人物名，图谱节点统计范围更大。全量文本和模型不提交 Git；新版数据库及索引独立。

## 实际验证（2026-10-17）

- **84 项自动化测试全部通过，0 跳过**，含真实 pgvector 写入、过滤、替换与删除。
- PostgreSQL/pgvector、Redis、Celery worker 三项连接检查通过。
- 真实模型流式回答、引用、token 统计、Redis 命中和 LangGraph 人物关系问题通过。
- 实际 Celery 上传任务完成，新内容无需重启即可检索；删除探针后恢复到 9,921 块。
- 浏览器验证桌面问答、Markdown、历史会话、326 份文档及移动端布局，无脚本错误。

证据：reports/validation.json、reports/tests-react.xml、reports/full_validation.json、reports/browser_full_validation.json、reports/full_*.png。单次真实模型验收不等于整体回答质量评测；本机没有 Docker，容器配置未实际构建启动。精排默认关闭。

## 开发与复现

使用 Python 3.11+、Node.js 22+：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-prod.txt -r requirements-ml.txt
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
cd frontend
npm ci
npm run build
cd ..
```

当前本机 .env 已配置，不要用示例覆盖。新设备按 .env.full.example 准备语料、模型、密钥及 PostgreSQL / Redis。便携组件在 .runtime，数据库运行数据使用 .runtime/state/portable-location.json 指向的 ASCII 路径，规避 Windows 中文路径编码问题。端口：API 8001、PostgreSQL 15432、Redis 16379，均绑定回环地址，不注册系统服务。

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe scripts/check_stack.py --require-all
.\.venv\Scripts\python.exe -m pytest -q
# 会调用已配置的外部模型：
.\.venv\Scripts\python.exe scripts/verify_full.py
.\.venv\Scripts\python.exe scripts/verify_browser_full.py
```

设置 MATHKB_TEST_POSTGRES_URL 后启用真实 pgvector 测试，使用独立 collection 并清理。scripts/run_eval.py --skip-mcq --out reports/eval_full.json 只评测本地召回；不带该开关的选择题评测可能调用模型。旧 eval_current.json 是离线样例结果，不能作为全量 BGE 质量指标。

## 容器部署配置

在 .env 设置 MODEL_CACHE_DIR 并配置部署用密码、访问令牌；完整模式挂载 data/full、本地模型和密钥，并安装 CPU ML 依赖：

```powershell
docker compose --env-file .env -f docker-compose.yml -f docker-compose.full.yml up --build -d
docker compose --env-file .env -f docker-compose.yml -f docker-compose.full.yml exec api python scripts/check_stack.py --require-all
```

入口 http://127.0.0.1:8080/ 。本机便携 Redis 使用社区 Windows 构建，仅用于本地验证；生产部署使用 Compose 的 Linux Redis / Celery。

## 简历与面试材料

- docs/简历条目.md：项目条目与已验证数字。
- docs/ARCHITECTURE.md：分层、检索与索引一致性。
- docs/迁移映射.md：论文能力与工程改造对照。
- docs/面试深挖.md、docs/演示脚本.md：设计讲解与演示。
- docs/部署与验证.md：完整模式及验证边界。
