# LangChain / LangGraph 对照学习报告

> 面向初学者：先读懂本项目的手写 Agent，再映射到 LangChain/LangGraph 的对应概念。
>
> **结论先行：本项目代码中没有实际使用 LangChain/LangGraph**（只在 requirements.txt 中声明、在 architecture.md 早期设计稿中提及但未实现）。实际 Agent 是手写的 ReAct 循环（约 300 行）。但手写版恰好实现了这两个框架的核心概念——读懂手写版，就理解了框架封装的到底是什么。

---

## 目录

1. [事实澄清：LangChain/LangGraph 在本项目的状态](#一事实澄清)
2. [总体对照地图](#二总体对照地图)
3. [逐文件逐函数精读（含传入传出）](#三逐文件逐函数精读)
4. [手写代码 ↔ LangChain/LangGraph 概念对照](#四概念对照)
5. [改写示例：用 LangGraph 重写本项目 Agent](#五改写示例)
6. [动手练习建议](#六动手练习)

---

## 一、事实澄清

| 位置 | 内容 | 状态 |
|------|------|------|
| ~~`requirements.txt`~~ | 早期误声明 `langchain`/`langgraph`，**已从依赖清单移除**（代码零引用，无需安装） | 已清理 |
| `src/agent/react.py` | 手写 ReAct 循环 | **实际实现**，全项目 Agent 逻辑核心 |

> 注：早期设计稿 `docs/architecture.md`（含 PostgreSQL/Neo4j/LangGraph 工作流等未实现内容）已删除，实际实现以 `README.md`「当前状态」和代码为准。

---

## 二、总体对照地图

先看数据怎么流，每一环用什么技术：

```
用户问题："华为在智慧城市领域的中标情况如何？"
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ src/web/pages/3_Agent对话.py  →  src/web/common.py:157-161  │
│   load_agent(): ReActAgent(LLMClient(), build_registry())   │
└────────────────────────┬────────────────────────────────────┘
                         │  agent.run(question, history)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ src/agent/react.py  ReActAgent.run()                        │
│   循环: llm.chat(messages, tools) → 有tool_calls?           │
│     ├─ 是 → registry.call() 执行工具 → 结果作为 observation  │
│     │        塞回 messages → 下一轮                         │
│     └─ 否 → 模型输出最终回答，循环结束                       │
└───────┬──────────────────────────────┬─────────────────────┘
        │                              │
        ▼                              ▼
┌──────────────────┐        ┌──────────────────────────────┐
│ src/agent/llm.py │        │ src/mcp_tools/registry.py    │
│ LLMClient.chat() │        │ ToolRegistry.call()          │
│ OpenAI SDK 调用  │        │ 按名字找工具函数并执行       │
│ 智谱AI兼容接口   │        │ (实现函数在 tools.py)        │
└──────────────────┘        └──────────────────────────────┘
```

**LangChain/LangGraph 的定位**：上图右侧的"循环控制 + 消息管理 + 工具执行"这一坨，LangGraph 用 `create_react_agent()` 一行就能替代；LLM 调用则由 `ChatOpenAI`（langchain 的模型抽象）替代。框架的价值 = 把这些通用样板代码变成配置。

---

## 三、逐文件逐函数精读

以下 5 个文件构成完整 Agent，共约 400 行，全部读完。每个函数标注：**传入 → 传出 → 作用**。

### 3.1 `src/agent/llm.py` —— LLM 客户端（60 行）

**作用**：把"调用大模型 API"封装成一个 `chat()` 方法。这是最底层的一块积木。

#### `class LLMClient`

**`__init__(self, model="glm-4-flash", api_key=None, base_url=None)`**
- 传入：模型名、API 密钥（不给则从 `.env` 读 `ZHIPU_API_KEY`）、API 地址
- 作用：只记参数，**不立即创建连接**（`self._client = None`）

**`_get_client(self) -> OpenAI`**（[llm.py:37-44](../src/agent/llm.py#L37-L44)）
- 传入：无（读自己的属性）
- 传出：OpenAI SDK 客户端对象
- 作用：第一次调用时才创建客户端（**延迟导入/懒加载**——不调用就不 import openai，测试环境没装 SDK 也不报错）

**`chat(self, messages, tools=None, temperature=0.3, max_tokens=2048) -> dict`**（[llm.py:46-85](../src/agent/llm.py#L46-L85)）
- 传入：
  - `messages`：`[{"role": "system"|"user"|"assistant"|"tool", "content": str, ...}]` 对话历史
  - `tools`：工具说明书列表（OpenAI function calling 格式），不给则模型只能纯文本回答
- 传出：**统一的纯 dict**：
  ```python
  {"content": "模型文本",            # 可能是 None
   "tool_calls": [                   # 模型想调用的工具，可能为 None
       {"id": "c1", "function": {"name": "company_profile",
                                  "arguments": "{\"company\": \"华为\"}"}}]}
  ```
- 作用：① 发请求；② 把 SDK 返回的对象转成上面的简单 dict（**为什么要转？** 下游代码不需要 import openai 的类型，测试时也能用假 LLM 替换）；③ 失败自动重试 3 次，等待时间指数递增（2s → 4s → 8s）。

#### ⚡ 对照 LangChain

`LLMClient` ≈ langchain 的 **ChatModel 抽象**。用 langchain 写同样的东西：

```python
from langchain_openai import ChatOpenAI   # 任何 OpenAI 兼容服务都用它

llm = ChatOpenAI(
    model="glm-4-flash",
    api_key=os.getenv("ZHIPU_API_KEY"),
    base_url="https://open.bigmodel.cn/api/paas/v4",
    temperature=0.3,
)
# langchain 版 chat：传入消息对象，返回 AIMessage（含 tool_calls 属性）
resp = llm.invoke(messages)          # messages 是 [SystemMessage(...), HumanMessage(...)]
resp.content      # 文本
resp.tool_calls   # [{"name": ..., "args": ..., "id": ...}]，langchain 已帮你解析好
```

LangChain 做了什么：把"不同厂商 SDK 返回格式不同"抹平成统一的 `AIMessage`，并把消息格式从"手写 dict"变成"带类型的消息对象"。**换厂商 = 换一个类名**，`chat()` 循环代码不动。

---

### 3.2 `src/mcp_tools/registry.py` —— 工具注册表（80 行）

**作用**：Agent 的"工具箱"。定义工具怎么登记、说明书长什么样、怎么被调用。

#### `@dataclass class Tool`（[registry.py:17-31](../src/mcp_tools/registry.py#L17-L31)）

一个工具的四要素：`name`（名字）、`description`（给模型看的用途说明）、`parameters`（JSON Schema 参数定义）、`func`（真正的 Python 函数）。

**`schema(self) -> dict`**
- 传出：OpenAI function calling 格式的"说明书"，形如：
  ```python
  {"type": "function",
   "function": {"name": "search_projects",
                "description": "按行业/省份/关键词筛选项目",
                "parameters": {"type": "object", "properties": {...}}}}
  ```
- 作用：每次 `chat()` 时把这批说明书发给模型——**模型就是看了说明书才知道有哪些工具可调**。

#### `@dataclass class ToolRegistry`

**`register(self, name, description, parameters) -> 装饰器`**（[registry.py:38-50](../src/mcp_tools/registry.py#L38-L50)）
- 用法（装饰器写法，在测试 [test_stage4.py:57-66](../tests/test_stage4.py#L57-L66) 有最小示例）：
  ```python
  reg = ToolRegistry()

  @reg.register(name="add", description="求和", parameters={...})
  def add(a, b):
      return {"sum": a + b}
  ```
- 作用：被装饰的函数登记进 `self.tools` 字典。这就是"工具注册"。

**`schemas(self) -> list[dict]`**
- 传出：所有工具的说明书列表 → 直接喂给 `llm.chat(tools=...)`

**`call(self, name, arguments) -> str`**（[registry.py:55-70](../src/mcp_tools/registry.py#L55-L70)）
- 传入：`name`（模型指定的工具名）、`arguments`（**JSON 字符串**——模型输出的参数永远是字符串）
- 传出：**JSON 字符串**（执行结果）
- 作用：① 按名查工具；② `json.loads` 解析参数；③ `tool.func(**args)` 执行；④ **任何异常都转成错误 JSON 返回而不抛出**——这一点很关键：如果工具报错直接抛异常，整个 Agent 就崩了；转成错误信息塞回对话，模型看到错误可以自行调整（比如换个参数重试）。
- ⑤ 结果超过 4000 字符截断（防止单个工具结果撑爆上下文）。

#### ⚡ 对照 LangChain

`ToolRegistry.register` ≈ langchain 的 **@tool 装饰器**：

```python
from langchain_core.tools import tool

@tool
def search_projects(industry: str | None = None, province: str | None = None,
                    keyword: str | None = None, top_k: int = 10) -> dict:
    """按行业/省份/关键词/金额区间筛选中标项目。"""   # docstring 就是 description
    ...  # 函数体不变
```

LangChain 做了什么：**从函数签名和 docstring 自动生成 JSON Schema**——本项目中每个工具的 `parameters` 字典要手写一遍（见 [tools.py:240-305](../src/mcp_tools/tools.py#L240-L305) 的 `TOOL_DEFS`），langchain 省掉了这份重复劳动。工具异常转错误信息、结果截断等，`ToolNode` 内置了类似处理（`handle_tool_errors=True`）。

---

### 3.3 `src/mcp_tools/tools.py` —— 5 个工具的业务实现

**作用**：每个工具 = 一个普通 Python 函数 + 一段 schema 定义。

**关键结构**（三段式，[tools.py:336-340](../src/mcp_tools/tools.py#L336-L340)）：

```python
# ① 业务函数（普通函数，查询 parquet / 图谱 / 向量库）
def search_projects(industry=None, province=None, ...) -> dict: ...

# ② TOOL_DEFS：工具名 → {"description": ..., "parameters": ...}（手写 JSON Schema）
TOOL_DEFS = {"search_projects": {...}, "company_profile": {...}, ...}

# ③ build_registry()：把两者装配成 ToolRegistry（进程内单例）
def build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for name, spec in TOOL_DEFS.items():
        reg.register(name=..., ...)(_TOOL_FUNCS[name])
    return reg
```

5 个工具速览：

| 工具名 | 传入参数 | 返回 | 数据源 |
|--------|---------|------|--------|
| `search_projects` | industry/province/keyword/min_amount/max_amount/top_k | 匹配项目列表 | parquet 表格 |
| `company_profile` | company（公司名） | 中标统计/行业分布/竞对名单 | networkx 图谱 |
| `graph_query` | query_type（枚举5种）+ company/industry | 竞对、排行、技术热度等 | networkx 图谱 |
| `semantic_search` | query（自然语言）/top_k/industry | 语义相似项目 | ChromaDB |
| `industry_stats` | 无必填 | 各行业/地区数量与金额汇总 | parquet 表格 |

注意 [tools.py:44-70](../src/mcp_tools/tools.py#L44-L70) 的 `_load_df/_load_graph/_load_index`：模块级缓存，进程内只初始化一次；`_load_index` 还带**降级逻辑**（智谱 Embedding 不可用时退回本地 HashingEmbedder）。

#### ⚡ 对照 LangChain

这 5 个函数就是框架里说的 "tools"。在 langgraph 中直接把函数列表传给 agent 即可：

```python
from langgraph.prebuilt import create_react_agent
agent = create_react_agent(llm, [search_projects, company_profile, graph_query,
                                  semantic_search, industry_stats])
```

`TOOL_DEFS` 那份手写 schema 全部删除——langchain 自动从类型注解生成。

---

### 3.4 `src/agent/react.py` —— ReAct 循环（核心！170 行）

**这是全项目最值得精读的文件，也是 LangGraph 替代得最彻底的部分。**

#### `@dataclass AgentResult`（[react.py:38-59](../src/agent/react.py#L38-L59)）

一次问答的完整产出：
- `answer: str` —— 最终回答文本
- `trace: list[dict]` —— 每一步的 {思考, 工具调用, 观察}，供 Web 展示推理过程
- `steps: int` —— 用了几步
- `used_tools: bool` —— 是否真正调用过工具（用于幻觉检测，见下）

**`trace_summary(self) -> str`**：把 trace 渲染成人类可读的多行文本。

#### `looks_simulated(answer: str) -> bool`（[react.py:29-35](../src/agent/react.py#L29-L35)）

- 传入：一段回答文本
- 传出：bool
- 作用：用正则 `(Thought\s*:|Action\s*:|Observation\s*:...)` 检测回答里是否出现"模拟工具执行"的特征词。
- **背景**（真实踩坑，见 [prompts.py](../src/agent/prompts.py) 顶部注释）：小模型 glm-4-flash 有时不真正调用工具，而是在回答里**编造**"Thought: 我调用了工具... Observation: 华为中标 1.2 亿元"——数据是幻觉。这是初学 Agent 开发必踩的坑。

#### `class ReActAgent`

**`__init__(self, llm, registry, max_steps=8, system_prompt=SYSTEM_PROMPT, verbose=False)`**
- `llm`：只要求有 `chat()` 方法（见 protocols.py，**鸭子类型接口**）
- `max_steps=8`：**循环上限**。没有它会怎样？模型可能陷入"调工具→看结果→再调工具"的死循环，每轮都烧 API 费用。

**`run(self, question, history=None) -> AgentResult`**（[react.py:83-102](../src/agent/react.py#L83-L102)）
- 传入：用户问题、可选的多轮对话历史（`Conversation` 对象）
- 传出：`AgentResult`
- 流程：
  1. 组装初始消息：`[system] + 历史消息 + [user 问题]`
  2. 调 `_run_loop` 跑主循环
  3. **幻觉防御**：若回答带模拟特征 且 本次从未真正调用工具 → 把假回答和一条纠正消息（`SIMULATED_ANSWER_NUDGE`）追加进对话，**重跑一次循环**

**`_run_loop(self, messages, start_step=0) -> AgentResult`**（[react.py:104-169](../src/agent/react.py#L104-L169)）

逐行看这个循环（建议对照源码读）：

```python
for step in range(start_step + 1, self.max_steps + 1):
    # ① 问模型："基于这些消息 + 这些工具说明书，下一步做什么？"
    msg = self.llm.chat(messages, tools=self.registry.schemas())
    thought = msg.get("content")          # 模型的思考文本
    tool_calls = msg.get("tool_calls")    # 模型想调用的工具

    if not tool_calls:
        # ② 模型不再要求调用工具 = 认为信息够了，给出最终回答
        return AgentResult(answer=thought, ...)

    # ③ 把模型的"调用意图"追加进对话历史（role=assistant, 带 tool_calls）
    messages.append({"role": "assistant", "content": thought, "tool_calls": [...]})

    # ④ 逐个真正执行工具，结果以 role=tool 消息塞回
    for tc in tool_calls:
        observation = self.registry.call(name, args)   # 真正执行！
        messages.append({"role": "tool", "tool_call_id": tc["id"],
                         "content": observation})
    # ⑤ 回到循环开头：模型看到工具结果，决定继续调工具还是回答
```

**一图看懂一次完整运行的 messages 变化**：

```
[system]   系统提示词
[user]     华为在智慧城市领域的中标情况如何？
[assistant] content="需要查华为画像" tool_calls=[company_profile("华为")]
[tool]     {"group": "华为", "total_amount": ..., "industries": {...}}   ← 真实数据
[assistant] content=None tool_calls=[graph_query("competitors", "华为")]
[tool]     {"competitors": ["中兴", ...]}
[assistant] content="华为在智慧城市领域共中标 3 个项目，总金额..."   ← 无 tool_calls，结束
```

- `used_tools` 标记：只要执行过至少一次工具就为 True → 用来配合幻觉检测。
- 步数耗尽（[react.py:160-169](../src/agent/react.py#L160-L169)）：追加一条 `FINAL_ANSWER_NUDGE`（"别再调工具了，立即基于已有数据回答"）强制收尾。

#### ⚡ 对照 LangGraph

`_run_loop` ≈ langgraph 的 **create_react_agent**（prebuilt）或 **StateGraph** 手搭的图。手写的 for 循环 + if 分支，在 LangGraph 里变成"节点 + 条件边"：

```python
from langgraph.prebuilt import create_react_agent
agent = create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)
result = agent.invoke({"messages": [("user", "华为在智慧城市领域的中标情况如何？")]},
                      config={"recursion_limit": 25})   # 对应 max_steps
result["messages"][-1].content   # 最终回答
```

框架内部做的事和你的 for 循环一模一样：模型输出 → 有 tool_calls 就执行 → 塞回 ToolMessage → 回到模型 → 没有就结束。

---

### 3.5 支持性文件

#### `src/agent/protocols.py`（16 行，读完）

```python
@runtime_checkable
class SupportsChat(Protocol):
    def chat(self, messages: list[dict], tools: list[dict] | None = None, **kwargs) -> dict: ...
```

**作用**：用 Python 的 `Protocol`（结构化子类型/鸭子类型的显式版）定义"什么算一个 LLM"——只要有 `chat()` 方法签名匹配即可。真实 `LLMClient` 和测试用 `FakeLLM` 都满足。这等价于 langchain 里 `BaseChatModel` 抽象基类的作用（框架版还多流式、缓存、token 计数等标准能力）。

#### `src/agent/conversation.py`（33 行，读完）

`Conversation` 类管理多轮对话历史：
- `add(role, content)`：追加一条消息
- `add_exchange(question, answer)`：记录一轮问答
- `as_list() -> list[dict]`：导出消息列表（传给 `agent.run(history=...)`）
- `_trim()`：超过 `max_messages=40` 时按**完整问答对**裁剪旧消息（不能从半截切，否则 user/assistant 配对乱了模型会困惑）

#### ⚡ 对照 LangGraph

`Conversation` ≈ LangGraph 的 **checkpointer 记忆机制**：

```python
from langgraph.checkpoint.memory import InMemorySaver
agent = create_react_agent(llm, tools, checkpointer=InMemorySaver())
# 同一 thread_id 的多次 invoke 自动带历史，等价于 add_exchange 的作用
cfg = {"configurable": {"thread_id": "user-42"}}
agent.invoke({"messages": [("user", "q1")]}, cfg)
agent.invoke({"messages": [("user", "q2")]}, cfg)   # 模型自动知道 q1
```

LangGraph 的版本更强：记忆可持久化到 SQLite/Postgres、可跨进程恢复，而 `Conversation` 只活在内存里。

#### `src/agent/prompts.py`（System Prompt 模板）

定义了三段提示词，都有明确职责：
- `SYSTEM_PROMPT`：角色设定 + 工具选择指引（什么问题用什么工具）+ 回答规范（数字必须来自工具）。**注意**：刻意不出现 Thought/Action/Observation 字样（避免诱导模型文本模拟工具，见 3.4 的坑）。
- `FINAL_ANSWER_NUDGE`：步数耗尽后的强制收尾指令
- `SIMULATED_ANSWER_NUDGE`：幻觉模拟被检测到后的纠正指令

#### `tests/test_stage4.py` —— 不花钱测 Agent 的方法

**`FakeLLM`**（[test_stage4.py:44-53](../tests/test_stage4.py#L44-L53)）：按脚本顺序返回预设消息的替身。

```python
llm = FakeLLM([
    {"content": "需要计算", "tool_calls": [_tool_call("c1", "add", {"a": 1, "b": 2})]},
    {"content": "1+2=3", "tool_calls": None},
])
agent = ReActAgent(llm, registry)
result = agent.run("算一下1+2")
# 断言：result.answer == "1+2=3"，且 registry.call 真的被执行过
```

**为什么重要**：测 Agent 不调真 API——① 免费 ② 可断言确定性行为 ③ CI 可跑。这依赖两个设计：`LLMClient` 输出统一 dict（FakeLLM 好造）+ `SupportsChat` 协议（FakeLLM 能注入）。langgraph 也有对应测试方案（`FakeChatModel` / `langgraph.test`），但本项目这个手写替身更直白易读。

---

## 四、概念对照（速查表）

| 本项目（手写） | LangChain / LangGraph 对应 | 备注 |
|---------------|---------------------------|------|
| `LLMClient.chat()` | `ChatOpenAI.invoke()` / `init_chat_model()` | 模型抽象统一 |
| `messages: list[dict]` | `HumanMessage / AIMessage / ToolMessage / SystemMessage` 对象列表 | 类型化消息 |
| `ToolRegistry.register` 装饰器 | `@tool` 装饰器 | 后者从签名自动生成 schema |
| `TOOL_DEFS` 手写 JSON Schema | 自动生成（Pydantic） | 消除重复 |
| `registry.call()` 异常转错误 JSON | `ToolNode(handle_tool_errors=True)` | 同样思想 |
| `ReActAgent._run_loop` for 循环 | `create_react_agent()`（langgraph.prebuilt）/ `langchain.agents.create_agent` | **对应度最高** |
| `max_steps=8` | `recursion_limit`（config） | 防死循环 |
| `AgentResult.trace` | `agent.stream(...)` 逐步产出 / `astream_events` | 展示推理过程 |
| `Conversation` + `_trim` | `checkpointer=InMemorySaver()` + thread_id | 框架版可持久化 |
| 幻觉防御：检测模拟→注入纠正→重试 | 条件边（conditional edge）`loop back` | **本项目特色，框架没有现成的**——这正是学 StateGraph 自定义图的最好素材 |
| `SupportsChat` Protocol | `BaseChatModel` 抽象基类 | 依赖倒置 |
| MCP Server（`server.py`，FastMCP） | `langchain_mcp_adapters`（client 侧）；LangChain 1.x `create_agent` 可直接接 MCP 工具 | 本项目工具双重身份：进程内 + MCP 暴露 |

**一句话总结三者关系**：
- **LangChain** = 积木库（模型抽象、消息类型、工具定义、RAG 组件）
- **LangGraph** = 流程编排（把"调用模型/执行工具/条件分支"画成图，自带记忆、断点、人工介入）
- 本项目手写的 = 这两层的最小功能子集，**没有框架抽象的额外复杂度，是理解框架的最佳教材**

---

## 五、改写示例：用 LangGraph 重写本项目 Agent

假如把 `ReActAgent` 换成 LangGraph，改动范围有多大？

### 方案 A：prebuilt 一行流（最简）

```python
# src/agent/langgraph_agent.py（练习用，勿直接替换）
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import InMemorySaver

from src.agent.llm import llm_from_env          # 见方案 B
from src.mcp_tools.tools import build_registry

def build_agent():
    registry = build_registry()
    tools = [t.func for t in registry.tools.values()]   # 复用全部业务函数
    return create_react_agent(
        llm_from_env(),
        tools,
        prompt=SYSTEM_PROMPT,
        checkpointer=InMemorySaver(),
    )

# 使用（替换 src/web/common.py 的 load_agent）：
result = build_agent().invoke(
    {"messages": [("user", "华为在智慧城市领域的中标情况如何？")]},
    {"configurable": {"thread_id": "demo"}, "recursion_limit": 16},
)
answer = result["messages"][-1].content
```

**可直接复用的**：`tools.py` 的 5 个业务函数（普通函数照用）、`SYSTEM_PROMPT`、`build_registry`。
**被替代的**：`react.py` 整个循环、`llm.py` 的重试逻辑（langchain 内置）、`conversation.py`（checkpointer 接管）。
**暂时丢失的**：`AgentResult.trace`（推理过程展示）——需要改用 `agent.stream()` 逐步收集，Web 界面相应调整。

### 方案 B：StateGraph 手搭（把幻觉防御做成图节点，最能学到东西）

```python
from typing import Annotated
from langchain_core.messages import AnyMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from src.agent.prompts import SYSTEM_PROMPT
from src.mcp_tools.tools import build_registry

def llm_from_env():
    import os
    return ChatOpenAI(
        model="glm-4-flash", temperature=0.3,
        api_key=os.getenv("ZHIPU_API_KEY"),
        base_url="https://open.bigmodel.cn/api/paas/v4",
    )

# ① 状态：messages 用 add_messages reducer（自动追加而不是覆盖）
class AgentState(MessagesState):
    simulated_retry: bool   # 记录是否触发过幻觉纠正

# ② 节点
def call_model(state: AgentState):
    llm = llm_from_env().bind_tools([t.func for t in build_registry().tools.values()])
    return {"messages": [llm.invoke([SYSTEM_PROMPT] + state["messages"])]}

tools_node = ToolNode([t.func for t in build_registry().tools.values()])

def check_simulation(state: AgentState):
    """本项目特色：幻觉防御，做成条件边的判断函数。"""
    last = state["messages"][-1]
    if not getattr(last, "tool_calls", None) and looks_simulated(str(last.content)):
        return "retry"        # 条件边的返回值决定走哪条边
    return "continue"

def inject_nudge(state: AgentState):
    return {"messages": [("user", SIMULATED_ANSWER_NUDGE)]}

# ③ 组图：节点 + 边
g = StateGraph(AgentState)
g.add_node("agent", call_model)
g.add_node("tools", tools_node)
g.add_node("nudge", inject_nudge)
g.add_edge(START, "agent")
g.add_conditional_edges("agent", check_simulation,
                        {"continue": "tools", "retry": "nudge"})
g.add_edge("tools", "agent")     # 工具结果回给模型 —— 就是手写循环的"回到开头"
g.add_edge("nudge", "agent")
app = g.compile(checkpointer=InMemorySaver())
```

这张图就是：`agent → (要调工具? tools → agent) / (检测到幻觉模拟? nudge → agent) / END`。对照 [react.py](../src/agent/react.py) 的 `run()` 可以看到一一对应：for 循环 → 边循环；`looks_simulated` 判断 → 条件边；`SIMULATED_ANSWER_NUDGE` 注入 → `nudge` 节点。

---

## 六、动手练习建议

按难度递增（都基于本仓库现成代码）：

1. **读**：按顺序精读 `registry.py`(80行) → `llm.py`(100行) → `react.py`(170行)，对照本文第三节的说明。跑一遍测试看行为：
   ```bash
   ./run.sh -m pytest tests/test_stage4.py -v
   ```
2. **加法练习**：给 `FakeLLM` 加一个新脚本场景，验证幻觉防御分支（`looks_simulated` + 重试路径，参考 [test_stage4.py](../tests/test_stage4.py) 中相关用例的写法）。
3. **装依赖的对照实验**（不替换现有代码，新建文件）：写 `src/agent/langgraph_agent.py` 实现方案 A，同一问题对比手写版和框架版的 trace 输出。
4. **进阶**：实现方案 B，把幻觉防御迁移为条件边，体会"框架把控制流变成图"的好处（可视化、可中断、可持久化）。
5. **理解框架价值边界**：本项目的 `Conversation` 裁剪、`registry.call` 截断、幻觉防御，哪些是框架内置的？哪些仍要自己写？（答案藏在第四节对照表的"备注"列。）

> 学习心法：**先用最朴素的方式手写一遍（本项目已替你写好），再学框架**——你会清楚地知道每个框架 API 在替你做什么、代价是什么（黑盒程度、调试难度、版本变动），而不是被 `create_react_agent` 的魔法糊住眼睛。
