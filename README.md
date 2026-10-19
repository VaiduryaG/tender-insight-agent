# 招投标智能分析 Agent

基于中国政府采购网公开数据，构建招投标领域知识图谱，通过自然语言 Agent 实现商业情报分析、竞对监控、商机发现。

## 项目简介

政府采购中标公告蕴含丰富的商业情报：谁在哪个行业、哪个地区中标了什么项目、金额多少。本项目将这些非结构化公告转化为结构化数据，进一步构建「公司-项目-行业-技术」知识图谱，并提供自然语言查询入口——用户可以直接问 *「华为在智慧城市领域的中标情况如何？」*，由 Agent 自主推理、调用工具、组合数据后给出分析结论。

**核心亮点：**
- 真实数据：全部来自中国政府采购网公开中标公告（非虚构数据）
- 完整链路：数据清洗 → 实体对齐 → 知识图谱 → 向量检索 → Agent 问答 → Web 可视化
- 复杂数据处理：多格式标准化、公司名实体对齐（同一公司存在多种写法）

## 技术栈

| 层次 | 技术 | 说明 |
|------|------|------|
| 数据处理 | Pandas, jieba, re | 清洗、标准化、分词 |
| LLM | 智谱AI GLM (OpenAI兼容接口) | 实体对齐辅助、Agent推理 |
| Agent 框架 | LangGraph (StateGraph) | ReAct 循环编排、条件边幻觉防御 |
| 知识图谱 | networkx + JSON持久化 | 图存储与关系查询（抽象接口，可切换Neo4j） |
| 向量检索 | ChromaDB | 项目语义搜索 |
| 工具协议 | MCP (FastMCP) | 工具标准化封装 |
| Agent 模式 | ReAct + 幻觉防御 | 思考→行动→观察循环（LangGraph StateGraph 编排） |
| Web | Streamlit + Plotly + Pyvis | 界面与图谱可视化 |

> 向量化优先使用智谱AI Embedding API（无需下载本地模型）；若后续需要离线能力再引入 sentence-transformers。

---

## 系统架构

```
┌────────────────────────────────────────────────────────┐
│              Web 展示层 (Streamlit)                     │
│     数据概览 │ 图谱可视化 │ Agent 对话                  │
└────────────────────────┬───────────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────────┐
│              Agent 层 (ReAct + GLM)                     │
│   问题分析 → 工具选择 → 结果观察 → 综合回答             │
└────────────────────────┬───────────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────────┐
│              工具层 (MCP 标准化封装)                     │
│  search_projects │ graph_query │ semantic_search │ ...  │
└───────┬──────────────────┬──────────────────┬──────────┘
        │                  │                  │
┌───────▼──────┐  ┌────────▼───────┐  ┌───────▼──────┐
│  networkx    │  │   ChromaDB     │  │   SQLite     │
│  知识图谱    │  │   向量库        │  │  结构化数据  │
│ (接口抽象，  │  │                │  │              │
│ V2.0可切Neo4j)│  │                │  │              │
└───────▲──────┘  └────────▲───────┘  └───────▲──────┘
        │                  │                  │
┌───────┴──────────────────┴──────────────────┴──────────┐
│              数据处理层                                  │
│   清洗去重 → 格式标准化 → 实体对齐（规则+LLM）           │
└────────────────────────▲───────────────────────────────┘
                         │
┌────────────────────────┴───────────────────────────────┐
│         数据源：中国政府采购网（V1.0人工采集种子数据，    │
│                    V2.0 实现自动化爬虫）                 │
└────────────────────────────────────────────────────────┘
```

**数据流：** 原始CSV → 清洗标准化 → 实体对齐 → 分别写入 SQLite / Neo4j / ChromaDB → 工具层统一查询 → Agent 推理 → Web 展示

---

## 模块划分

### 模块1：数据处理 `src/data_processing/`

**职责：** 将原始公告数据加工为高质量结构化数据，是全项目的数据质量基石。

| 文件 | 职责 | 输入 → 输出 |
|------|------|------------|
| `cleaner.py` | 去重、缺失值处理、异常值检测 | 原始CSV → 基础清洗数据 |
| `normalizer.py` | 金额/日期/公司名格式标准化 | 基础清洗数据 → 标准化数据 |
| `entity_aligner.py` | 公司名实体对齐（核心难点） | 标准化数据 → 对齐后数据 + 映射表 |

**实体对齐策略（分层）：**
```
L1 规则匹配：去除"有限公司/股份/集团"等后缀后精确匹配
L2 模糊匹配：编辑距离 + Jaccard 相似度，阈值判断
L3 LLM 辅助：调用 GLM 判断"中国电信股份有限公司温州分公司"与
   "中国电信集团有限公司"是否为同一主体的不同分公司/母公司
L4 人工映射表：data/raw/company_mapping_template.csv 兜底
```

**验收标准：** 输出标准化统计报告；公司名对齐结果可通过人工抽查验证。

### 模块2：知识图谱 `src/knowledge_graph/`

**职责：** 建模并构建「公司-项目-行业-技术」图谱，支持关系查询。

| 文件 | 职责 |
|------|------|
| `schema.py` | 节点/关系类型定义 |
| `store.py` | **存储抽象接口**（`GraphStore` 基类）+ networkx 实现 |
| `builder.py` | 从清洗数据构建节点与关系 |
| `queries.py` | 常用图查询封装（调用 GraphStore 接口） |

**图谱模型：**
```
节点：Company(公司) / Project(项目) / Industry(行业) / Technology(技术) / Region(地区)
关系：
  (:Company)-[:WON {amount, date}]->(:Project)        中标
  (:Project)-[:BELONGS_TO]->(:Industry)               行业归属
  (:Project)-[:REQUIRES]->(:Technology)               技术需求
  (:Project)-[:LOCATED_IN]->(:Region)                 地域分布
  (:Company)-[:COMPETES_WITH {shared_projects}]->(:Company)  竞对（由同行业共同投标推导）
```

**存储设计（Neo4j 预留）：**
```
GraphStore (抽象基类)
   ├── 定义接口：add_node / add_edge / neighbors /
   │            find_competitors / company_projects / subgraph
   ├── NetworkXStore (V1.0)：纯内存实现，JSON 文件持久化到 data/graph/
   └── Neo4jStore (V2.0，待补)：实现同一接口，切换只改一行配置
```
builder/queries/上层模块只依赖 `GraphStore` 接口，不感知底层实现。V2.0 补 Neo4j 时新增一个 `Neo4jStore` 类即可，图数据构建逻辑完全复用。

**验收标准：** 图谱可构建并持久化；`queries.py` 中预置查询（公司中标历史、行业竞对、技术趋势）均可返回正确结果；切换 Store 实现不影响上层代码。

### 模块3：向量检索 `src/vector_search/`

**职责：** 项目文本语义向量化，支持模糊语义搜索。

| 文件 | 职责 |
|------|------|
| `embedder.py` | 调用智谱AI Embedding API 生成向量 |
| `search.py` | ChromaDB 写入与语义检索 |

**验收标准：** 搜索「智慧校园」能召回标题不含该词但语义相关的教育信息化项目。

### 模块4：MCP 工具层 `src/mcp_tools/`

**职责：** 将底层能力封装为标准化工具，供 Agent 调用，同时可通过 MCP 协议暴露给外部客户端。

| 工具 | 功能 | 数据源 |
|------|------|--------|
| `search_projects` | 按行业/地区/金额区间/关键词筛选项目 | SQLite |
| `company_profile` | 公司画像：中标历史、总金额、行业分布 | SQLite + Neo4j |
| `graph_query` | 竞对分析、合作关系、技术关联 | networkx 图谱 |
| `semantic_search` | 语义搜索相似项目 | ChromaDB |
| `industry_stats` | 行业/地区/时间维度统计分析 | SQLite |

**设计决策：** 工具实现为普通 Python 函数（进程内调用），MCP Server (`server.py`) 作为薄包装对外暴露。Agent V1.0 进程内直接调用，避免多进程通信复杂度。

**验收标准：** 每个工具有独立测试用例；MCP Server 可启动并响应工具调用。

### 模块5：Agent 核心 `src/agent/`

**职责：** 理解自然语言问题，自主规划、调用工具、综合数据回答。

| 文件 | 职责 |
|------|------|
| `llm.py` | 智谱AI客户端封装（OpenAI兼容接口） |
| `prompts.py` | ReAct 提示词模板、少样本示例 |
| `graph_agent.py` | **LangGraph Agent**（StateGraph：agent→tools 循环 + 幻觉防御条件边） |
| `react.py` | 手写 ReAct 循环（LangGraph 版的行为基准与测试替身协议来源） |
| `conversation.py` | 多轮对话历史管理 |

**典型问答示例：**
```
Q: 华为在智慧城市领域的中标情况如何？
Agent 思考: 需要查华为的公司信息 → 调用 company_profile
Agent 思考: 需要行业维度的图谱关系 → 调用 graph_query
Agent 思考: 补充语义相关的项目 → 调用 semantic_search
Agent 回答: 综合三个工具结果 + 金额统计，生成结构化分析
```

**验收标准：** 10 个预设测试问题，Agent 能正确选择工具并给出有数据支撑的回答。

### 模块6：Web 界面 `src/web/`

**职责：** 可视化展示与交互入口。

| 页面 | 功能 |
|------|------|
| `pages/1_数据概览.py` | 行业/地区/时间维度统计图表 |
| `pages/2_知识图谱.py` | Pyvis 交互式图谱可视化，支持按公司/行业过滤 |
| `pages/3_Agent对话.py` | 聊天界面，展示 Agent 思考过程与工具调用记录 |

**验收标准：** 三个页面均可正常交互；图谱可视化支持节点点击展开。

### 模块7：爬虫 `src/crawler/`（V2.0，本期不开发）

V1.0 使用人工采集的种子数据（18条）验证全链路。V2.0 实现中国政府采购网自动化采集，扩展数据量至 500+ 条。

---

## 开发方案

### 开发顺序（严格串行，每阶段有可验证产出）

```
阶段1  数据处理          ████░░░░░░  ~2天
        产出：清洗后数据 + 实体对齐映射表 + 质量报告
        验证：cleaner/normalizer/entity_aligner 单测通过，报告可读

阶段2  知识图谱          ░░████░░░░  ~2天
        产出：networkx 图谱（节点+关系）+ 查询封装 + JSON持久化
        验证：预置图查询返回正确结果，GraphStore 接口可替换

阶段3  向量检索          ░░░░██░░░░  ~1天
        产出：ChromaDB 向量库 + 语义搜索
        验证：语义搜索能召回相关项目

阶段4  Agent 核心        ░░░░░░███░  ~2天
        产出：ReAct Agent + 5个工具注册
        验证：10个预设问题测试通过

阶段5  MCP Server        ░░░░░░░░█░  ~1天
        产出：MCP 标准化工具服务
        验证：工具调用测试通过

阶段6  Web 界面 + 整合   ░░░░░░░░░█  ~2天
        产出：Streamlit 三页面应用 + 端到端联调
        验证：完整演示流程走通（启动→浏览→提问→回答）
```

**总预估：约 10 天**（每天 3-4 小时投入）

### 当前状态

```
✅ 环境搭建（.venv + 核心依赖）
✅ 种子数据（18条人工采集，9个行业，12个省份）
✅ 阶段1：数据处理（清洗+标准化+实体对齐，28个测试通过）
✅ 阶段2：知识图谱（networkx构建+查询+持久化，134节点/158关系）
✅ 阶段3：向量检索（智谱embedding-3 + ChromaDB，语义搜索+元数据过滤）
✅ 阶段4：Agent 核心（LangGraph StateGraph + GLM-4-Flash + 5个工具，链式推理验证通过）
✅ 阶段5：MCP Server（FastMCP stdio，内存会话+子进程端到端验证）
✅ 阶段6：Web 界面 + 整合（Streamlit三页面，端到端联调通过）
✅ V2.0：自动化爬虫 + 数据清洗强化（58条数据、竞对271组、质量仪表盘与基准集工具）
✅ 数据优化：LLM辅助行业分类（"其他"占比 32.8% → 10.3%）

🎉 双版本完成：115个测试全通过，全链路可演示（Agent 已升级 LangGraph 实现，行业分类质量优化）
```

### 前置条件（阶段1开始前确认）

| 事项 | 状态 | 说明 |
|------|------|------|
| Python 3.12 + .venv | ✅ 已完成 | |
| 种子数据 | ✅ 已完成 | `data/raw/bidding_sample.csv` |
| 智谱AI API Key | ✅ 已配置 | `.env` |
| 图数据库 | ✅ 已决策 | 远程开发不跑数据库服务，V1.0 用 networkx + JSON 持久化；`GraphStore` 接口已抽象，V2.0 补 Neo4j 只需新增实现类 |
| 阶段性依赖安装 | ⬜ 按阶段安装 | 见 requirements.txt 分组注释 |

### 风险与降级方案

| 风险 | 概率 | 降级方案 |
|------|------|----------|
| 远程环境无法运行数据库服务 | 已规避 | V1.0 采用 networkx（纯 Python 库）+ JSON 持久化，无需任何服务；GraphStore 接口抽象保证 V2.0 可无缝切换 Neo4j |
| HuggingFace 模型下载失败 | 高 | 已规避：使用智谱AI Embedding API，无需本地模型 |
| LLM API 限流/费用 | 低 | 实体对齐 L3 层可跳过（L1+L2 已可达 85%+ 准确率） |
| 种子数据量小（18条） | 确定 | 属预期内：V1.0 验证链路，V2.0 爬虫扩量 |

### 版本规划

**V1.0（本期，约10天）**
- 18条种子数据全链路打通：清洗 → 图谱 → 语义检索 → Agent → Web
- 5个标准工具 + ReAct 问答 + 图谱可视化

**V2.0（已完成 2026-09-17）**
- ✅ 自动化爬虫 + 数据清洗强化（详见 [V2.0 方案](docs/v2_crawler_plan.md)）
  - 中国政府采购网自动爬取（保守低速：5-8秒间隔、单日限量、WAF熔断保护），增量去重
  - 正文字段提取（三层策略：表格区/键值对/行内冒号），字段命中率 ≥94%
  - 数据量 18 → 58，图谱竞对 14 → 271 组，质量仪表盘 + 人工基准集工具
  - 实体对齐 L3 LLM 复核开启 + needs_review 人工确认闭环
- ✅ Agent 升级 LangGraph（2026-10-08）：StateGraph 编排 ReAct 循环，幻觉防御改为条件边（nudge 节点），手写版保留作行为基准（详见 [LangChain/LangGraph 对照学习](docs/langchain_langgraph_study.md)）
- PDF 公告解析、附件金额提取（P1，单独一期）
- 跨平台数据源接入与去重（P2）
- Neo4j 迁移：实现 `Neo4jStore`（`GraphStore` 同一接口），支持 Cypher 查询
- 竞对分析增强（图算法：社区发现、中心度）

---

## 快速开始

```bash
# 环境验证
./run.sh scripts/verify_setup.py

# 数据处理（阶段1产出）
./run.sh scripts/run_stage1.py

# 构建知识图谱（阶段2产出）
./run.sh scripts/build_graph.py

# 构建向量索引（阶段3产出）
./run.sh scripts/build_vector_index.py

# 启动 Web（阶段6产出）
./run.sh -m streamlit run src/web/app.py

# 启动 MCP Server（stdio，供 Claude Desktop 等客户端连接）
./run.sh scripts/run_mcp_server.py
```

> **环境说明（uv 管理，公司规定禁止直接 pip）：**
>
> `.venv` 由 `uv venv` 创建（默认不含 pip），所有依赖通过 `uv pip` 安装：
>
> ```bash
> # 装依赖（统一入口，必须加 -p 指定项目环境）
> uv pip install -p .venv/bin/python -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
>
> # 装单个包
> uv pip install -p .venv/bin/python <package> -i https://pypi.tuna.tsinghua.edu.cn/simple
> ```
>
> **为什么用 `run.sh` 而不是直接 `.venv/bin/python`：** 本机 shell 预设了公司全局环境的 `PYTHONPATH`，其优先级高于 `.venv`，会导致 pandas/langgraph 等包被全局同名旧包遮蔽。`run.sh` 在运行时屏蔽 `PYTHONPATH`，确保 100% 使用 `.venv` 的依赖（已验证 16 个核心包全部归属 `.venv`）。

## 数据来源

中国政府采购网（www.ccgp.gov.cn）公开中标公告，人工采集 18 条种子数据，覆盖智慧教育、智能交通、智慧医疗、政务监管等 9 个行业、12 个省份。

## 文档

- [开发过程全解](docs/project_walkthrough.md)（个人文档：运行手册 + 逐文件逐函数说明 + 踩坑记录）
- [测试迭代记录](docs/testing_iteration_log.md)（个人文档：问题现象→诊断→根因→解决→验证）
- [V2.0 爬虫方案](docs/v2_crawler_plan.md)（合规红线 + D1-D9 进度 + 决策记录）
- [LangChain/LangGraph 对照学习](docs/langchain_langgraph_study.md)（手写 Agent ↔ 框架概念映射）

## License

MIT