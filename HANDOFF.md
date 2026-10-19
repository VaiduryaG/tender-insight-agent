# 项目交接备忘（供压缩上下文后继续工作）

> 更新时间：2026-10-19。本文写给 Claude：压缩上下文后先读本文 + CLAUDE.md + README.md，即可无损接续工作。

---

## 一、项目当前状态（一句话）

招投标智能分析 Agent，**V1.0 + V2.0 双版本完成，Agent 已于 2026-10-08 升级 LangGraph，2026-10-19 完成 LLM 重试优化与行业分类优化**，115 个测试全通过，全链路可演示、可写简历。

## 二、必须知道的事实

### 技术栈（实际在用）

| 层 | 技术 |
|----|------|
| 数据处理 | Pandas + jieba，产物 parquet（无 SQLite/PostgreSQL，58 条数据不值得引库） |
| 知识图谱 | networkx + JSON 持久化（GraphStore 抽象接口，Neo4jStore 未实现，远程环境跑不了数据库服务） |
| 向量检索 | ChromaDB + 智谱 embedding-3（测试用 HashingEmbedder 离线兜底） |
| **Agent** | **LangGraph StateGraph（生产实现 `src/agent/graph_agent.py`）**；手写 ReAct 循环（`react.py`）保留作行为基准，`looks_simulated`/`AgentResult` 两实现共用 |
| LLM | 智谱 glm-4-flash（免费）+ embedding-3，OpenAI 兼容 SDK，Key 在 `.env` |
| 工具 | 自研 ToolRegistry（5 工具）+ FastMCP stdio 薄包装 |
| Web | Streamlit 三页面 + Plotly + Pyvis，全局主题在 `common.py`（apply_theme/styled_fig/sidebar_logo） |
| 测试 | pytest 111 个，全离线（FakeLLM 替身，零 API 消耗） |

### 环境硬约束（CLAUDE.md 也有，重复强调）

- **依赖只能用 uv**：`uv pip install -p .venv/bin/python <pkg> -i https://pypi.tuna.tsinghua.edu.cn/simple`
- **运行统一 `./run.sh`**（屏蔽公司全局 PYTHONPATH，否则包被全局旧包遮蔽）
- 种子数据 `data/raw/bidding_sample.csv`（18 条）勿动

### LangGraph 迁移要点（2026-10-08 完成）

- 生产入口：`LangGraphAgent`（web/common.py `load_agent`、scripts/run_agent.py 已切换）
- 图结构：agent → (tool_calls?) → tools → agent；无 tool_calls 时条件边做幻觉防御（模拟特征且未用工具 → nudge 节点 → agent，只重试一次）
- `registry_to_base_tools()`：JSON Schema → pydantic `create_model` 动态生成参数模型 → BaseTool，`_run` 委托 `registry.call`（工具逻辑单一来源 tools.py）
- `_lc_to_dict()`：langchain 消息 → OpenAI role dict，**必须显式映射**（langchain type 是 human/ai，直传智谱报 400 "角色信息不正确"，IT-017）
- 对外接口与手写版完全一致：`run(question, history) -> AgentResult`

### 数据规模

58 条中标公告（种子 18 + 爬虫 50 去重后并入 48），58 公司/58 项目/竞对 271 组；爬虫合规红线已代码化（5-8s 间隔、单日≤200 请求、403 熔断）。

## 三、文档地图（都在 docs/）

| 文档 | 用途 |
|------|------|
| `project_walkthrough.md` | 最全：运行手册（开头）+ 逐文件逐函数说明 + 开发回顾 26 条 + P1/P2 备忘 |
| `testing_iteration_log.md` | 调试台账 IT-001~017（新问题置顶），遇到怪现象先翻这里 |
| `v2_crawler_plan.md` | V2.0 爬虫方案（已完成），含合规红线与决策记录 |
| `langchain_langgraph_study.md` | 手写 Agent ↔ LangChain/LangGraph 概念映射（面试学习材料；第五节改写示例 = 已落地的 graph_agent.py） |
| `HANDOFF.md`（本文） | 上下文压缩后的接续入口 |

已删除：architecture.md / data_model.md（早期设计稿，与实际实现矛盾）、data_collection_guide_v2.md（手工采集时代产物）。

## 四、未完成/待办（用户发起才做）

- **P1**：附件/PDF 金额提取（pdfplumber）；LLM 长尾兜底提取
- **Agent 侧**：~~llm.py 重试区分可重试/不可重试错误（400 被三连重试伪装成 RuntimeError，IT-017 排查弯路）~~ ✅ 已完成（IT-018）；~~LangGraph MemorySaver checkpointer 替代手写 Conversation~~ ⏭️ 已评估：Conversation 在当前场景下是合理设计，MemorySaver 是过度设计
- **扩量**：重跑 `crawl_ccgp.py`（500+ 条，分 2-3 天）——当前 IP 被 WAF 封禁，需换网络/等待解封
- Neo4jStore / SQLite 迁移 / 图算法增强
- ~~行业分类优化（"其他"占 43%）~~ ✅ 已完成（IT-019，"其他"占比 32.8% → 10.3%）
- 曾提议未确认：简历项目描述（STAR）与面试问答准备

## 五、近期会话做了什么（时间线）

1. 确认项目全部完成（除有意延后的 Neo4j/SQLite）
2. 跑全量测试（101 passed）
3. **前端美化三轮迭代**：全局主题（蓝 #2563eb、卡片、Hero 横幅）→ 图谱页降噪（COMPETES_WITH 边默认隐藏，62→20 条线；悬停中文标签；侧边栏 Logo + 自定义导航）→ 隐藏 Streamlit 默认导航（`stSidebarNav display:none`）、图谱页统计条改自定义 HTML 小字号。用户已确认满意（"可以了"）
4. **docs 清理**：删 3 份过时文档；requirements.txt 清理（当时 langchain/langgraph 声明了未用，曾移除）
5. 用户要求恢复使用 LangChain/LangGraph（简历技能需要）→ **LangGraph 迁移完成**（见上），requirements 重新加入 langchain/langgraph/langchain-openai（已装 langgraph 1.2.11 / langchain 1.4.0 / langchain-openai 1.6.7），新增 test_stage4b_langgraph.py 10 用例，文档全面同步（README 技术栈、IT-017、walkthrough 条目 25/26）
6. 用户请求写 HANDOFF.md，然后压缩上下文
7. **2026-10-19 继续优化**：
   - 尝试数据扩量 → IP 被 WAF 封禁，暂时受阻
   - **llm.py 重试逻辑优化（IT-018）**：区分可重试错误（网络/限流/5xx）与不可重试错误（4xx），新增 4 个测试用例
   - 评估 MemorySaver → 结论：Conversation 在当前场景下是合理设计，MemorySaver 是过度设计，跳过
   - **行业分类优化（IT-019）**：新增 `scripts/classify_industry.py`（LLM 辅助分类脚本），"其他"占比 32.8% → 10.3%，新增 industry_llm 字段，图谱/向量索引已重建，全量测试 115 passed

## 六、运行命令速查

```bash
./run.sh -m pytest tests/ -q                                # 全量测试
./run.sh -m streamlit run src/web/app.py                    # Web
./run.sh scripts/run_agent.py -q "问题"                     # Agent CLI
./run.sh scripts/run_agent.py --full                        # 10 问验收
./run.sh scripts/run_stage1.py && ./run.sh scripts/build_graph.py && ./run.sh scripts/build_vector_index.py  # 全链路重建
```

注意：上一会话启动的 Streamlit 后台进程可能还占着 8501 端口，重启前先 `pkill -f "streamlit run"`。
