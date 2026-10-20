# 招投标智能分析 Agent

基于中国政府采购网公开数据，构建招投标领域知识图谱，通过自然语言 Agent 实现商业情报分析、竞对监控、商机发现。

## 项目简介

政府采购中标公告蕴含丰富的商业情报：谁在哪个行业、哪个地区中标了什么项目、金额多少。本项目将这些非结构化公告转化为结构化数据，进一步构建「公司-项目-行业-技术」知识图谱，并提供自然语言查询入口——用户可以直接问 *「华为在智慧城市领域的中标情况如何？」*，由 Agent 自主推理、调用工具、组合数据后给出分析结论。

**核心亮点：**
- 真实数据：全部来自中国政府采购网公开中标公告（非虚构数据）
- 完整链路：数据清洗 → 实体对齐 → 知识图谱 → 向量检索 → Agent 问答 → Web 可视化
- 复杂数据处理：多格式标准化、公司名实体对齐（同一公司存在多种写法）
- 幻觉防御：Agent 回答强制基于工具返回的真实数据，检测到"模拟工具执行"自动纠正重试

## 技术栈

| 层次 | 技术 | 说明 |
|------|------|------|
| 数据处理 | Pandas, jieba | 清洗、标准化、分词 |
| LLM | 智谱AI GLM (OpenAI兼容接口) | 实体对齐辅助、Agent推理 |
| Agent 框架 | LangGraph (StateGraph) | ReAct 循环编排、条件边幻觉防御 |
| 知识图谱 | networkx + JSON持久化 | 图存储与关系查询（抽象接口，可切换Neo4j） |
| 向量检索 | ChromaDB | 项目语义搜索 |
| 工具协议 | MCP (FastMCP) | 工具标准化封装，可对外暴露给 MCP 客户端 |
| Web | Streamlit + Plotly + Pyvis | 界面与图谱可视化 |
| 测试 | pytest + GitHub Actions CI | 115 个离线测试用例，推送自动验证 |

## 系统架构

```
┌────────────────────────────────────────────────────────┐
│              Web 展示层 (Streamlit)                     │
│     数据概览 │ 图谱可视化 │ Agent 对话                  │
└────────────────────────┬───────────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────────┐
│         Agent 层 (LangGraph StateGraph + GLM)          │
│      问题分析 → 工具调用 → 结果观察 → 综合回答          │
└────────────────────────┬───────────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────────┐
│              工具层 (ToolRegistry / MCP)                │
│  search_projects │ graph_query │ semantic_search │ ...  │
└───────┬──────────────────┬─────────────────────────────┘
        │                  │
┌───────▼──────┐  ┌────────▼───────┐
│  networkx    │  │   ChromaDB     │
│  知识图谱    │  │   向量库        │
│ (接口抽象，  │  │                │
│ 可切换Neo4j) │  │                │
└───────▲──────┘  └────────▲───────┘
        │                  │
┌───────┴──────────────────┴──────────────────────────────┐
│              数据处理层                                  │
│   清洗去重 → 格式标准化 → 实体对齐（规则+LLM）           │
└────────────────────────▲───────────────────────────────┘
                         │
┌────────────────────────┴───────────────────────────────┐
│      数据源：中国政府采购网（种子数据 + 自动化爬虫）      │
└────────────────────────────────────────────────────────┘
```

## 模块划分

### 数据处理 `src/data_processing/`

将原始公告数据加工为高质量结构化数据，是全项目的数据质量基石。

| 文件 | 职责 |
|------|------|
| `cleaner.py` | 去重、缺失值处理、异常值检测 |
| `normalizer.py` | 金额/日期/公司名格式标准化 |
| `entity_aligner.py` | 公司名实体对齐（核心难点：同一公司多种写法归并） |

**实体对齐分层策略：** L1 后缀剥离规则匹配 → L2 编辑距离+Jaccard 模糊匹配 → L3 LLM 辅助判断 → L4 人工映射表兜底。

### 知识图谱 `src/knowledge_graph/`

建模并构建「公司-项目-行业-技术」图谱，支持关系查询。

```
节点：Company / Project / Industry / Technology / Region
关系：
  (:Company)-[:WON {amount, date}]->(:Project)              中标
  (:Project)-[:BELONGS_TO]->(:Industry)                     行业归属
  (:Project)-[:REQUIRES]->(:Technology)                     技术需求
  (:Project)-[:LOCATED_IN]->(:Region)                       地域分布
  (:Company)-[:COMPETES_WITH {shared_projects}]->(:Company) 竞对（同行业共同投标推导）
```

存储层通过 `GraphStore` 抽象接口隔离：当前为 networkx + JSON 持久化实现，切换 Neo4j 只需新增同接口实现类，上层代码零改动。

### 向量检索 `src/vector_search/`

项目文本语义向量化（智谱 Embedding API），支持模糊语义搜索——搜索「智慧校园」能召回标题不含该词但语义相关的教育信息化项目。

### 工具层 `src/mcp_tools/`

将底层能力封装为标准化工具，供 Agent 调用，同时通过 MCP 协议（FastMCP）对外暴露。

| 工具 | 功能 |
|------|------|
| `search_projects` | 按行业/地区/金额区间/关键词筛选项目 |
| `company_profile` | 公司画像：中标历史、总金额、行业分布（支持集团聚合与简称模糊匹配） |
| `graph_query` | 竞对分析、合作关系、技术关联 |
| `semantic_search` | 语义搜索相似项目 |
| `industry_stats` | 行业/地区/时间维度统计分析 |

### Agent 核心 `src/agent/`

理解自然语言问题，自主规划、调用工具、综合数据回答。

| 文件 | 职责 |
|------|------|
| `graph_agent.py` | **LangGraph Agent**（StateGraph：agent→tools 循环 + 幻觉防御条件边） |
| `react.py` | 手写 ReAct 循环（LangGraph 版的行为基准） |
| `llm.py` | LLM 客户端封装（区分可重试/不可重试错误的退避策略） |
| `prompts.py` | 提示词模板 |
| `conversation.py` | 多轮对话历史管理 |

**幻觉防御机制：** 条件边检测最终回答——若回答带"模拟工具执行"特征且未真正调用过工具，注入纠正消息重试，强制 Agent 基于真实工具数据回答。

### Web 界面 `src/web/`

| 页面 | 功能 |
|------|------|
| 数据概览 | 行业/地区/时间维度统计图表 |
| 知识图谱 | Pyvis 交互式图谱可视化，支持按公司/行业过滤、节点展开 |
| Agent 对话 | 聊天界面，展示 Agent 思考过程与工具调用记录 |

### 爬虫 `src/crawler/`

中国政府采购网自动化采集（保守低速：5-8秒间隔、单日限量、WAF熔断保护），增量去重，正文三层策略字段提取（表格区/键值对/行内冒号），字段命中率 ≥94%。

## 快速开始

```bash
# 1. 克隆并创建虚拟环境
git clone https://github.com/VaiduryaG/tender-insight-agent.git
cd tender-insight-agent
python -m venv .venv
.venv\Scripts\activate        # Windows（Linux/macOS: source .venv/bin/activate）
pip install -r requirements.txt

# 2. 配置 API Key
cp .env.example .env          # 编辑 .env，填入智谱AI API Key

# 3. 生成数据（读取种子数据，依次生成处理后数据/图谱/向量索引）
python scripts/run_stage1.py
python scripts/build_graph.py
python scripts/build_vector_index.py

# 4. 运行测试 / 启动 Web
python -m pytest tests/ -q
python -m streamlit run src/web/app.py   # 浏览器打开 http://localhost:8501
```

> Linux 服务器环境可使用 `./run.sh` 代替 `python`（自动屏蔽全局 PYTHONPATH 干扰，确保使用 .venv 依赖）。

## 数据来源

中国政府采购网（www.ccgp.gov.cn）公开中标公告：18 条人工采集种子数据 + 爬虫自动扩充，覆盖智慧教育、智能交通、智慧医疗、政务监管等行业。

## License

MIT
