# LangGraph 迁移版学习报告（graph_agent.py 精读）

> 面向初学者：项目已完成 LangGraph 迁移，本报告逐行精读新版实现，并随时与手写版（react.py）对照。
> 前置阅读：[langchain_langgraph_study.md](langchain_langgraph_study.md)（旧报告讲"框架是什么、对应关系"；本报告讲"迁移后代码怎么读"）。

---

## 目录

1. [迁移了什么：现状总览](#一迁移了什么)
2. [一张图看懂新版架构](#二一张图看懂新版架构)
3. [逐段精读 graph_agent.py（含传入传出）](#三逐段精读)
4. [关键设计点深挖](#四关键设计点深挖)
5. [手写版 ↔ LangGraph 版 逐行对照](#五逐行对照)
6. [测试怎么验证的](#六测试怎么验证的)
7. [值得学习的点与可改进点](#七值得学习的点与可改进点)
8. [动手练习建议](#八动手练习建议)

---

## 一、迁移了什么

| 事项 | 状态 |
|------|------|
| 新文件 [src/agent/graph_agent.py](../src/agent/graph_agent.py)（291 行） | `LangGraphAgent` —— 基于 `StateGraph` 的 ReAct Agent，**对外接口与手写版完全一致** |
| Web 层接入 | [common.py:163-174](../src/web/common.py#L163-L174) `load_agent()` 已切换为 `LangGraphAgent(LLMClient(), build_registry())` |
| requirements.txt | `langchain>=1.0.0`、`langgraph>=1.0.0`、`langchain-openai>=1.0.0`（本次是真用了，不再是空声明） |
| 新测试 [tests/test_stage4b_langgraph.py](../tests/test_stage4b_langgraph.py)（198 行，9 个用例） | FakeLLM 替身，零 API 消耗，**31 个测试全部通过**（含旧版 22 个） |
| 旧文件 [src/agent/react.py](../src/agent/react.py) | **保留**。原因：① `AgentResult` 和 `looks_simulated` 被新版复用；② 手写版留在测试里作为行为基准。`__init__.py` 仍导出 `ReActAgent`（手写版仍是可用的备选实现） |

**迁移策略**：不是推倒重来，而是"换引擎不换方向盘"——`run(question, history) -> AgentResult` 签名一字不改，Web 层、MCP 层、测试全部无感切换。这是框架迁移的标准做法（适配层模式）。

---

## 二、一张图看懂新版架构

源码文件头的注释画了架构图（[graph_agent.py:3-12](../src/agent/graph_agent.py#L3-L12)），展开版如下：

```
                          START
                            │
                            ▼
                    ┌──────────────┐
              ┌────►│  agent 节点   │  LLM 推理（agent_node）
              │     │  注入 system  │  输出 AIMessage
              │     └──────┬───────┘  可能带 tool_calls
              │            │
              │     route_after_agent（条件边，读最后一条消息）
              │            │
              │   ┌────────┼─────────────┐
              │   │        │             │
              │   ▼        ▼             ▼
              │ "tools"  "nudge"        END
              │   │        │        （正常最终回答）
              │   ▼        ▼
              │ tools节点  nudge节点
              │ ToolNode   保留假回答
              │ 批量执行工具 注入纠正消息
              │   │        │
              └───┴────────┘   （两条边都回到 agent）
```

三条路径对应三种真实行为：

1. **工具循环**：agent 输出 tool_calls → tools 执行 → 结果回给 agent → 反复，直到模型不再要求调工具
2. **幻觉防御**：agent 给出"最终回答"但带 `Thought:/Action:/Observation:` 模拟特征 且 从未真正调过工具 且 没重试过 → nudge 注入纠正消息 → 回到 agent 重新回答（只重试一次）
3. **正常结束**：最终回答无模拟特征（或已重试过/调用过工具）→ END

---

## 三、逐段精读

整个 [graph_agent.py](../src/agent/graph_agent.py) 分四段：**工具适配**（48-105 行）→ **状态定义**（110-115 行）→ **Agent 主体**（120-261 行）→ **消息转换**（264-291 行）。

### 3.1 `registry_to_base_tools(registry) -> list`（[48-105 行](../src/agent/graph_agent.py#L48-L105)）

**要解决的问题**：LangGraph 的 `ToolNode` 只认 langchain 的 `BaseTool` 对象，不认本项目的 `ToolRegistry`（裸 dict schema + Python 函数）。需要一座"桥"。

**传入**：`ToolRegistry`（手写版工具箱，里面有 5 个工具的 name/description/JSON Schema/func）
**传出**：`list[BaseTool]` —— 每个工具包装成 langchain 标准工具对象

分三步：

#### 第一步：JSON Schema → pydantic 模型

**`_make_args_model(name, schema) -> type[BaseModel]`**（[62-73 行](../src/agent/graph_agent.py#L62-L73)）
- 传入：工具名、手写的 JSON Schema（来自 [tools.py](../src/mcp_tools/tools.py) 的 `TOOL_DEFS`）
- 传出：动态生成的 pydantic 模型类（如 `search_projects_args`）
- 做什么：遍历 schema 的 `properties`，按 `JSON_TYPE_MAP` 把 `"string"/"number"/"integer"` 等 JSON 类型映射成 Python 类型（`str/float/int`）：
  - **必填字段**：`(ann, Field(..., description=...))` —— `...` 表示无默认值即必填
  - **非必填字段**：`(ann | None, Field(default=None, ...))` —— 给默认 `None`
- **为什么需要**（[71 行注释](../src/agent/graph_agent.py#L71)）：不处理的话 pydantic 会把所有字段标成 required，模型只传必填参数时工具执行会报错。测试 `test_registry_to_base_tools_respects_required` 专门验证了这点。

> 💡 对照：这正是旧报告说的"langchain 的 `@tool` 装饰器能从函数签名自动生成 schema"。这里因为要复用已有 JSON Schema，反向手写了一个转换器。如果从零开始，直接用 `@tool` 就不需要这 20 行。

#### 第二步：动态创建 BaseTool 子类

（[75-104 行](../src/agent/graph_agent.py#L75-L104)）

```python
tool_cls = type(f"_Tool_{t.name}", (BaseTool,), ns)   # 动态造类
tools.append(tool_cls())                              # 实例化
```

- **为什么用 `type()` 动态造类**：`BaseTool` 要求 `name`、`args_schema` 是**类属性**（pydantic 校验发生在类定义时），而每个工具的这些值不同，只能每个工具造一个子类。
- `_run(self, **kwargs)`（[83-84 行](../src/agent/graph_agent.py#L83-L84)）是关键：
  ```python
  def _run(self, **kwargs):
      return self._registry.call(self._tool_name, json.dumps(kwargs, ensure_ascii=False))
  ```
  **执行不复制**——仍然委托给 `registry.call()`。tools.py 依旧是唯一工具逻辑所在地，工具报错转 JSON、4000 字符截断等手写版的防御逻辑原样生效。
- [86 行注释](../src/agent/graph_agent.py#L86)解释了一个 pydantic 冷知识：动态加的私有属性（`_registry`、`_tool_name`）也必须在 `__annotations__` 里声明，否则 pydantic 忽略/报错。

> ⚡ 更简的替代（学习时值得知道）：langchain 提供 `StructuredTool.from_function(func=..., name=..., description=..., args_schema=args_model)`，三行就能包装一个函数，不需要 `type()` 动态造类。本项目的写法更通用（能在 `_run` 里访问 registry 上下文），但也更绕。

#### ⚡ 对照 LangChain 原生写法

```python
from langchain_core.tools import tool

@tool
def search_projects(industry: str | None = None, ...) -> dict:
    """按行业/省份/关键词筛选项目。"""
    ...  # 函数体不变，schema 自动生成，无需 registry_to_base_tools
```

---

### 3.2 `AgentState`——图的状态定义（[110-115 行](../src/agent/graph_agent.py#L110-L115)）

```python
class AgentState(dict):
    messages: Annotated[list, add_messages]
    used_tools: bool
    retried: bool
```

**LangGraph 最核心的概念**：图在节点之间流转时携带的"共享内存"就是 State。每个节点收到 state、返回 `dict` 类型的**增量更新**，LangGraph 负责合并。

- `messages` 的 `Annotated[list, add_messages]`：`add_messages` 是 **reducer（归并函数）**——节点返回 `{"messages": [新消息]}` 时，langgraph 不是覆盖旧列表，而是**追加**（并按 id 去重）。这就是 ReAct 循环里对话历史能越滚越长的机制。
- `used_tools: bool`、`retried: bool`：普通字段（无 reducer），节点返回时直接覆盖。用于幻觉防御的判定。

**传入**：`run()` 的初始值 `{"messages": [...], "used_tools": False, "retried": False}`
**传出**：`_graph.invoke(...)` 返回跑完后的最终 state（含全部消息累积）。

> ⚡ 对照手写版：手写版的"状态"就是那个局部变量 `messages: list[dict]`，靠闭包在 for 循环里传递。LangGraph 把它显式化为一个类，好处是任何节点都能读全量状态、可以持久化/恢复（加 checkpointer 即可）、可以可视化。

---

### 3.3 `LangGraphAgent` 类

#### `__init__(self, llm, registry, max_steps=8, system_prompt=..., verbose=False)`（[128-147 行](../src/agent/graph_agent.py#L128-L147)）

- 传入签名与 `ReActAgent.__init__` **完全一致**——这是能无缝互换的前提
- 做三件事：
  1. `self._model = RunnableLambda(self._chat_via_protocol)` —— 把"dict 协议的 chat 调用"包成 langchain 的 Runnable
  2. `self._tools = ToolNode(registry_to_base_tools(registry))` —— langgraph 官方的工具执行节点
  3. `self._graph = self._build_graph()` —— 编译出可执行的图

#### `_chat_via_protocol(self, messages) -> AIMessage`（[151-165 行](../src/agent/graph_agent.py#L151-L165)）

**适配器**：langchain 世界（消息对象）↔ 本项目世界（dict 协议）的翻译官。
- 传入：langchain 消息对象列表
- 传出：`AIMessage`（langchain 格式），其 `tool_calls` 已解析成结构化的 `[{"name", "args", "id", "type"}]`
- 内部：`_lc_to_dict` 转成 dict → 调 `self.llm.chat(dict_msgs, tools=self.registry.schemas())`（**还是原来那个 chat 接口**，所以 `LLMClient` 和 `FakeLLM` 都能用）→ 把返回的 tool_calls 重新包装
- **注意**：这里没有用 `ChatOpenAI`（langchain 官方模型类），而是保留自研 `LLMClient`。好处：`_clean_content`（截断智谱异常流式拼接）、指数退避重试这些踩坑换来的逻辑不丢；代价：少用了一点框架能力。

#### `_build_graph(self) -> CompiledGraph`（[169-212 行](../src/agent/graph_agent.py#L169-L212)）

图的装配现场，三个节点 + 三种边：

**`agent_node(state) -> dict`**（[172-178 行](../src/agent/graph_agent.py#L172-L178)）
- 读 state 的 messages，若第一条不是 SystemMessage 就注入（[175-176 行](../src/agent/graph_agent.py#L175-L176) 注释说明：保证 nudge 重试轮次也带 system）
- 调 `self._model.invoke(msgs)` 得到 AIMessage
- 返回 `{"messages": [ai]}` → 由 `add_messages` reducer 追加进 state

**`tools_node(state) -> dict`**（[180-182 行](../src/agent/graph_agent.py#L180-L182)）
- 直接委托 `self._tools.invoke(state)`（LangGraph 官方 ToolNode）
- **ToolNode 自动做的事**：读最后一条 AIMessage 的所有 tool_calls → 逐个执行（走我们包装的 `_run` → `registry.call`）→ 每个结果包成 `ToolMessage` 追加进 messages
- 额外返回 `"used_tools": True` —— 幻觉防御的判据在这里被点亮

**`nudge_node(state) -> dict`**（[184-193 行](../src/agent/graph_agent.py#L184-L193)）
- 取最后一条消息（被判为"模拟"的假回答），返回它自己 + 一条 `SIMULATED_ANSWER_NUDGE` user 消息
- 手写版是把假回答追加进 messages 后重跑循环；图版把这一步变成了**显式的图节点**——这是本次迁移最有教学价值的地方：**控制流逻辑变成了图上的一等公民**
- 同时置 `"retried": True`，配合条件边保证只重试一次

**`route_after_agent(state) -> str`**（[195-203 行](../src/agent/graph_agent.py#L195-L203)）——条件边的路由函数
- 传入：当前 state
- 传出：**字符串边名**（`"tools"` / `"nudge"` / `END`）
- 判定逻辑：
  1. 最后一条是 AIMessage 且带 tool_calls → 走 `tools`
  2. 否则是最终回答；若 `looks_simulated(answer)` 且 `not used_tools` 且 `not retried` → 走 `nudge`
  3. 其余 → `END`

**装配**（[205-212 行](../src/agent/graph_agent.py#L205-L212)）：
```python
g.add_node("agent", agent_node)           # 注册节点
g.add_node("tools", tools_node)
g.add_node("nudge", nudge_node)
g.add_edge(START, "agent")                # 普通边：固定走向
g.add_conditional_edges("agent", route_after_agent,   # 条件边：路由函数决定
                        {"tools": "tools", "nudge": "nudge", END: END})
g.add_edge("tools", "agent")              # 工具结果回模型 —— 手写版循环的"回到开头"
g.add_edge("nudge", "agent")              # 纠正后重试
return g.compile()                        # 编译为可执行图
```

#### `run(self, question, history=None) -> AgentResult`（[216-235 行](../src/agent/graph_agent.py#L216-L235)）

- 传入/传出与手写版 `run` 签名一致（`Conversation` 对象照旧）
- 流程：拼初始消息（system + 历史 + user 问题）→ 一次性 `self._graph.invoke(...)` 交给 langgraph 跑完整个循环
- **`recursion_limit` 的换算**（[225 行](../src/agent/graph_agent.py#L225)）：
  ```python
  config={"recursion_limit": self.max_steps * 2 + 6}
  ```
  `recursion_limit` 数的是**图节点执行次数**，不是"模型推理轮次"。一轮工具循环 = agent + tools 两个节点，所以 `max_steps`（模型轮次）×2；+6 是 nudge 重试路径的余量。**这是初学 langgraph 最容易踩的坑**：直接把 max_steps 传给 recursion_limit，循环会在一半处被砍断。
- steps 的统计：数最终 state 里的 AIMessage 条数（每轮推理一条）

#### `_extract(self, messages) -> (answer, trace, used_tools)`（[239-261 行](../src/agent/graph_agent.py#L239-L261)）

langgraph 跑完只给你一串消息，但 Web 界面需要旧版的 `trace` 结构（thought/tool_calls/observation）。这个函数做**逆映射**：
- 遍历最终消息列表：AIMessage → trace 记录思考与调用（无 tool_calls 的即最终回答）；ToolMessage → 提取观察结果（截断 200 字符）回填到上一条 trace 记录
- ToolMessage 的出现同时点亮 `used_tools`
- 传出：`(answer: str, trace: list[dict], used_tools: bool)` → 组装成手写版同一个 `AgentResult`

> ⚡ 对照：手写版是在循环里**边跑边记** trace；图版是跑完后从消息序列**事后重建**。langgraph 原生的对应方案是 `agent.stream()` 逐步产出（流式），本项目选择重建，为了不动 Web 层代码。

#### `_lc_to_dict(m) -> dict`（[264-291 行](../src/agent/graph_agent.py#L264-L291)，模块级函数）

消息对象 → dict 的字段级映射。**最重要的注释在 [267-269 行](../src/agent/graph_agent.py#L267-L269)**：

> langchain 的 `m.type` 是 `'human'/'ai'/'tool'`，而 OpenAI 兼容 API 要求 `'user'/'assistant'/'tool'`——直接透传 type 会触发智谱 400 '角色信息不正确'（错误码 1214）。

这是本次迁移踩的真实坑：`HumanMessage` → `user`、`AIMessage` → `assistant` 必须显式映射。测试 `test_lc_to_dict_maps_langchain_roles_to_openai` 是这条的回归测试。同时它还负责把 AIMessage 的 `tool_calls`（langchain 已解析的 `name/args` 结构）**反序列化回** OpenAI 格式的 `function.arguments` JSON 字符串——因为底层还是走智谱的 OpenAI 兼容协议。

---

## 四、关键设计点深挖

### 4.1 适配层 sandwiches 结构

```
langgraph 图（框架世界，消息对象）
   │ agent_node 调 _model.invoke()
   ▼
_chat_via_protocol + _lc_to_dict（翻译层，唯一知道两个世界的地方）
   │ llm.chat(dict, tools)          registry.call(name, args)
   ▼
LLMClient（智谱API）               ToolRegistry → tools.py（本项目世界，纯 dict/str）
```

两个世界的复杂度都被关在翻译层里：`LLMClient`、`ToolRegistry`、`tools.py`、测试替身**一行没改**。读代码时抓住这个分界，就不会迷路。

### 4.2 为什么没用 `create_react_agent()` 一行流

langgraph 有 prebuilt 的 `create_react_agent(llm, tools, prompt=...)`（旧报告方案 A），本项目没用，而是手搭 StateGraph。因为**幻觉防御的条件边是定制控制流**——prebuilt 只支持"有 tool_calls → tools，无 → END"的固定形状，插不进 nudge 分支。这就是"框架 prebuilt vs 手搭图"的经典取舍：prebuilt 省事，定制控制流必须下到 StateGraph 层。

### 4.3 状态里三个字段的分工

| 字段 | 类型/reducer | 谁写 | 谁读 | 作用 |
|------|-------------|------|------|------|
| `messages` | `add_messages`（追加） | agent/tools/nudge | 全部节点 | 对话主体 |
| `used_tools` | 覆盖 | tools_node（True） | route_after_agent | 幻觉判据：真调过工具就不算模拟 |
| `retried` | 覆盖 | nudge_node（True） | route_after_agent | 保证 nudge 只走一次（防死循环） |

---

## 五、逐行对照

| 手写版 [react.py](../src/agent/react.py) | 图版 [graph_agent.py](../src/agent/graph_agent.py) | 差异说明 |
|---|---|---|
| `_run_loop` 的 for 循环 | `agent ↔ tools` 边循环 | for 变成了图循环 |
| `if not tool_calls: return` | `route_after_agent` 返回 END | 分支变条件边 |
| `registry.call(name, args)` 逐个执行 | `ToolNode` 批量执行（内部仍到 `registry.call`） | 执行逻辑未复制 |
| run 尾部：`looks_simulated` → 注入 NUDGE → 重跑 `_run_loop` | `nudge` 节点 + 条件边回 agent | 事后补救变图内路径 |
| 手动防重复重试（无，靠 used_tools 判断） | `retried` 状态字段显式防重 | 图版更严谨 |
| `max_steps` 直接限循环轮数 | `recursion_limit = max_steps*2 + 6` | **单位不同**：节点次数 vs 模型轮次 |
| 边跑边记 trace | `_extract` 事后从消息重建 | 数据结构完全对齐 |
| `messages: list[dict]` 闭包传递 | `AgentState` + `add_messages` reducer | 状态显式化 |
| `FakeLLM` 直接注入 | `FakeLLM` 经 `_chat_via_protocol` 注入 | 替身复用（协议不变） |
| `SYSTEM_PROMPT` 在 run() 开头拼 | `agent_node` 内检查注入（重试轮也有） | 图版修复了一个边角：手写版重跑 `_run_loop` 时 system 还在 messages 里所以没事，图版消息流转多次，在节点内兜底注入 |

**行为兼容性验证**：`test_stage4b_langgraph.py` 的 4 个完整循环用例（正常循环/幻觉重试/调用过工具不重试/直答）与 `test_stage4.py` 的同名场景断言一致——同一套 FakeLLM 脚本喂两个实现，产出相同的 `AgentResult`。

---

## 六、测试怎么验证的

[test_stage4b_langgraph.py](../tests/test_stage4b_langgraph.py) 分四组，全部用 FakeLLM（零 API 费用）：

| 组 | 用例 | 验证什么 |
|---|---|---|
| 消息映射 | `test_lc_to_dict_*`（2个） | langchain type → OpenAI role 的坑（智谱 400 回归） |
| 工具适配 | `test_registry_to_base_tools_*`（2个） | 非必填参数不被标 required；执行真的委托回 registry |
| 完整循环 | `test_langgraph_agent_*`（5个） | 工具循环/幻觉防御/防御豁免/直答/历史注入 |
| pydantic 回归 | `test_langgraph_agent_non_required_tool_param` | 模型只传必填参数时图流程不崩 |

推荐精读 `test_langgraph_agent_simulation_defense`（[136-148 行](../tests/test_stage4b_langgraph.py#L136-L148)）：FakeLLM 第一轮返回编造的"Thought:...Observation:..."文本，断言 `retried_for_simulation is True` 且最后一轮收到的消息里有 `SIMULATED_ANSWER_NUDGE`——把条件边 + nudge 节点 + 状态字段三者的协作全部验证到了。

运行：
```bash
./run.sh -m pytest tests/test_stage4b_langgraph.py tests/test_stage4.py -v   # 31 passed（已验证）
```

---

## 七、值得学习的点与可改进点

### 做得好的（可直接抄走的设计）

1. **接口不变迁移**：`run(question, history) -> AgentResult` 签名不动，Web/MCP/测试零改动。迁移的正确姿势。
2. **执行逻辑不复制**：`_run` 委托 `registry.call`，工具的唯一实现仍在 tools.py——两版 Agent 永远行为一致，不会出现"改了 A 忘了 B"。
3. **踩坑注释**：1214 角色映射坑、pydantic 注解坑、recursion_limit 换算，全部写进了源码注释 + 回归测试。**踩坑→注释→测试**的闭环是个人项目的宝贵资产。
4. **测试替身穿透框架**：`SupportsChat` 协议在图版继续生效——框架没进来之前 FakeLLM 能用，框架进来之后照样能用（靠 `_chat_via_protocol` 这层）。

### 可改进/后续可学（不改变现状的前提下）

1. **工具适配可简化**：`registry_to_base_tools` 的 `type()` 动态造类可换成 `StructuredTool.from_function`（更短），或干脆逐步把 tools.py 的函数迁到 `@tool` 装饰器风格（schema 自动生成，`TOOL_DEFS` 可删）。
2. **未用 checkpointer**：`Conversation` 仍是手动的。加 `InMemorySaver` + thread_id，多轮记忆就能交给框架（旧报告 3.5 节的对照点），`conversation.py` 可退役。
3. **trace 可用原生流式**：`agent.stream()` 逐步产出节点事件，比 `_extract` 事后重建更"框架原生"，但需要改 Web 层——适合作为下一阶段练习。
4. **LLMClient 与 langchain 的取舍**：目前 dict 协议 + 翻译层是稳妥选择；若想体验框架原生，可换 `ChatOpenAI(base_url=智谱)`，但要自己补回 `_clean_content` 的防污染逻辑（那是个真实踩坑，删之前先读 llm.py:87-100 的注释）。

---

## 八、动手练习建议

按难度递增（环境已就绪，`langchain 1.4.0 / langgraph 1.2.11` 都在 .venv 里）：

1. **读 + 跑**：对照本报告第三节精读 [graph_agent.py](../src/agent/graph_agent.py)，跑通 31 个测试。在 `_build_graph` 里临时加一行 `print(state)` 观察每步 state 的样子。
2. **可视化图**：`print(self._graph.get_graph().draw_ascii())`——langgraph 自带图结构打印，直观看到 agent/tools/nudge 的拓扑，与第二节的图对照。
3. **加一条边**：给图加一个 `log_node`（记录每次工具调用的次数到 state），体会"加逻辑 = 加节点/边"的开发模式。
4. **checkpointer 实验**：给 `compile(checkpointer=InMemorySaver())`，用 `thread_id` 连续 `invoke` 两次，验证第二次能记住第一次——然后思考 `Conversation` 是否可以退役。
5. **对照实验**：把 `common.load_agent()` 切回 `ReActAgent`，同一问题对比两个版本的 `trace_summary()` 输出（接口一致所以能直接互换）——这是检验"行为对齐"的最直接方式。

> 学习心法：这个迁移版最大的价值是让你看到**同一种 Agent 控制流的两种表达**——for 循环（命令式）和 StateGraph（声明式图）。框架没有魔法，`create_react_agent` 内部就是你手写的那个循环；而当你需要定制分支（幻觉防御）时，图的表达力反而更清晰。
