# 测试迭代记录（Testing Iteration Log）

> 本文档持续记录项目开发与运行中遇到的每一个问题：现象 → 诊断过程 → 根因 → 解决方案 → 验证结果。
> 定位是"调试台账"：以后再遇到类似现象，先来这里翻历史，避免重复踩坑。
> 新问题按时间倒序追加在最上面；已解决的标记 ✅，未解决的标记 🔴 并写明下一步。

---

## ✅ IT-018 [2026-10-19] LLM 重试逻辑区分可重试/不可重试错误

**现象**：IT-017 排查时发现 `llm.chat` 的三连重试把 400 BadRequest 错误伪装成了 RuntimeError，吞掉了真实异常（"角色信息不正确"），导致排查走弯路。
**根因**：原实现 `except Exception` 一刀切重试，未区分错误类型：
- 400/401/403/404 等 4xx 客户端错误表明请求本身有问题，重试无意义且会隐藏真实原因
- 网络异常/超时/限流(429)/服务端错误(5xx) 才是可重试错误
**解决**：
1. 引入 OpenAI SDK 异常类分类：`_RETRYABLE_ERRORS`（APIConnectionError/APITimeoutError/RateLimitError/InternalServerError）与 `_NON_RETRYABLE_ERRORS`（BadRequestError/AuthenticationError/PermissionDeniedError/NotFoundError）
2. `chat()` 方法按异常类型分流：可重试错误指数退避重试、不可重试错误立即抛出、未知异常不重试
3. 延迟导入异常类（避免模块级 openai 依赖）
**验证**：新增 4 个测试用例（非重试错误立即抛出、可重试错误重试成功、可重试错误3次后失败、未知异常不重试），全量 115 passed。
**教训**：重试逻辑必须区分错误类型——客户端错误（4xx）立即失败、服务端/网络错误才重试，否则排查时真实异常被吞。这是 IT-017 的遗留修复。

## ✅ IT-019 [2026-10-19] 行业分类优化：LLM 辅助重新分类"其他"项目

**现象**：数据概览页行业饼图中"其他"占比 32.8%（19/58），影响可视化效果与数据质量观感。
**根因**：行业分类依赖人工标注，种子数据中部分项目标注为"其他"实际可归属明确行业（如"排队叫号系统"→政务监管、"宽带通信"→教育后勤）。
**解决**：
1. 新增 `scripts/classify_industry.py`：LLM 辅助分类脚本
2. 策略：基于标题+摘要+技术标签，调用 GLM-4-flash 判断行业（从已知行业列表选择）+ 置信度 + 推理过程
3. 保守原则：置信度 < 0.8 或 LLM 选择"其他"时保持原样，避免误分类
4. 新增 `industry_llm` 字段保留新分类，原始 `industry` 字段不动
**验证**：
- 19 个"其他"项目，13 个成功重新分类（置信度 ≥ 0.8）
- "其他"占比：32.8% → 10.3%（下降 22.5 个百分点）
- 新增行业分布：政务监管 +4、教育后勤 +3、物业服务 +2、智慧医疗 +1、智慧教育 +1、乡村振兴 +1、医疗卫生 +1
- 全量测试 115 passed，图谱重构（271 组竞对关系）、向量索引重建均正常
**教训**：LLM 辅助分类需保守策略（置信度阈值 + 可解释性），配合人工审核才能保证质量；"其他"类项往往是标注缺失而非真的无法分类。

## ✅ IT-017 [2026-10-08] LangGraph 迁移：智谱 400 "角色信息不正确" + pydantic 把可选参数当必填

**现象**：Agent 升级 LangGraph（graph_agent.py）后，真实调用智谱 API 连续 3 次失败（RuntimeError）；另发现 graph_query 工具报 `company: Field required`——但该参数在 schema 里是非必填。
**诊断**（两个独立缺陷）：
1. **消息角色映射**：`_lc_to_dict()` 直接用 langchain 消息的 `m.type` 当 role——langchain 的 type 是 `human`/`ai`，而 OpenAI 兼容 API 要求 `user`/`assistant`。智谱对未知角色报 400 错误码 1214 "角色信息不正确"。手写版从未暴露此问题（它只处理 dict），是**框架消息对象 ↔ API dict 边界**的新坑。
2. **pydantic 动态模型**：用 `create_model` 从 JSON Schema 生成工具参数模型时，所有字段都写成了 `Field(...)`（必填），忽略了 schema 的 `required` 列表——graph_query 的 company/industry/top 全被标为必填，ToolNode 校验阶段直接拒绝合法调用。
**解决**：
1. `_lc_to_dict` 显式映射：SystemMessage→system、AIMessage→assistant、ToolMessage→tool、HumanMessage→user（不信任 `m.type`）
2. `_make_args_model` 按 `required` 列表区分：必填 `Field(...)`、可选 `Field(default=None)` 且类型标注为 `ann | None`
**验证**：新增 test_stage4b_langgraph.py 10 用例（含角色映射回归 + 非必填参数回归），全量 111 passed；真实 API 验证"中国电信表现"（单工具）与"智慧教育金额最高+竞对"（链式两工具）均正常。
**教训**：① 框架的消息对象与 HTTP API 的 dict 之间**必须显式做词汇映射**，`type`/`role` 这种"看着一样"的字段最容易埋雷；② `llm.chat` 的三连重试把 400 BadRequest 伪装成了 RuntimeError——重试逻辑要区分"可重试错误"（网络/限流）与"不可重试错误"（4xx 参数错误），否则排查时真实异常被吞；③ pydantic `create_model` 动态建模时，required 语义必须逐字段对齐源 schema。

## ✅ IT-016 [2026-09-17] 集团简称查询失败 + 聚合竞对丢失领域信息

**现象**：Agent 10问验收第10问"中国联通的竞争对手有哪些？分别在哪些领域竞争？"——competitors 返回空；修完后回答竞对名单正确但说"具体领域无法得知"。
**诊断**（三个叠加缺陷，同源）：
1. `graph_query` 的 competitors 分支只做精确 canonical_name 匹配，无 company_profile 那样的模糊回退
2. "中国联通"是"中国联合网络通信"的**简称**（联通二字在集团全称中不相邻），子串匹配也漏 → 需要字符集包含匹配（简称⊆全称字符集且唯一命中）
3. `group_profile` 聚合竞对时只保留 company 名，丢弃了 shared_industries——聚合是信息有损操作，各分公司竞对的共同领域必须在聚合时合并保留
**解决**：tools.py 新增 `_resolve_group()`（两级：子串包含 → 字符集包含唯一命中），company_profile_lookup 与 competitors 分支共用；group_profile 竞对聚合改为 `{company: set(shared_industries)}`。
**验证**：全量 101 passed；"中国联通"→9家竞对+领域（智能交通/政务监管），"中国电信"→27家竞对；Agent 重测第10问回答完整。
**教训**：面向 LLM 的工具必须假设输入是"人类的随意说法"而非数据集精确键值——**每个按名查询的工具都要有简称/别名兜底**；聚合函数要做信息保全审计（哪些字段在 reduce 时被丢掉了）。

## ✅ IT-015 [2026-09-17] 爬虫正文乱码：chardet 编码误判导致提取全失败

**现象**：50 条爬取数据中 1 条（衢州医疗设备公告）正文全是 `жөҷжұҹзңҒ...` 乱码，字段提取全失败。
**诊断**：`resp.encoding = resp.apparent_encoding`（chardet 探测）在该页面误判编码。ccgp 页面 HTML 头部有 `<meta charset>` 声明，应按声明解码。
**解决**：`base.py::_decode()` —— 解析响应头 1KB 内的 meta charset 声明优先解码；无声明按 utf-8 → gbk 回退（政府站常见编码）；加乱码启发式检查（`�` 字符计数）。
**验证**：全量测试 101 passed；删除乱码条目重爬，正文正常、7 字段全提取（supplier/amount/project_name/purchaser/region/project_id/announce_time，金额 1694511.7 正确）。
**教训**：`apparent_encoding` 的 chardet 探测对短文本/混合编码不可靠；政府网站优先信 meta 声明。

## ✅ IT-014 [2026-09-16] Agent 不调用工具，编造"XX公司/YY公司"式回答（已修复）

**现象**
Web「Agent对话」页提问"智慧教育行业谁的金额最高？它有哪些竞争对手？"，返回的回答是：
```
Thought: 首先需要查询智慧教育行业金额最高的公司……
Action: 使用 graph_query 工具查询……
Observation: 智慧教育行业金额最高的公司是XX公司。
最终答案：……竞争对手有YY公司、ZZ公司等。
```
全部是占位符，没有真实数据。

**诊断过程（关键，按实验顺序）**
| # | 实验 | 结果 |
|---|------|------|
| 1 | 命令行同问题复现 ×3 | 3/3 编造，稳定复现 |
| 2 | 裸调 `llm.chat`（简短 system） | ✅ 正常返回 tool_calls → 排除 SDK/schema 层 |
| 3 | 裸调 + 完整 SYSTEM_PROMPT ×5 | 4/5 编造，1/5 正常 → 锁定 system prompt |
| 4 | 换 glm-4-air / glm-4-plus（原 prompt） | 全部正常 → flash 模型更敏感 |
| 5 | **改写 prompt：去掉 Thought/Action/Observation 词汇 ×5** | **5/5 正常 → 实锤根因** |

**根因**
SYSTEM_PROMPT 的「工作方法」一节教模型用 `Thought → Action → Observation → Final Answer` 的**文本格式**组织推理。glm-4-flash 把它理解成了"在回复文本里把工具调用过程演一遍"（自己填写 Observation 的内容），而不是通过 function calling 协议真正发起工具调用——于是没有工具数据，只能编占位符。
阶段4验收时恰好模型走了真实 tool_calls 路径（实验3证明原 prompt 下也有约 1/5 概率正常），掩盖了问题。

**修复实施（3处）**
1. `src/agent/prompts.py`：重写 SYSTEM_PROMPT——删除文本格式 ReAct 教学，改为"直接调用工具→拿真实数据→回答"，加两条硬禁令（不编造数据/不模拟工具过程）；prompt 模块 docstring 记录此教训防止回退；新增 `SIMULATED_ANSWER_NUDGE` 纠正消息常量
2. `src/agent/react.py`：新增 `looks_simulated()`（正则检测 Thought:/Action:/Observation:/最终答案: 特征）；`run()` 中若最终回答带模拟特征且 `used_tools=False` → 注入幻觉回答 + 纠正消息重试一次；`AgentResult` 新增 `used_tools` / `retried_for_simulation` 字段
3. `src/web/pages/3_Agent对话.py`：`used_tools=False` 的回答渲染前显示"⚠️ 本次回答未经过工具数据查询"警告条

**验证结果（2026-09-16）**
- 单测：新增 5 个幻觉防御回归用例（检测器正确性/重试消息结构/只重试一次/正常路径不误触发/调工具同时写草稿不误触发），test_stage4.py 16→21 通过
- 端到端：原必现编造的同问题连跑 **5/5 全部真实链式调用**（步骤1 industry_top → 步骤2 competitors），无 XX公司 占位符，数字正确
- 全量回归：77 → **82 passed**
- 遗留观察点：`looks_simulated` 基于文本特征，理论上模型正常回答中引用"Action"一词会误判——但重试代价仅为一次额外 LLM 调用，且已验证不误触发正常路径，风险可接受

**教训**
提示词里教模型"协议词汇的文本格式"会与 function calling 协议打架——模型分不清"描述协议"和"使用协议"。验收不能只测一次：本问题在阶段4验收时因单次运气好而漏网，**LLM 行为验收必须多次采样**（≥5次）。

---

## ✅ IT-013 [2026-09-16] Streamlit 弃用 API 警告

**现象**：页面运行打印 `use_container_width will be removed after 2025-12-31`、`st.components.v1.html` 弃用警告。
**诊断**：Streamlit 1.64 中 `use_container_width` 被 `width='stretch'` 取代；`st.iframe` 不接受原始 HTML（pyvis 输出是完整 HTML 文档需要 iframe 隔离，`st.html` 不隔离 JS 会互相污染）。
**解决**：三个页面批量替换 `use_container_width=True` → `width='stretch'`；图谱页保留 `components.html`（合理使用，接受弃用警告）。
**验证**：页面 bare-mode 渲染回归通过。

## ✅ IT-012 [2026-09-16] MCP stdio 协议被日志污染（设计预防）

**风险**：MCP stdio 模式下 stderr 即协议通道，loguru 默认往 stderr 写日志会破坏协议流。
**解决**：`scripts/run_mcp_server.py` 启动时 `logger.remove()`。这是 MCP 开发最常见坑之一。
**验证**：stdio 子进程端到端握手 + list_tools(5) + call_tool 成功。

## ✅ IT-011 [2026-09-16] 智谱 API 思考文本混入流式 chunk JSON

**现象**：Agent 步骤1的"思考"内容尾部出现 `{"index":0,"finish_reason":"tool_calls","delta":{...}}` 原始 JSON。
**诊断**：智谱 OpenAI 兼容层偶发把流式 chunk 序列化结果拼接在 content 尾部。
**解决**：`llm.py` 加 `_clean_content()`——content 含 `"finish_reason"`/`"delta"` 特征时从 `{"index"` 标记处截断。
**验证**：重跑后思考内容干净。
**教训**：第一版用"content 以 { 开头"判断（假设 JSON 在开头），实际是拼接在**尾部**——修复防御逻辑前先确认数据形态。

## ✅ IT-010 [2026-09-16] trace 展示崩溃：'NoneType' object is not subscriptable

**现象**：Agent 其实完整跑通（真实调用了工具），但 Web/命令行展示时崩溃。
**诊断**：`trace_summary()` 对最终回答那条 trace 的 `observation=None` 做了切片。报错位置（展示层）≠根因位置（数据结构约定）。
**解决**：渲染时判空（observation is None → 显示"给出最终回答"）。
**教训**：错误堆栈指向的代码往往不是错误的原因；先看数据在哪一步变成 None。

## ✅ IT-009 [2026-09-16] 问集团名只返回单分公司数据

**现象**：问"中国电信"，返回 1 个项目 186 万（太原分公司），实际集团有 2 项目 358.5 万。
**诊断**：company_profile 三级解析中模糊匹配先命中了单个分公司 canonical_name。
**解决**：`queries.py` 新增 `group_profile()`（按 group 聚合成员公司：项目数/总额/行业分布/竞对并集）；工具层调整解析顺序为 精确命中 → 集团名唯一命中→聚合 → 公司名模糊。
**验证**："中国电信" → 2 项目 3585000 元，成员公司列表正确。

## ✅ IT-008 [2026-09-16] 向量索引兜底 id 与图谱不一致

**现象**：测试发现无 project_id 的行在向量库用 `row-{行号}` 兜底，图谱用标题哈希——同一项目两侧 id 对不上，行号还会随 DataFrame 变化。
**解决**：`search.py::_row_id` 改为与图谱 `project_key` 一致的标题哈希兜底。
**验证**：幂等性测试通过（同数据重复 build 不产生重复）。

## ✅ IT-007 [2026-09-16] ChromaDB 查询维度不匹配 + 隐性模型下载

**现象**：查询报 `Collection expecting embedding with dimension of 2048, got 384`；且构建阶段莫名超时。
**诊断**：`collection.query(query_texts=...)` 时 ChromaDB 会退回**内置 ONNX 模型**（all-MiniLM-L6-v2，384维）给查询文本向量化，与库内存的智谱 2048 维冲突；首次触发还自动下载 79MB 模型（公司网络慢导致超时假象）。
**解决**：查询向量必须由建索引用的**同一个 embedder** 生成，以 `query_embeddings=` 传入；代码注释写明原因。
**教训**：向量检索的铁律——**建索引和查询必须用同一个 embedding 模型**；任何"自动向量化"的便利接口都要搞清背后用的什么模型。

## ✅ IT-006 [2026-09-16] parquet 回读列表列变 numpy array

**现象**：`row.get("tech_tags_list") or []` 报 `The truth value of an array is ambiguous`。
**诊断**：parquet 序列化把 Python list 列读回为 numpy array，`or` 对其有歧义。阶段2（builder）、阶段3（search）各踩一次。
**解决**：统一 `tags = list(x) if x is not None and not pd.isna(x) else []` 模式。

## ✅ IT-005 [2026-09-16] MultiDiGraph edges 解包错误

**现象**：`stats()` 报 `not enough values to unpack (expected 4, got 3)`。
**诊断**：networkx `edges(keys=True)` 返回 (u,v,key) 三元组，需加 `data=True` 才是四元组。
**解决**：改 `edges(keys=True, data=True)`。

## ✅ IT-004 [2026-09-16] 公司名后缀剥离不迭代，集团归并失败

**现象**："中国电信股份有限公司"剥后缀→"中国电信"，"中国电信集团有限公司"→"中国电信集团"，差一个字无法归并到同一集团。
**解决**：`entity_aligner.py` 新增 `_iterative_strip_suffix()` 循环剥离直到无后缀可剥。
**验证**：两个写法归并到同一 key；回归测试固化。

## ✅ IT-003 [2026-09-16] 正则误吞"有限公司"，公司名丢字

**现象**：`中国联合网络通信有限公司银川市分公司` 的集团名变成"中国联合网络通"（丢"信"）。
**诊断**：分公司名正则 `(?P<core>.+?)(?P<city>[一-龥]{2,8})分公司` 中，城市 `[一-龥]` 把"有限公司"四个汉字也匹配了，非贪婪 core 提前截断。
**解决**：放弃正则，确定性字符串操作——剥"分公司"后缀后定位最后一个"公司"，其后即城市名。
**验证**：回归测试 `test_branch_of_unicom` 固化；全量对齐抽查正确。

## ✅ IT-002 [2026-09-16] 全局 Python 环境遮蔽 .venv 依赖

**现象**：.venv 里装了包，import 却解析到全局旧版本；全局 langgraph 还是能 import 无版本号的半残包。
**诊断**：公司 shell 预设 `PYTHONPATH=/opt/python-venv/...`，在 sys.path 中优先于 .venv site-packages。
**解决**：`run.sh` 运行时 `env -u PYTHONPATH`；验证包归属用 `importlib.util.find_spec` 的 origin（不能用 `__file__`——namespace package 的 `__file__` 是 None，会误报）。

## ✅ IT-001 [2026-09-16] pip 违规安装 + .venv 重建

**现象**：早期用 `.venv/bin/pip` 装了依赖（违反公司 uv-only 规定）；VS Code 打开 notebook 时也自动 pip 装了 ipykernel。
**解决**：删掉整个 `.venv`，`uv venv .venv --python 3.12` 重建（uv 创建的 venv 不含 pip，物理杜绝再犯），全部依赖 `uv pip install` 重装。
**教训**：环境约束要在项目初始化第一天就想清楚（后来写进了 CLAUDE.md）。

---

## 附：测试统计

| 阶段 | 测试文件 | 用例数 | 备注 |
|------|----------|--------|------|
| 阶段1 数据处理 | test_stage1.py | 28 | 含实体对齐回归用例（IT-003/004） |
| 阶段2 知识图谱 | test_stage2.py | 13 | 含 save/load round-trip、接口契约 |
| 阶段3 向量检索 | test_stage3.py | 13 | 全离线（HashingEmbedder） |
| 阶段4 Agent | test_stage4.py | 25 | 含幻觉防御5用例（IT-014）+ LLM重试策略4用例（IT-018） |
| 阶段4b LangGraph Agent | test_stage4b_langgraph.py | 10 | StateGraph 循环/条件边幻觉防御/角色映射回归 |
| 阶段5 MCP | test_stage5.py | 7 | 协议级内存会话测试 |
| 爬虫+提取（V2.0） | test_crawler.py / test_extract.py | 19 | WAF熔断/增量/三层提取策略 |
| **合计** | | **115** | |

**待办**：~~IT-014 修复后，需在 test_stage4.py 补一个"幻觉式回答检测与重试"的回归用例~~ ✅ 已完成（test_stage4.py 新增5用例，全量82 passed）
