"""Web 公共模块：带缓存的数据/图谱/Agent 加载器。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 项目根

import pandas as pd
import streamlit as st

from src.knowledge_graph.queries import (
    graph_overview,
    group_aggregation,
    technology_trends,
)
from src.knowledge_graph.store import NetworkXStore

STAGE1_DATA = "data/processed/bidding_stage1.parquet"
GRAPH_PATH = "data/graph/bidding_graph.json"

# ---- 全局视觉主题 -------------------------------------------------------------

# 主色与图表分类色板（全站图表统一取色）
ACCENT = "#2563eb"
PALETTE = [
    "#2563eb", "#7c3aed", "#0891b2", "#059669", "#d97706",
    "#dc2626", "#db2777", "#65a30d", "#475569", "#7c2d12",
]

_THEME_CSS = """
<style>
/* 全局字体与留白 */
html, body, [class*="css"] {
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
}
.block-container { padding-top: 1.2rem; max-width: 1180px; }

/* 首页 Hero 横幅 */
.hero {
    background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 60%, #3b82f6 100%);
    border-radius: 16px; padding: 26px 32px; margin-bottom: 18px;
    color: #ffffff;
}
.hero-title { font-size: 1.7rem; font-weight: 700; letter-spacing: .02em; }
.hero-sub { margin-top: 6px; font-size: .92rem; opacity: .88; }
.hero-chip {
    display: inline-block; margin: 10px 8px 0 0; padding: 3px 12px;
    background: rgba(255,255,255,.16); border: 1px solid rgba(255,255,255,.28);
    border-radius: 999px; font-size: .78rem;
}

/* 指标卡：白底卡片 + 细边框 + 轻阴影 */
[data-testid="stMetric"] {
    background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px;
    padding: 14px 18px; box-shadow: 0 1px 3px rgba(15, 23, 42, .06);
}
[data-testid="stMetricLabel"] p { color: #64748b; font-size: .84rem; }
[data-testid="stMetricValue"] { color: #0f172a; font-weight: 700; }

/* 小节标题：左侧主色竖条 */
h2 { border-left: 4px solid #2563eb; padding-left: .6rem; }

/* 按钮：圆角 + hover 提亮 */
.stButton > button {
    border-radius: 10px; border: 1px solid #e2e8f0; color: #334155;
    background: #ffffff; transition: all .15s ease;
}
.stButton > button:hover { border-color: #2563eb; color: #2563eb; background: #eff6ff; }

/* 侧边栏浅色底 */
[data-testid="stSidebar"] { background: #f8fafc; }

/* expander 圆角 */
[data-testid="stExpander"] {
    border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden;
}

/* 隐藏 Streamlit 按 pages/ 目录自动生成的默认导航（与自定义导航重复） */
[data-testid="stSidebarNav"] { display: none; }

/* 侧边栏：字体整体加大、 Logo 置顶紧凑 */
[data-testid="stSidebar"] * { font-size: .95rem; }
[data-testid="stSidebar"] .nav-logo {
    padding: .4rem .2rem .2rem;
}
[data-testid="stSidebar"] .nav-logo .logo-title { font-size: 1.18rem; }

/* 图谱页统计条：小号副信息级别（自定义 HTML，避免 st.metric 大字号） */
.stat-bar {
    display: flex; gap: 12px; flex-wrap: wrap; margin: 2px 0 10px;
}
.stat-item {
    background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px;
    padding: 6px 14px; display: flex; align-items: baseline; gap: 8px;
    box-shadow: 0 1px 2px rgba(15, 23, 42, .05);
}
.stat-label { font-size: .78rem; color: #64748b; }
.stat-value { font-size: .95rem; font-weight: 600; color: #0f172a; }
</style>
"""


def apply_theme() -> None:
    """注入全局主题 CSS。每个页面入口调用一次。"""
    st.markdown(_THEME_CSS, unsafe_allow_html=True)


# 节点类型 → 展示颜色（与 PALETTE 同色系：公司红/项目蓝/行业橙/技术绿/地区紫）
TYPE_COLORS = {
    "Company": "#dc2626",
    "Project": "#2563eb",
    "Industry": "#d97706",
    "Technology": "#059669",
    "Region": "#7c3aed",
}


def styled_fig(fig, height: int = 340):
    """统一 Plotly 图表风格：字体/配色/网格/留白。"""
    fig.update_layout(
        font=dict(family='"PingFang SC", "Microsoft YaHei", sans-serif',
                  size=13, color="#334155"),
        plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
        colorway=PALETTE,
        margin=dict(l=10, r=16, t=44, b=10),
        height=height,
        title=dict(font=dict(size=15, color="#0f172a"), x=0.005, xanchor="left"),
        legend=dict(orientation="h", y=-0.12) if len(fig.data) > 1 else {},
    )
    fig.update_xaxes(gridcolor="#eef2f7", zerolinecolor="#e2e8f0", linecolor="#e2e8f0")
    fig.update_yaxes(gridcolor="#eef2f7", zerolinecolor="#e2e8f0", linecolor="#e2e8f0")
    return fig


_SIDEBAR_LOGO = """
<div class="nav-logo">
  <div class="logo-title">📊 招投标智能分析</div>
  <div style="font-size:.78rem;color:#64748b;margin-top:2px;">Bidding Intelligence Agent</div>
</div>
"""


def sidebar_logo() -> None:
    """侧边栏顶部品牌区。需在 `with st.sidebar:` 块内、页面第一个元素调用。"""
    st.markdown(_SIDEBAR_LOGO, unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def load_df() -> pd.DataFrame:
    return pd.read_parquet(STAGE1_DATA)


@st.cache_resource(show_spinner=False)
def load_graph() -> NetworkXStore:
    store = NetworkXStore()
    store.load(GRAPH_PATH)
    return store


@st.cache_resource(show_spinner=False)
def load_agent():
    """构建 LangGraph Agent（LLM + 工具注册表）。失败返回 None 并携带原因。

    Agent 实现为 LangGraph StateGraph（src/agent/graph_agent.py），
    与早期手写 ReAct 循环（react.py）接口完全一致，可互换。
    """
    try:
        from src.agent.graph_agent import LangGraphAgent
        from src.agent.llm import LLMClient
        from src.mcp_tools.tools import build_registry

        return LangGraphAgent(LLMClient(), build_registry(), max_steps=8, verbose=False)
    except Exception as e:
        return e


def require_data_ready() -> bool:
    """检查阶段1-2产物是否就绪，未就绪时渲染引导信息。"""
    ok = Path(STAGE1_DATA).exists() and Path(GRAPH_PATH).exists()
    if not ok:
        st.error("数据未就绪，请先运行：`./run.sh scripts/run_stage1.py` 和 `./run.sh scripts/build_graph.py`")
    return ok


def headline_metrics() -> dict:
    """首页核心指标。"""
    df = load_df()
    stats = graph_overview(load_graph())
    return {
        "中标项目": len(df),
        "公司（法人实体）": stats["nodes"].get("Company", 0),
        "覆盖行业": stats["nodes"].get("Industry", 0),
        "技术标签": stats["nodes"].get("Technology", 0),
        "中标总金额（万元）": round(df["amount_normalized"].sum() / 10_000, 1),
        "图谱关系总数": sum(stats["edges"].values()),
    }


def top_groups(top: int = 10) -> list[dict]:
    return group_aggregation(load_graph())[:top]


def tech_trend(top: int = 15) -> list[dict]:
    return technology_trends(load_graph(), top)
