# 招投标智能分析 Agent — 项目开发全解（个人文档）

> 本文档只面向项目作者本人，以开发者视角回顾 V1.0 的完整开发过程，
> 逐文件、逐函数说明每个部分的输入、输出与用途。
> 阅读前提：你至少完整看过一遍 README.md。

---

## 〇、如何运行此项目

### 0.1 环境约定（重要，先读这个）

| 约定 | 原因 |
|------|------|
| 所有 Python 命令用 `./run.sh` 包装 | 本机 shell 有公司预设的 `PYTHONPATH` 指向全局环境（`/opt/python-venv`），优先级高于 `.venv`，会把 pandas/langgraph 等遮蔽到全局旧版本。`run.sh` 运行时屏蔽 `PYTHONPATH`，保证 100% 用 `.venv` 的包 |
| 装依赖只用 `uv`（公司规定，禁止 pip） | `.venv` 由 `uv venv` 创建，本身不含 pip |
| 镜像统一走清华源 | 公司网络访问 PyPI 官方源慢 |

```bash
# 装依赖（统一入口）
uv pip install -p .venv/bin/python -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 0.2 运行步骤（Step-by-Step 操作手册）

> 所有命令都在**项目根目录**执行。每一步都附了「预期结果」，输出对不上时按第 0.5 节排查。
> V2.0 后全流程变为四步数据构建（爬取 → 解析 → 阶段1 → 图谱 → 向量），日常运行只需 Step 7。

**Step 1 — 进入项目目录**

```bash
cd /home/guoruirui/work-space/project
ls run.sh   # 确认能看到 run.sh
```

**Step 2 — 环境自检**

```bash
./run.sh scripts/verify_setup.py
```

预期结果（末尾四项全通过）：
```
目录与文件: ✅ 通过
核心依赖: ✅ 通过
配置: ✅ 通过
✅ 环境就绪！
```

- 若提示 `.venv` 相关问题（新机器/误删）：先 `uv venv .venv --python 3.12`，再装依赖：
  `uv pip install -p .venv/bin/python -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`
- 若提示「阶段产物 ⬜」：说明还没构建过数据，继续 Step 3-6；产物已存在则可直接跳到 Step 7

**Step 3 — 爬虫抓取（V2.0 新增；可选——已有 data/raw/crawled/ 时可跳过）**

```bash
# 先摸底：只解析列表页不抓详情（1个请求）
./run.sh scripts/crawl_ccgp.py --pages 1 --list-only

# 正式抓取（保守低速：5-8秒随机间隔、每10页长休息1-2分钟、单次请求上限默认200）
./run.sh scripts/crawl_ccgp.py --pages 3 --limit 20    # 抓20条详情，约5分钟
# 增量：重复运行自动跳过已爬 URL（seen_urls.json 持久化），随时可中断续爬
```

预期结果：
```
列表第0页: 20 条，其中新 17 条
[1/17] 已入库 1 条: <公告标题>
本次完成: 新入库 N 条, 失败 0 条, 总库存 N
```

⚠️ **红线**：遇 403（WAF 拦截）或连续 5 次失败会自动熔断退出，属保护行为——当日停止爬取，
次日再续。绝不手动绕过。

**Step 4 — 爬取数据解析（V2.0 新增：原始JSONL → V1.0 schema）**

```bash
./run.sh scripts/parse_raw.py
```

预期结果：
```
原始公告 N 条，开始解析映射…
转换完成: M 条（去重删除 K 条）
  供应商: 46/48 (96%)
  行业分布: ...
已输出 -> data/processed/bidding_crawled.csv
```

**Step 5 — 阶段1：数据处理（自动合并 种子+爬虫 双数据源）**

```bash
./run.sh scripts/run_stage1.py
```

预期结果（58条 = 18种子 + 48爬虫 - 8条零金额/缺供应商剔除）：
```
并入爬虫数据 48 条
实体对齐完成: 58 个公司名 -> 58 个法人实体 / 55 个集团
低置信度对齐 7 条 -> data/processed/needs_review.csv
标准化数据 -> data/processed/bidding_stage1.parquet
```
并打印质量仪表盘（核心指标 vs 上次运行对比、对齐方法分布、爬虫提取质量区块）。

**Step 6 — 重建图谱与向量索引**

```bash
./run.sh scripts/build_graph.py          # 预期: 58 项目 -> 竞对 271 组
./run.sh scripts/build_vector_index.py   # 预期: 向量索引构建完成: 58 条
```

**Step 7 — 运行项目（三种方式，可任选或同时）**

**方式 A：Web 界面（推荐，演示效果最好）**

```bash
./run.sh -m streamlit run src/web/app.py
```

- 终端出现 `Local URL: http://localhost:8501`，远程开发时 VS Code 会自动转发端口，在**本地浏览器**打开该地址
- 三个页面：📈 数据概览（图表）→ 🕸️ 知识图谱（侧边栏选公司，深度拉到2）→ 🤖 Agent 对话（输入问题，回答下方可展开推理过程）
- 停止：终端 `Ctrl+C`

**方式 B：命令行 Agent（不点浏览器，快速验证）**

```bash
./run.sh scripts/run_agent.py -q "中国联通的竞争对手有哪些？分别在哪些领域竞争？"
```

预期结果：打印 `[步骤N] 调用 xxx(...)` 真实工具调用链，最后输出带数据的回答（约 10-30 秒）。
回答文本中若出现 `Thought:/Action:` 字样属于幻觉防御范围，系统会自动纠正重试。

```bash
./run.sh scripts/run_agent.py --full   # 跑全部 10 个验收问题
```

**方式 C：MCP Server（给 Claude Desktop 等外部客户端用）**

```bash
./run.sh scripts/run_mcp_server.py
```

- 启动后终端**看似卡住无输出是正常的**（stdio 阻塞监听），停止用 `Ctrl+C`
- 协议级验证：`./run.sh -m pytest tests/test_stage5.py -v`

**Step 8 — 跑测试（任何时候都可以，零 API 消耗）**

```bash
./run.sh -m pytest tests/ -v
```

预期结果：`101 passed`

### 0.3 日常快速启动（产物已就绪后）

```bash
./run.sh -m streamlit run src/web/app.py    # Web
# 或
./run.sh scripts/run_agent.py               # 命令行
```

产物已持久化在 `data/` 下，日常**不需要**重跑 Step 3-6。想扩充数据量时重跑 Step 3（增量）→ 4 → 5 → 6。

**数据侧日常操作补充（V2.0）：**

```bash
# 人工核对提取质量（基准集）：生成预填模板 → 核对 → 评估
./run.sh scripts/golden_set.py --make --sample 20
./run.sh scripts/golden_set.py --eval

# 实体对齐人工确认闭环：
# 1. 打开 data/processed/needs_review.csv
# 2. 对低置信度行填 verified_group 列 + is_verified=True
# 3. 重跑 Step 5（L4 直接命中，不再进灰区）
```

### 0.4 测试（零 API 消耗，随时可跑）

```bash
./run.sh -m pytest tests/ -v                  # 全部 101 个测试
./run.sh -m pytest tests/test_crawler.py -v   # 单独跑爬虫模块
```

测试全部使用离线替身（FakeLLM / HashingEmbedder / 迷你夹具数据 / 网络stub），不消耗 API、不依赖网络。
爬虫解析测试使用 `tests/fixtures/ccgp_list_sample.html`（真实列表页快照）。

### 0.5 常见问题排查

| 现象 | 原因与解法 |
|------|-----------|
| `ModuleNotFoundError`（pandas 等明明装了） | 用了裸 `python` 而不是 `./run.sh`——全局 PYTHONPATH 干扰，所有命令必须走 `./run.sh` |
| 爬虫被 403 拦截退出（CrawlingBlocked） | WAF 保护行为，**当日停止爬取**，次日重跑自动续爬；不要手动绕过 |
| 爬虫"列表页解析出 0 条" | ccgp 页面结构可能变更（连续5条失败会自动熔断）；检查 tests/fixtures 里的样本与线上差异 |
| Agent 回答里公司名/金额是编的 | V2.0 已有幻觉防御（自动纠正重试+used_tools标记）；若 Web 上仍出现请回报（对照 IT-014） |
| Agent 说"未找到该公司" | 试试集团简称（如"中国联通"），_resolve_group 支持子串/字符集两级解析；仍失败说明该集团确实不在数据集中 |
| Web 端口被占用 | 换端口：`./run.sh -m streamlit run src/web/app.py --server.port 8502` |
| Agent 报 401 / API Key 错误 | 检查 `.env` 中 `ZHIPU_API_KEY` 是否有效、账户是否欠费 |
| 想完全重建数据产物 | `rm -rf data/processed data/graph data/vector data/raw/crawled` 后重跑 Step 3 → 6 |
| MCP Server 启动后没反应 | 正常现象（stdio 阻塞监听），验证协议用 `./run.sh -m pytest tests/test_stage5.py -v` |
| jieba 打印 SyntaxWarning | Python 3.12 下的已知无害警告，忽略 |

### 0.6 数据与产物清单

| 文件 | 说明 | 可否删除 |
|------|------|----------|
| `data/raw/bidding_sample.csv` | 18 条人工采集种子数据（**手工整理，勿动**） | ❌ |
| `data/raw/crawled/announcements.jsonl` | V2.0 爬取原始正文（解析规则迭代后可重解析，**删了要重爬**） | 尽量保留 |
| `data/raw/crawled/seen_urls.json` | 已爬 URL 集合（增量去重依据；删除会导致重爬） | 与 jsonl 同进退 |
| `data/raw/company_mapping_template.csv` | 人工实体映射表（L4 兜底） | 保留 |
| `data/processed/bidding_crawled.csv` | 爬虫数据转 V1.0 schema（parse_raw 产出） | 可重建 |
| `data/processed/needs_review.csv` | 实体对齐人工确认清单（闭环入口，填好勿删） | 保留 |
| `data/processed/golden_set.csv` | 人工标注基准集（质量评估依据） | 保留 |
| `data/processed/extract_stats.json` / `stage1_metrics.json` | 质量仪表盘数据源与快照 | 可重建 |
| `data/processed/bidding_stage1.parquet` | 阶段1产物 | 可重建 |
| `data/graph/bidding_graph.json` | 阶段2图谱 | 可重建 |
| `data/vector/chroma/` | 阶段3向量库 | 可重建 |
| `.env` | 智谱 API Key（勿提交、勿外传） | ❌ |

---

## 一、系统总览

```
中国政府采购网 ──► 爬虫层 src/crawler (V2.0，保守低速+熔断)      18条种子CSV
   │                        │                                        │
   │                        ▼                                        │
   │               原始JSONL ──► parse_raw ──► V1.0 schema 12列      │
   │                                                 │               │
   └──────────────────────┬───────────────────────────┘               │
                          ▼                                           ▼
                  阶段1 数据处理（合并双源：清洗→标准化→实体对齐L1-L4）
                          │
            ┌─────────────┴─────────────┐
            ▼                           ▼
   阶段2 知识图谱(58公司/271竞对)   阶段3 向量库(ChromaDB+智谱embedding)
            │                           │
            └─────────────┬─────────────┘
                          ▼
        工具层 src/mcp_tools  ◄────  Agent层 src/agent (ReAct+GLM+幻觉防御)
                │                                    │
     build_registry(进程内)                 scripts/run_agent.py(命令行)
     server.py(MCP stdio)                   src/web/pages/3(对话页)
                                                     │
                                            Web层 src/web (Streamlit)
```

一次提问的完整路径：
`用户问题 → ReActAgent.run() → LLM 思考选工具 → ToolRegistry.call() →
工具函数读 parquet/图谱/向量库 → Observation 回填 → 循环 → Final Answer → Web 渲染`

数据扩量路径（V2.0）：
`crawl_ccgp.py（增量爬取）→ parse_raw.py（解析转换）→ run_stage1.py（双源合并清洗）
→ build_graph.py + build_vector_index.py（重建）`

---

## 二、逐文件详解

### 2.1 数据处理模块 `src/data_processing/`

#### `cleaner.py` — 结构性清洗

只做"行级"判断（删不删这一行），不改字段内容——格式转换归 normalizer 管。

| 成员 | 签名 | 说明 |
|------|------|------|
| `REQUIRED_FIELDS` | 常量 | 6个必填字段（title/company_name/amount/date/province/city），缺失整行剔除 |
| `OPTIONAL_DEFAULTS` | 常量 | 可选字段缺省值（purchaser→"未知"，industry→"未分类"等） |
| `AMOUNT_MIN/MAX` | 常量 | 金额合理区间 [1万, 100亿]，越界视为噪声/解析错误剔除 |
| `CleaningReport` | dataclass | 清洗统计：total_input / dropped_missing(带title) / dropped_duplicate / dropped_amount_outlier / filled_optional / total_output；`summary()` 渲染文本 |
| `DataCleaner.clean` | `(df) -> (df, CleaningReport)` | 主入口，依次执行下面5步，返回清洗后数据+报告 |
| `_strip_whitespace` | `(df) -> df` | 文本列去首尾空白、全角空格转半角 |
| `_drop_missing_required` | `(df, report) -> (df, report)` | 必填字段任一为空/空串 → 剔除，记录被删title |
| `_drop_duplicates` | `(df, report) -> (df, report)` | 整行重复去重 + project_id 非空时按编号去重 |
| `_drop_amount_outliers` | `(df, report) -> (df, report)` | 金额转数值后过滤越界值；**无法解析的不删**（留给 normalizer 返回 None） |
| `_fill_optional` | `(df, report) -> (df, report)` | 可选字段空值填默认值 |

#### `normalizer.py` — 字段格式标准化

| 函数 | 签名 | 说明 |
|------|------|------|
| `normalize_amount` | `(value) -> float \| None` | 任意金额写法 → 元。"208.8万元"→2088000.0，"1,163,909.08元"→1163909.08，"0.5亿"→5e7。用正则提数字+单位换算表（万亿/亿/千万/万）。失败返回 None |
| `normalize_date` | `(value) -> str \| None` | 任意日期写法 → "YYYY-MM-DD"。"2026年9月8日"/"2026.9.8"/"2026/09/08" 全支持；非法日期（2026-13-01）返回 None |
| `normalize_text` | `(value) -> str \| None` | 去空白、压连续空格、全角空格转半角 |
| `normalize_tech_tags` | `(value) -> list[str]` | "AI;智慧课堂;云桌面" → ["AI","智慧课堂","云桌面"]。支持分号/顿号/逗号等分隔，去重保序 |
| `normalize_dataframe` | `(df) -> df` | 批量应用上述函数，产出 `*_normalized` 列和 `tech_tags_list` 列（保留原始列）；日期解析失败的行剔除 |

#### `entity_aligner.py` — 公司名实体对齐（全项目核心难点）

产出两级结果：`canonical_name`（法人实体级，图谱 Company 节点用）和
`group_name`（集团级，聚合分析用）。四层策略 L1规则 → L2模糊 → L3 LLM → L4 人工表。

| 成员 | 签名 | 说明 |
|------|------|------|
| `_COMPANY_SUFFIXES` | 常量 | 企业后缀表，按长度降序（"股份有限公司"先于"有限公司"匹配，防止截断残留） |
| `_BRANCH_SUFFIXES` | 常量 | 分公司后缀（"分公司"/"分支公司"） |
| `AlignResult` | dataclass | 单个公司名的对齐结果：original_name / canonical_name / group_name / method(manual/rule/llm/self) / confidence |
| `EntityAligner.__init__` | `(mapping_csv, fuzzy_threshold=0.82, llm_gray_zone, use_llm=False)` | 加载人工映射（两个来源：needs_review 人工确认清单优先 + 主映射表）；V2.0 管线中 `use_llm=True` 已开启 |
| `align` | `(names) -> list[AlignResult]` | 主入口。同名去重共享结果；L4命中直接用；其余按 `_normalize_key` 分组，同键=同一法人实体（组内取最长原名做 canonical） |
| `_load_mapping` | `(path) -> dict` | 加载人工映射 → {原名: (标准名, 集团名)}。**D6 闭环**：先读 `data/processed/needs_review.csv`（is_verified=True 且 verified_group 非空的行），再读主映射表（前者优先） |
| `_normalize_key` | `(name) -> str` | L1核心：迭代剥后缀 + 去分隔符。"中国电信集团有限公司"和"中国电信股份有限公司"都归一化为"中国电信"，同键合并 |
| `_iterative_strip_suffix` | `(name) -> str` | 循环剥企业后缀直到剥不动（**必须迭代**：单次剥离会让"…集团有限公司"停在第1层，与"…股份有限公司"写法合并不了） |
| `_extract_group` | `(name) -> str` | 提取集团名。分公司处理：剥"分公司"后缀 → 定位最后一个"公司"，其后是城市名，截掉（确定性字符串操作，**不用正则**——正则 `[一-龥]{2,8}` 曾把"有限公司"当城市名吞掉，导致"中国联合网络通信"丢字成"中国联合网络通"） |
| `_similarity` | `(a, b) -> float` | L2：0.6*编辑距离比 + 0.4*字符Jaccard。阈值 0.82 已用 V2.0 真实 58 条标定（0.75 相似对为不同公司，零误合并） |
| `_in_gray_zone` | `(name, existing) -> bool` | 相似度落在 (0.60, 0.82) 灰区时才值得调 LLM。V2.0 实测 58 条灰区 7 次 |
| `_llm_confirm_group` | `(name, rule_group) -> str \| None` | L3：GLM-4-Flash 判断是否同一集团，失败静默返回 None 降级到规则结果。实测 7/7 与规则结论一致 |
| `to_mapping_dataframe` | `(results) -> df` | 导出映射表 CSV，供人工复核和 L4 增量沉淀 |

### 2.2 知识图谱模块 `src/knowledge_graph/`

#### `schema.py` — 图谱建模

| 成员 | 说明 |
|------|------|
| `NodeType(StrEnum)` | 5类节点：Company / Project / Industry / Technology / Region |
| `RelationType(StrEnum)` | 5类关系：WON(中标,带amount/date) / BELONGS_TO / REQUIRES / LOCATED_IN / COMPETES_WITH(带shared_industries) |
| `node_id(type, key)` | `(NodeType, str) -> "Type:key"` 全局唯一节点id，与存储实现无关 |
| `project_key(project_id, title)` | 项目键：采购编号优先，缺失用 `hash-` + 标题md5前12位兜底（保证幂等） |

#### `store.py` — 存储抽象（Neo4j 预留的关键设计）

`GraphStore(ABC)` 定义8个抽象方法；`NetworkXStore` 用 `MultiDiGraph` 实现全部接口。
上层只依赖接口——V2.0 写一个 `Neo4jStore` 实现同接口即可整体替换，测试有
`test_store_interface_contract` 强制约束实现完整性。

| GraphStore 抽象方法 | 说明 |
|------|------|
| `add_node(type, key, props) -> str` | 建/更新节点（重复添加合并属性） |
| `add_edge(src, dst, rel, props)` | 建边；同类型同方向只保留一条，重复合并属性 |
| `get_node(type, key) -> dict \| None` | 读节点属性 |
| `nodes(type=None) -> [(key, props)]` | 列节点，可按类型过滤 |
| `edges(rel_type) -> [(src, dst, props)]` | 列某类关系的所有边 |
| `neighbors(type, key, rel=None, direction) -> 迭代器` | 遍历邻居，产出 (key, type, 边属性)；direction: out/in/both |
| `subgraph(type, key, depth) -> {nodes, edges}` | BFS 取 N 跳 ego 网络，输出结构直接喂 Pyvis |
| `stats() -> dict` | 各类型节点数/关系数统计 |
| `save(path) / load(path)` | JSON 持久化（node_link_data 格式） |

内部约定：节点属性里的 `_type`/`_key` 是系统字段（下划线开头），对外输出时过滤。

#### `builder.py` — 图谱构建

| 函数 | 签名 | 说明 |
|------|------|------|
| `build_graph(df, store) -> store` | 主入口：逐行调 `_add_project_row`，最后 `_derive_competitors`。同一 store 重复调用幂等（add_node/add_edge 均合并） |
| `_add_project_row(store, row)` | 单条公告：建 Company(带group属性) + Project + WON边 + 行业/地区/技术节点与边。技术列可能是 numpy array（parquet 回读），统一转 list |
| `_derive_competitors(store) -> int` | 竞对推导：BELONGS_TO 得项目→行业，WON 得公司→项目 → 每个行业内公司两两连 COMPETES_WITH（双向边，属性 shared_industries）。返回无向竞对组数 |

#### `queries.py` — 查询层（Agent 工具的数据来源）

| 函数 | 签名 | 说明 |
|------|------|------|
| `company_projects(store, company)` | `-> list[dict]` | 中标历史（WON邻居 + Project详情），按日期倒序 |
| `company_profile(store, company)` | `-> dict` | 公司画像：project_count / total_amount / industry_distribution / competitors / recent_projects |
| `group_profile(store, group)` | `-> dict` | **集团画像**：聚合 group 下所有成员公司（阶段4加的——用户问"中国电信"时应答集团维度而非单家分公司）。V2.0 修复：竞对聚合保留 shared_industries（原实现聚合时丢失领域信息，Agent 答"领域无法得知"——IT-016） |
| `find_competitors(store, company)` | `-> list[dict]` | 竞对名单，按共同竞争行业数排序 |
| `industry_top_companies(store, industry, top)` | `-> list[dict]` | 行业内金额排行 |
| `technology_trends(store, top)` | `-> list[dict]` | 技术需求热度（REQUIRES 边计数） |
| `region_overview(store)` | `-> list[dict]` | 各省项目数/金额 |
| `group_aggregation(store)` | `-> list[dict]` | 集团维度聚合（总额/项目数/成员公司列表） |
| `graph_overview(store)` | `-> dict` | 图规模统计（store.stats 透传） |

### 2.2b 爬虫模块 `src/crawler/`（V2.0 新增）

**合规红线固化在代码里**（docs/v2_crawler_plan.md 第二章）：请求间 5-8 秒随机延时、
每 10 个请求长休息 60-120 秒、单次运行请求上限 200、403 即熔断当日停止、连续 5 次失败熔断。

#### `base.py` — 爬虫基础设施

| 成员 | 签名 | 说明 |
|------|------|------|
| `BROWSER_HEADERS` | 常量 | 完整浏览器请求头（D1 验证：裸 curl 无 UA 会被 WAF 403） |
| `CrawlingBlocked(Exception)` | — | WAF 拦截/熔断信号，入口脚本捕获后当日停止 |
| `RateLimiter` | dataclass | `wait()`：计数请求，每 rest_every 个长休息，其余随机间隔 |
| `PoliteSession.__init__` | `(rate_limiter, timeout=20, max_retries=3)` | requests.Session + 浏览器头 |
| `.get(url, referer)` | `-> str`（HTML文本） | 限速请求；200 → `_decode` 解码；403 → 抛 CrawlingBlocked（**绝不重试**）；其他状态码指数退避重试；连续失败 `_check_breaker` 熔断 |
| `._decode(resp)` | `-> str` | **编码解码**（IT-015 教训）：解析响应头 1KB 内 meta charset 声明优先，utf-8 → gbk 回退，乱码启发式检查（`�` 计数）。chardet 的 apparent_encoding 在 ccgp 个别页面会误判出乱码 |

#### `ccgp.py` — ccgp 爬虫（解析与请求分离，解析函数纯输入输出可单测）

| 成员 | 签名 | 说明 |
|------|------|------|
| `_DETAIL_RE` | 常量 | 详情链接正则 `^\./\d{6}/t\d{8}_\d+\.htm$`（D1 验证的相对路径模式；曾因照搬"含/zbgg/"过滤条件解析出 0 条——D1 第二轮分析才发现链接不含栏目字样） |
| `ListItem` | dataclass | 列表条目：url / title / publish_time / region / purchaser_from_list（列表页 `<li>` 自带后三项元数据，D1 意外收获） |
| `parse_list(html)` | `-> list[ListItem]` | 列表页 HTML → 条目。`<li>` 内 4 个 `<em>` 依次为时间/地域/采购人；相对 URL `_absolute_url` 拼绝对路径 |
| `crawl_list_page(page)` | `-> list[ListItem]` | 抓第 page 页（0=index.htm, N=index_N.htm）并解析；解析出 0 条抛异常（页面结构变更预警） |
| `crawl_detail(url)` | `-> str` | 抓详情页 → `parse_detail` 取 `div.vF_detail_main` 正文纯文本（压空白；D1 验证金额为表格布局含大量空白） |

#### `extract.py` — 正文字段提取（D4，三层策略）

| 成员 | 签名 | 说明 |
|------|------|------|
| `KEY_ALIASES` | 常量 | 键行别名 → 统一字段名（supplier/amount/project_name/purchaser/region/announce_time/project_id） |
| `_SUPPLIER_SUFFIX_RE` | 常量 | 供应商值行判定：企业后缀结尾（有限公司/集团/中心/医院…）。**为什么不用表头词表**：扁平化表格的列名不完备（"评审方法/是否价格扣除"…穷举不完），正则一劳永逸 |
| `_TOTAL_PRICE_RE` | 常量 | 表格金额 `总价：X（元）`——"单价：A（元），总价：B（元）"必须取总价 |
| `ExtractionResult` | dataclass | fields（原始值）/ amount_normalized（元）/ sources（字段→table_zone/kv_zone/inline）/ missing（needs_review） |
| `FieldExtractor.extract(body)` | `-> ExtractionResult` | 主入口，三层按优先级：供应商=表格区→键值→行内；金额=概要总金额→表格总价→键值（**概要 ￥0 万元时回退表格区**——分标/按实结算类公告概要常为0）；每字段命中即止并记录来源 |
| `._parse_table_zone(lines)` | `-> dict` | 定位"三、中标（成交）信息"区段 → 找表头"中标供应商名称"/"中标（成交）金额" → 其后 10 行内找企业后缀行/总价格式行 |
| `._scan_kv(lines)` | `-> dict` | 键值对扫描。键行经 `_canonical_key` 归一化（剥"一、/1."序号前缀与尾冒号）；**supplier 的值行用企业后缀正则判定（窗口15行）**，其他字段取键后 3 行内首个非键/表头/纯数字行 |
| `._inline_value(lines, field)` | `-> str \| None` | 行内"键：值"写法（如"供应商名称：XX公司"） |
| `._canonical_key(line)` | `-> str` | 键归一化（"一、项目编号：" → "项目编号"） |

**50 条真实验收**：supplier 94% / amount 98% / project_name·purchaser·region·project_id 98%（方案验收线 ≥90%）。
来源分布 kv_zone 302 / table_zone 35 / inline 11——键值对扫描是主力，表格区处理复杂公告，冒号行兜底。

#### `storage.py` — 原始数据存储

| 成员 | 签名 | 说明 |
|------|------|------|
| `RawAnnouncement` | dataclass | 原始落地形态：url / title / publish_time / region / purchaser_from_list / body_text / crawled_at |
| `RawStore.is_seen(url)` / `.unseen(items)` | — | 增量去重（seen_urls.json 持久化，进程重启后仍生效——断点续爬） |
| `.save(raw)` | — | JSONL 追加 + 登记 URL（同 URL 幂等） |
| `.count()` / `.load_all()` | — | 库存统计 / 读回全部（parse_raw 解析用） |

**设计决策**：爬取只存原文（最小化在线时间、降低被封风险），解析纯本地——提取规则迭代后
对同一 JSONL 重跑 parse_raw 即可，**不重爬**。

### 2.3 向量检索模块 `src/vector_search/`

#### `embedder.py` — 向量化（双实现）

| 成员 | 签名 | 说明 |
|------|------|------|
| `BaseEmbedder(ABC)` | `embed(texts) -> list[vec]` | 抽象接口 + `name` 属性 |
| `ZhipuEmbedder` | `embed -> 向量` | 智谱 embedding-3（2048维）。64条/批分批，指数退避重试3次；API key 从 .env 延迟加载 |
| `HashingEmbedder` | `embed -> 向量` | 离线兜底：字符2-gram特征哈希 + L2归一化（512维）。语义弱但零依赖、确定性——测试全用它，不花钱不联网 |
| `cosine(a, b)` | `-> float` | 余弦相似度（离线验证用） |

#### `search.py` — ChromaDB 索引与检索

| 函数/方法 | 签名 | 说明 |
|------|------|------|
| `project_text(row)` | `-> str` | 拼被检索文本：标题；行业；技术标签；摘要 |
| `ProjectVectorIndex.__init__` | `(embedder, db_path)` | PersistentClient + "projects" collection（cosine 空间） |
| `.build(df)` | `-> int` | 全量 upsert（id=project_id，与图谱 project_key 一致），返回索引条数 |
| `._row_id(row, idx)` | `-> str` | id 生成：编号优先 → 标题哈希兜底（与图谱对齐，两侧可关联） |
| `._row_meta(row)` | `-> dict` | 冗余元数据（title/company/group/industry/province/city/amount/date）供过滤 |
| `.search(query, top_k, industry, province, min_amount)` | `-> list[dict]` | 语义搜索+元数据过滤。**关键坑**：必须先 `embedder.embed([query])` 再传 `query_embeddings`——若传 `query_texts`，Chroma 会用内置 ONNX 模型（384维）给查询向量化，与存的智谱2048维不匹配直接报错，还会悄悄下载79MB模型 |
| `._build_where(...)` | `-> dict \| None` | 组装 Chroma where 过滤条件（多条件 $and） |

### 2.4 Agent 模块 `src/agent/`

#### `llm.py` — LLM 客户端

| 成员 | 签名 | 说明 |
|------|------|------|
| `LLMClient.__init__` | `(model="glm-4-flash", api_key, base_url)` | OpenAI 兼容 SDK 连智谱（base_url 从 .env 读） |
| `.chat(messages, tools, temperature, max_tokens)` | `-> dict` | 统一返回 `{"content": str\|None, "tool_calls": [{id, function:{name, arguments}}] \| None}`——纯 dict 屏蔽 SDK 类型，测试替身可无缝替换。3次指数退避重试 |
| `._clean_content(content)` | `-> str \| None` | 过滤智谱 API 偶发拼接在思考文本尾部的流式 chunk JSON（含 `"finish_reason"/"delta"` 特征即从 `{"index"` 处截断）。这是真实踩到的 API 怪癖 |

#### `prompts.py` — 提示词

| 常量 | 说明 |
|------|------|
| `SYSTEM_PROMPT` | 直接工具调用工作方法（**刻意不含 Thought/Action/Observation 字样**——IT-014 教训：协议词汇的文本教学会诱导 glm-4-flash 用文本模拟工具执行而非真实调用）+ 5个工具的选择指引 + 回答要求（数字必须来自工具、数据不足明说）+ 数据背景说明。docstring 记录此教训防回退 |
| `FINAL_ANSWER_NUDGE` | 达到最大步数后强制收尾的提示（"不要再调用任何工具"） |
| `SIMULATED_ANSWER_NUDGE` | 幻觉纠正消息：检测到模拟式回答后注入，要求真正调用工具（IT-014 修复新增） |

#### `react.py` — 手写 ReAct 循环（行为基准）

> 2026-10-08 起 Agent 生产实现切换为 LangGraph（见下方 graph_agent.py）。
> 本文件保留：① `looks_simulated` / `AgentResult` 被两个实现共用；② 作为
> LangGraph 版的行为基准——同一 FakeLLM 脚本在两个实现下应产出一致结果。

| 成员 | 签名 | 说明 |
|------|------|------|
| `looks_simulated(answer)` | `-> bool` | **幻觉防御检测器**（IT-014）：正则识别 Thought:/Action:/Observation:/最终答案: 等模拟特征 |
| `AgentResult` | dataclass | answer（最终回答）/ trace（每步 {thought, tool_calls, observation}）/ steps / **used_tools**（是否经过真实工具查询，Web 可信度提示用）/ **retried_for_simulation**；`trace_summary()` 渲染人类可读推理过程（Web展示用） |
| `ReActAgent.__init__` | `(llm, registry, max_steps=8, system_prompt, verbose)` | llm 只要求实现 `SupportsChat` 协议（duck typing） |
| `.run(question, history=None)` | `-> AgentResult` | 循环：chat → 有 tool_calls 则执行并回填 tool 消息 → 直到无 tool_calls 返回回答；超 max_steps 追加 NUDGE 强制收尾。**幻觉防御**：回答带模拟特征且 used_tools=False → 注入幻觉回答 + 纠正消息自动重试一次。最终回答那条 trace 的 observation 是 None（无工具调用），展示层要判空（这里曾崩过一次） |

#### `graph_agent.py` — LangGraph Agent（生产实现，2026-10-08）

与 `react.py` 行为对齐的 LangGraph StateGraph 实现，对外接口完全一致
（`run(question, history) -> AgentResult`），Web / run_agent.py 无缝切换。

```
START → agent（LLM 推理）
          ├─ 有 tool_calls → tools（ToolNode 执行）→ 回 agent
          └─ 无 tool_calls → 条件边 route_after_agent：
                ├─ 模拟特征 且 未用工具 且 未重试过 → nudge → 回 agent（幻觉防御）
                └─ 正常 → END
```

| 成员 | 签名 | 说明 |
|------|------|------|
| `registry_to_base_tools(registry)` | `-> list[BaseTool]` | 把 ToolRegistry 每个工具动态包成 langchain BaseTool：`create_model` 从 JSON Schema 生成 pydantic 参数模型（**required 语义逐字段对齐**，可选字段 `ann \| None`——IT-017 教训）；`_run` 委托 `registry.call`，工具逻辑仍是 tools.py 单一来源 |
| `LangGraphAgent.__init__` | `(llm, registry, max_steps=8, system_prompt, verbose)` | llm 仍走 `SupportsChat` 协议（`RunnableLambda` 包装成 langchain Runnable），FakeLLM 测试替身通用 |
| `._chat_via_protocol(messages)` | `-> AIMessage` | langchain 消息 → `_lc_to_dict` → `llm.chat` → dict → `AIMessage(tool_calls=...)`。**框架消息对象与 API dict 的转换边界**，是整个适配层的关键点 |
| `._build_graph()` | `-> CompiledStateGraph` | agent/tools/nudge 三节点 + `add_conditional_edges` 幻觉防御条件边。重试轮次也自动带 system prompt（图内注入） |
| `.run(question, history)` | `-> AgentResult` | 消息拼装 → `graph.invoke`（recursion_limit = max_steps*2+6）→ `_extract` 还原 trace 结构 |
| `_lc_to_dict(m)` | `-> dict` | langchain 消息 → OpenAI role dict。**显式映射**（SystemMessage→system / AIMessage→assistant / ToolMessage→tool / HumanMessage→user），不信任 `m.type`（IT-017：`human`/`ai` 直传智谱报 400 "角色信息不正确"） |
| `._extract(messages)` | `-> (answer, trace, used_tools)` | 把消息序列还原成与手写版一致的 trace 列表（Web 展示层零改动） |

#### `conversation.py` — 对话历史

| 方法 | 说明 |
|------|------|
| `add(role, content)` | 追加消息 + 裁剪 |
| `add_exchange(q, a)` | 记录一轮完整问答（下轮作为上下文） |
| `as_list()` | 输出消息列表 |
| `_trim()` | 超限时裁剪，且**按完整问答对切**（偶数对齐，不会切出孤儿 user 消息） |

#### `protocols.py`

`SupportsChat(Protocol)` — LLM 的结构化 duck typing 声明；有测试验证真实 LLMClient 和 FakeLLM 都满足该协议。

### 2.5 工具层 `src/mcp_tools/`

#### `registry.py` — 工具注册表（Agent 进程内用）

| 成员 | 签名 | 说明 |
|------|------|------|
| `Tool` | dataclass | name / description / parameters(JSON Schema) / func；`schema()` 转 OpenAI function calling 格式 |
| `ToolRegistry.register(name, desc, params)` | 装饰器 | 注册工具函数 |
| `.schemas()` | `-> list[dict]` | 全部工具的 function calling schema |
| `.call(name, arguments)` | `-> str`（JSON串） | **绝不向上抛异常**：未知工具/坏JSON/参数不匹配/执行失败 全部转成 `{"error": ...}` 返回（Agent 循环的稳定性兜底）。结果序列化后截断到 4000 字符防撑爆上下文 |

#### `tools.py` — 5个工具的业务实现

数据加载器（模块级缓存，进程内只初始化一次）：

| 函数 | 说明 |
|------|------|
| `_load_df()` | 读阶段1 parquet |
| `_load_graph()` | NetworkXStore.load 图谱 JSON |
| `_load_index()` | 智谱 Embedding 探活，失败降级 HashingEmbedder，返回 ProjectVectorIndex |

5个工具函数（模块级，与 MCP Server 共用同一实现）：

| 工具 | 输入 | 输出 | 说明 |
|------|------|------|------|
| `search_projects` | industry/province/keyword/min_amount/max_amount/top_k | {total_matched, projects:[{title,company,group,amount,date,industry,region}]} | parquet 精确条件筛选，按金额降序 |
| `company_profile_lookup` | company | 公司或集团画像 dict | 三级解析（V2.0 升级 `_resolve_group`）：canonical 精确命中 → 集团解析（子串包含 → **字符集包含唯一命中**，解决"中国联通"简称 ⊆ "中国联合网络通信"）→ 公司名模糊匹配 |
| `graph_query` | query_type(competitors/industry_top/tech_trends/region_overview/group_aggregation) + 参数 | 各查询结果 | 图谱关系查询统一入口。V2.0：competitors 分支补齐集团简称回退（此前"中国联通"查竞对返回空——IT-016） |
| `semantic_search` | query/top_k/industry | {results:[{title,similarity,company,amount,industry}]} | 阶段3向量检索透传 |
| `industry_stats` | group_by(industry/province/group_name)/top_k | {dimension, stats:[{name,project_count,total_amount}]} | parquet 分组聚合 |
| `_resolve_group(name, df)` | `-> str \| None` | （辅助）简称 → 集团名：两级匹配，唯一命中才返回，多命中/零命中返回 None 不猜 |

`TOOL_DEFS` 集中定义 5 个工具的 JSON Schema（Agent 和 MCP 共用，防止两套描述漂移）；`build_registry()` 按 TOOL_DEFS 组装 ToolRegistry。

#### `server.py` — MCP Server（协议薄包装）

`FastMCP("bidding-agent")` 实例，5个 `@mcp.tool()` 包装函数逐个透传到 tools.py 的业务函数，结果统一 `_json()` 序列化（default=str 兜日期）。`main()` 以 stdio 传输运行。
配套入口 `scripts/run_mcp_server.py` 启动前 `logger.remove()`——**MCP 走 stdio 时 stderr 就是协议通道，日志字节会污染协议流**。

### 2.6 Web 模块 `src/web/`

| 文件 | 说明 |
|------|------|
| `common.py` | `load_df()/load_graph()`（st.cache_data/cache_resource 缓存）、`load_agent()`（失败返回异常对象，页面判型给提示）、`require_data_ready()`（产物缺失时渲染引导并 st.stop）、`headline_metrics()/top_groups()/tech_trend()`、`TYPE_COLORS`（5类节点配色） |
| `app.py` | 首页：6项核心指标 + 页面导航链接 + 数据说明 |
| `pages/1_数据概览.py` | Plotly 图表：行业饼图/金额条形、地区双图、时间趋势线、金额直方、技术标签TOP15、中标明细表 |
| `pages/2_知识图谱.py` | 侧边栏选公司/行业视角 + 深度滑杆(1-3跳) → `GraphStore.subgraph()` → Pyvis 渲染（节点按类型着色、项目按金额定大小、悬停显示属性），下方节点明细表。用 `components.html` 嵌入（pyvis 完整HTML文档需要 iframe 隔离，`st.iframe` 只收 URL 不适用） |
| `pages/3_Agent对话.py` | st.chat 界面 + 示例问题按钮 + 会话状态（chat_messages/conversation/traces）+ 推理过程 expander。Agent 初始化失败（无 API Key）时优雅降级提示 |

每个页面文件顶部都有 `sys.path.insert(0, 项目根)`——Streamlit 页面按独立脚本执行，不继承 app.py 的 path。

### 2.7 脚本 `scripts/`

| 脚本 | 功能 |
|------|------|
| `verify_setup.py` | 四项检查：目录文件 / .venv 依赖 / API Key 配置 / 各阶段产物是否就绪（缺产物时提示重建顺序） |
| `crawl_ccgp.py` | **V2.0 爬取入口**：`--pages N`（列表页数）/ `--limit N`（单次详情上限）/ `--max-requests 200`（红线）/ `--list-only`（摸底）。预算耗尽/熔断时安全收工，下次自动续爬 |
| `parse_raw.py` | **V2.0 转换入口**：原始 JSONL → V1.0 schema 12列。省份归一化（"浙江省"→"浙江"）、行业规则粗分类（9类关键词兜底"其他"）、去重（project_id 归一化 + 标题相似 0.92）、产出 extract_stats.json 质量统计 |
| `run_stage1.py` | 阶段1入口：**双数据源合并**（种子CSV + 爬虫CSV）→ DataCleaner → normalize_dataframe → EntityAligner（V2.0 已开 L3）→ merge → parquet + entity_mapping.csv + **质量仪表盘**（核心指标 vs 上次快照 stage1_metrics.json、对齐方法分布、needs_review 数、爬虫提取质量区块） |
| `golden_set.py` | **V2.0 基准集工具**：`--make --sample N` 生成预填机器结果的标注模板（is_correct + correct_value 列）；`--eval` 对比人工标注输出各字段准确率（验收线 ≥90%） |
| `build_graph.py` | 阶段2入口：读 parquet → build_graph → save JSON → 打印统计（集团TOP5/行业排行/技术热度） |
| `build_vector_index.py` | 阶段3入口：`pick_embedder()`（智谱探活失败自动降级离线）→ 建 ChromaDB → 3个演示搜索 |
| `run_agent.py` | Agent 命令行：`-q` 单问 / 默认3演示 / `--full` 10验收问题，带 Conversation 多轮 |
| `run_mcp_server.py` | MCP stdio 启动（关日志防协议污染） |

### 2.8 测试 `tests/`（101个，全部离线零 API 消耗）

| 文件 | 数量 | 覆盖点 |
|------|------|--------|
| `test_stage1.py` | 28 | 金额/日期/标签标准化参数化用例；清洗（缺必填/project_id去重/金额越界）；实体对齐**回归用例**（电信分公司归并、联通不丢字、迭代剥后缀、确定性） |
| `test_stage2.py` | 13 | store CRUD/边合并/未建节点报错；竞对推导只连同行业；各查询正确性；save/load round-trip；接口契约；subgraph 结构（含 depth=1/2 邻居范围） |
| `test_stage3.py` | 13 | HashingEmbedder 确定性/相似性；索引幂等；语义相关性排序；元数据过滤（单/组合/空结果）；空索引；project id 哈希兜底稳定 |
| `test_stage4.py` | 21 | registry 四类错误路径/截断；FakeLLM 驱动的 ReAct 循环（单工具/多工具/超步数收尾/直接回答/system prompt 注入）；**幻觉防御回归**（IT-014：模拟回答检测器/纠正重试消息结构/只重试一次/不误触发正常路径）；对话裁剪对齐；工具集成（mini数据 + monkeypatch 缓存） |
| `test_stage5.py` | 7 | MCP 内存会话协议级测试：list_tools 列全5个、逐工具 call_tool 结果断言、错误优雅处理 |
| `test_crawler.py` | 12 | **V2.0**：列表页解析（fixture 快照回归——曾抓出 parse_list 照搬错误过滤条件的 bug）；详情正文提取；RawStore 落地/幂等/断点续爬；PoliteSession 403 不重试/重试后成功/连续失败熔断；限速参数与方案红线一致性 |
| `test_extract.py` | 7 | **V2.0**：表格型/键值型真实样本回归；冒号行内写法；金额三种变体归一化；全无关文本不猜测（missing 标记）；连续表头行跳过 |

测试数据策略：测试一律用 `_mini_df()` 迷你夹具 + monkeypatch 替换 `tools._cache`，与真实数据文件、真实 API 完全隔离；爬虫解析用 `tests/fixtures/` 里的真实页面快照；网络层用 StubResponse。真实 API 只在 `run_agent.py`/`build_vector_index.py`/`crawl_ccgp.py` 运行时人工验证。

---

## 三、开发过程回顾（按时间线 + 踩坑记录）

### 阶段0：环境（踩坑最多的一天半）

1. **全局环境遮蔽**：公司 shell 预设 `PYTHONPATH=/opt/python-venv/...`，优先级高于 `.venv`，装在 .venv 的包被全局同名旧包覆盖（pandas/langgraph 悄悄用了全局版）。全局那个 langgraph 还是半残包（能 import 无版本号）。解法：`run.sh` 运行时 `env -u PYTHONPATH`。
2. **pip 违规**：早期用 `.venv/bin/pip` 装过包（违反公司 uv-only 规定），整个 `.venv` 删掉用 `uv venv` 重建（uv 创建的 venv 不含 pip，物理杜绝再犯）。
3. **教训**：验证包归属要看 `spec.origin`，不能只看能否 import（namespace package 的 `__file__` 是 None，会误报）。

### 阶段1：数据处理

4. **正则误吞"有限公司"**：分公司名正则里城市用 `[一-龥]{2,8}`，非贪婪 core 提前截断，"有限公司银川"整体被当城市名，"中国联合网络通信"丢字。解法：放弃正则，确定性字符串操作（定位最后一个"公司"）。
5. **后缀剥离不迭代**："中国电信集团有限公司"剥一层停 → "中国电信集团"，与"股份有限公司"写法（→"中国电信"）合并不了。解法：循环剥离。两个坑都固化为回归测试。
6. **dataclass 可变默认值**：`field: list = []` 直接 ValueError，要用 `default_factory`。

### 阶段2：知识图谱

7. **parquet 回读列表变 numpy array**：`array or []` 触发歧义异常。这是阶段2、3各踩一次的同类坑。
8. **MultiDiGraph edges 解包**：`edges(keys=True)` 是3元组，加 `data=True` 才是4元组。

### 阶段3：向量检索

9. **ChromaDB 查询维度不匹配**：`query(query_texts=...)` 时 Chroma 用内置 ONNX 模型（384维）向量化查询，与存的智谱2048维冲突，还顺手下载了79MB模型（慢到超时）。解法：查询向量由同一 embedder 生成后传 `query_embeddings`。这是"embedding 一致性"原则的经典案例。
10. **兜底 id 漂移**：向量索引原本行号兜底（row-0），图谱用标题哈希，同一项目两侧 id 对不上。统一为标题哈希。

### 阶段4：Agent

11. **集团名提问只回单分公司**：问"中国电信"命中了太原分公司画像。解法：新增 `group_profile` 集团聚合，工具层做三级解析（精确→集团→模糊）。
12. **trace 打印崩溃**：报错在最后一步的展示层（`observation=None` 切片），Agent 本体早就跑通了——教训：报错位置≠根因位置。
13. **智谱 API 怪癖**：思考文本尾部偶发拼接原始流式 chunk JSON。先按"以 { 开头"判断失败（拼接在尾部），改为特征标记截断。

### 阶段5：MCP

14. **stdio 协议污染**：loguru 往 stderr 写日志会破坏 MCP 协议流，server 启动时 `logger.remove()`。

### 阶段6：Web

15. **Streamlit 弃用 API**：`use_container_width` 已宣布移除期限，批量替换 `width='stretch'`。

### V2.0：爬虫与数据清洗强化（2026-09-16 ~ 09-17）

16. **WAF 对请求头敏感**：裸 curl（无 UA）直接 403 拦截（拦截页暴露公司出口 IP），带完整浏览器头后一切正常。解法：`BROWSER_HEADERS` 常量固化进 PoliteSession。**合规红线全部代码化**：5-8s 随机间隔、每10请求长休息、单次上限200、403 即熔断当日停止。
17. **D1 结论照搬出错**：D1 第一轮用"链接含 /zbgg/"过滤列表链接得到 0 条，第二轮才发现真实链接是 `./202609/tXXX.htm` 相对路径（不含栏目字样）；写 parse_list 时又把第一轮的错误条件搬了进去——**fixture 测试第一时间抓住**。教训：试探结论要经过二次确认才能当实现依据。
18. **扁平化表格列名不可穷举**：爬来的公告是表格压扁成"表头行+数据行"，表头词（评审方法/是否价格扣除…）无法穷举，键值扫描取到"评审方法"当供应商。解法：供应商值行用**企业后缀正则**判定（窗口15行），不维护词表。
19. **`_first_of` 来源标签错位**：把字面量 "table_zone" 当候选值传入，source 永远错误。重构为 `_pick((值, 标签), ...)` 配对传参。
20. **编码误判乱码**（IT-015）：chardet 的 apparent_encoding 在个别 ccgp 页面误判，正文变俄文乱码、提取全失败。解法：meta charset 声明优先 + utf-8/gbk 回退 + 乱码启发式检查；乱码条目删除重爬验证。
21. **两个模块各自正确、组合后互相矛盾**：爬虫数据的 city 全空（ccgp 行政区域只到省级），而 V1.0 把 city 列为必填 → 48 条爬虫数据在合并清洗时**全部被剔除**。解法：parse_raw 用省名填充 city。教训：管道拼接处要做端到端验证，单元正确 ≠ 组合正确。
22. **分标公告概要金额为 ￥0 万元**：真实写法（按实结算），真实金额在表格区。解法：金额归一后为 0 且表格区有值 → 回退。
23. **集团简称解析**（IT-016）：Agent 验收第10问"中国联通的竞争对手"返回空——competitors 分支无模糊回退，且"中国联通"是"中国联合网络通信"的简称（二字不相邻，子串匹配也漏）。解法：`_resolve_group()` 两级解析（子串包含 → 字符集包含唯一命中）；顺带发现 group_profile 聚合竞对时丢弃 shared_industries（聚合信息有损），一并修复。
24. **幻觉式回答复发防线**（IT-014，V1.0 遗留问题在 V2.0 期间集中爆发修复）：system prompt 里的 `Thought/Action/Observation` 文本格式教学把 glm-4-flash"教坏"——模型在回复文本里**演**工具调用（编造 XX公司），而非真正发起 function calling。诊断五连：复现×3 → 裸调排除SDK → 换 prompt 定位 → 换模型对照 → 改写 prompt 验证 5/5。修复三层：重写 prompt（删除协议词汇文本教学）+ react.py 幻觉检测自动纠正重试（looks_simulated + SIMULATED_ANSWER_NUDGE）+ Web 未走工具回答显示警告条。**教训：LLM 行为验收必须多次采样（≥5次），单次通过是概率性假阳性。**

### V2.1 LangGraph 迁移（2026-10-08）

25. **Agent 升级 LangGraph**：早期设计稿曾规划 LangGraph 工作流编排，V1.0 实际落地为手写 ReAct 循环（简单场景够用 + 全局 PYTHONPATH 有半残 langgraph 旧包）。为补齐简历技能项，本期将 Agent 层升级为 LangGraph StateGraph 实现（graph_agent.py）：agent/tools/nudge 三节点 + 条件边做幻觉防御（nudge 是框架原生写法，比手写版的"run 尾部重试"更声明式）；ToolRegistry 的 JSON Schema 经 `create_model` 动态生成 pydantic 模型包成 BaseTool，工具逻辑保持 tools.py 单一来源；对外接口与手写版完全一致，Web/run_agent 一行切换。手写版 react.py 保留作行为基准——同一 FakeLLM 脚本两个实现结果一致，这也成了简历上的"框架 vs 手写权衡"叙事素材。
26. **框架边界两坑**（IT-017）：① langchain 消息 `m.type` 是 `human`/`ai`，直传智谱 API 报 400 "角色信息不正确"——框架消息对象 ↔ HTTP API dict 必须显式词汇映射，"看着一样"的字段最容易埋雷；② `create_model` 动态建参数模型时所有字段被标成必填，ToolNode 校验直接拒绝合法调用——required 语义必须逐字段对齐源 schema。另发现 `llm.chat` 的三连重试把 400 BadRequest 伪装成 RuntimeError，真实异常被吞，排查走了弯路（重试逻辑应区分可重试/不可重试错误，已记入备忘）。

---

## 四、实现与 README 的已知差异

| 项 | README 原设计 | 实际实现 | 原因 |
|----|--------------|----------|------|
| 结构化存储 | SQLite | 直接读 parquet | 58条数据不值得引库；工具签名不变，继续扩量再切 |
| 图存储 | Neo4j（最终目标） | networkx + JSON | 远程环境跑不了数据库服务；GraphStore 接口已抽象，切换成本低 |
| 实体对齐 L3 | GLM 复核 | **V2.0 已开启**（灰区 7 次实测全部正确） | V1.0 时 18 条规则层已够；V2.0 数据量上来后打开并实测 |
| 向量化 | 智谱 Embedding | 智谱 + Hashing 降级双实现 | 测试要离线确定性；网络故障时管线不中断 |
| 爬虫技术栈 | DrissionPage + requests | **仅 requests + BeautifulSoup** | D1 验证：静态 HTML + 浏览器请求头即可，无需浏览器自动化 |

## 五、V2.0 后续备忘（P1/P2）

- **P1（下一期）**：附件/PDF 金额提取（对应人工采集时跳过的两类公告，pdfplumber）；LLM 长尾兜底（对 needs_review 正文调 GLM 提取）
- **Agent 侧待办**：`llm.py` 重试逻辑区分可重试（网络/限流）与不可重试（4xx）错误；LangGraph `MemorySaver` checkpointer 替代手写 Conversation（多轮对话持久化的框架原生方案）
- **扩量**：重跑 `crawl_ccgp.py` 即可增量扩充（红线参数已固化，500+ 条预计分 2-3 天）
- `Neo4jStore` 实现（GraphStore 接口已就绪，实现同接口 + 过 test_stage2 同套测试即可切换）
- SQLite 迁移（数据量上千后）
- 图算法增强（社区发现/中心度，networkx 原生支持，切换 Neo4j 后可用 GDS）
- 行业分类优化：当前规则粗分类"其他"占 43%（25/58），可引入 LLM 标注或更细关键词库
