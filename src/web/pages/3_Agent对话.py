"""Agent 对话页：自然语言问答，展示推理过程与工具调用记录。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # 项目根

import streamlit as st

from src.agent.conversation import Conversation
from src.web.common import apply_theme, load_agent, sidebar_logo

st.set_page_config(page_title="Agent 对话", page_icon="🤖", layout="wide")

with st.sidebar:
    sidebar_logo()
    st.divider()
    st.page_link("app.py", label="🏠 首页")
    st.page_link("pages/1_数据概览.py", label="📈 数据概览")
    st.page_link("pages/2_知识图谱.py", label="🕸️ 知识图谱")
    st.page_link("pages/3_Agent对话.py", label="🤖 Agent 对话")

apply_theme()
st.title("🤖 Agent 对话")
st.caption("Agent 基于 ReAct 模式自主调用工具（公司画像 / 图谱查询 / 语义搜索 / 项目筛选 / 统计）回答问题")

agent_or_err = load_agent()
if isinstance(agent_or_err, Exception):
    st.error(f"Agent 初始化失败：{agent_or_err}")
    st.info("请确认 `.env` 中已配置 ZHIPU_API_KEY")
    st.stop()
agent = agent_or_err

# 会话状态初始化
if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []  # [(role, content)] 用于渲染
if "conversation" not in st.session_state:
    st.session_state.conversation = Conversation(max_messages=20)
if "traces" not in st.session_state:
    st.session_state.traces = {}  # answer -> trace_summary

EXAMPLES = [
    "中国电信在中标数据里的表现如何？",
    "智慧教育行业谁的金额最高？它有哪些竞争对手？",
    "帮我找找和校园信息化建设相关的项目",
    "物联网技术的需求热度怎么样？",
]

# 历史消息渲染
for role, content in st.session_state.chat_messages:
    with st.chat_message(role):
        st.markdown(content)

# 示例问题（仅在无历史时显示）
if not st.session_state.chat_messages:
    st.markdown("**试试这些问题：**")
    eq1, eq2 = st.columns(2)
    for col, q in zip((eq1, eq2), EXAMPLES[:2]):
        if col.button(q, width='stretch'):
            st.session_state.pending_question = q
            st.rerun()

question = st.chat_input("输入你的问题，例如：哪个集团的中标金额最高？")
if "pending_question" in st.session_state:
    question = st.session_state.pop("pending_question")

if question:
    st.session_state.chat_messages.append(("user", question))
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Agent 思考与调用工具中…（约10-30秒）"):
            try:
                result = agent.run(
                    question, history=st.session_state.conversation
                )
                answer = result.answer or "（Agent 未能生成回答，请重试）"
                st.session_state.traces[answer] = result.trace_summary()
                used_tools = result.used_tools
            except Exception as e:
                answer = f"⚠️ 出错了：{e}"
                used_tools = False
        if not used_tools and "⚠️" not in answer:
            st.warning("⚠️ 本次回答未经过工具数据查询，内容可能不可靠")
        st.markdown(answer)

        if answer in st.session_state.traces:
            with st.expander("🔍 查看推理过程（思考 → 工具调用 → 观察）"):
                st.text(st.session_state.traces[answer])

    st.session_state.chat_messages.append(("assistant", answer))
    if answer not in ("", ) and "⚠️" not in answer:
        st.session_state.conversation.add_exchange(question, answer)
    st.rerun()
