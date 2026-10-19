"""招投标智能分析 Agent — Web 首页。

启动: ./run.sh -m streamlit run src/web/app.py
页面: pages/1_数据概览 · pages/2_知识图谱 · pages/3_Agent对话
侧边栏: Logo 置顶 + 带图标的页面导航（custom nav）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 项目根

import streamlit as st

from src.web.common import (
    apply_theme,
    headline_metrics,
    require_data_ready,
    sidebar_logo,
)

st.set_page_config(
    page_title="招投标智能分析 Agent",
    page_icon="📊",
    layout="wide",
)

apply_theme()

# ---- 侧边栏：Logo 置顶 + 带图标导航 ----------------------------------------------
with st.sidebar:
    sidebar_logo()
    st.divider()
    st.page_link("app.py", label="🏠 首页")
    st.page_link("pages/1_数据概览.py", label="📈 数据概览")
    st.page_link("pages/2_知识图谱.py", label="🕸️ 知识图谱")
    st.page_link("pages/3_Agent对话.py", label="🤖 Agent 对话")

# ---- Hero 横幅 ---------------------------------------------------------------
st.markdown(
    """
<div class="hero">
  <div class="hero-title">招投标智能分析 Agent</div>
  <div class="hero-sub">基于中国政府采购网公开中标公告 · 知识图谱 · 语义检索 · ReAct Agent</div>
  <div>
    <span class="hero-chip">🕸️ 知识图谱</span>
    <span class="hero-chip">🔎 语义检索</span>
    <span class="hero-chip">🤖 Agent 问答</span>
    <span class="hero-chip">🏢 集团聚合</span>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

if not require_data_ready():
    st.stop()

# ---- 核心指标 ---------------------------------------------------------------
metrics = headline_metrics()
cols = st.columns(len(metrics))
for col, (name, value) in zip(cols, metrics.items()):
    col.metric(name, value)

st.divider()

# ---- 功能导航 ---------------------------------------------------------------
c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("#### 📈 数据概览")
    st.caption("行业 / 地区 / 时间 / 技术多维统计图表")
    st.page_link("pages/1_数据概览.py", label="进入页面 →")
with c2:
    st.markdown("#### 🕸️ 知识图谱")
    st.caption("交互式公司 / 行业 ego 网络探索")
    st.page_link("pages/2_知识图谱.py", label="进入页面 →")
with c3:
    st.markdown("#### 🤖 Agent 对话")
    st.caption("自然语言问答，展示推理与工具调用")
    st.page_link("pages/3_Agent对话.py", label="进入页面 →")

st.divider()

# ---- 数据说明 ---------------------------------------------------------------
st.markdown("#### 数据说明")
st.markdown(
    """
- **数据来源**：中国政府采购网公开中标公告（人工采集种子 + 自动爬虫双源）
- **处理链路**：清洗标准化 → 公司名实体对齐（分公司归并集团）→ 知识图谱构建 → 语义向量化
- **实体对齐**：示例——「中国电信股份有限公司温州分公司」与「中国电信集团有限公司太原分公司」
  在图谱中是两个法人实体，但均归属集团 **中国电信**，可集团维度聚合分析
"""
)
