"""数据概览页：行业/地区/时间/技术 维度统计图表。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # 项目根

import pandas as pd
import plotly.express as px
import streamlit as st

from src.web.common import apply_theme, load_df, require_data_ready, sidebar_logo, styled_fig

st.set_page_config(page_title="数据概览", page_icon="📈", layout="wide")
apply_theme()

# 侧边栏：Logo 置顶 + 页面导航（与首页一致）
with st.sidebar:
    sidebar_logo()
    st.divider()
    st.page_link("app.py", label="🏠 首页")
    st.page_link("pages/1_数据概览.py", label="📈 数据概览")
    st.page_link("pages/2_知识图谱.py", label="🕸️ 知识图谱")
    st.page_link("pages/3_Agent对话.py", label="🤖 Agent 对话")
    st.divider()

st.title("📈 数据概览")

if not require_data_ready():
    st.stop()

df = load_df()
df["amount_wan"] = df["amount_normalized"] / 10_000
df["date"] = pd.to_datetime(df["announcement_date_normalized"])

# ---- 行业维度 ---------------------------------------------------------------
st.subheader("行业分布")
industry = (
    df.groupby("industry")
    .agg(项目数=("title", "count"), 总金额=("amount_wan", "sum"))
    .sort_values("总金额", ascending=False)
    .reset_index()
)
c1, c2 = st.columns(2)
c1.plotly_chart(
    styled_fig(
        px.pie(
            industry, names="industry", values="项目数", hole=0.55,
            color_discrete_sequence=px.colors.qualitative.Pastel,
        ).update_traces(textinfo="percent", textfont_size=12),
        height=360,
    ),
    width='stretch',
)
c2.plotly_chart(
    styled_fig(
        px.bar(
            industry.sort_values("总金额"),
            x="总金额", y="industry", orientation="h",
            labels={"总金额": "中标总金额（万元）", "industry": ""},
            color="总金额", color_continuous_scale=["#dbeafe", "#2563eb"],
        ).update_layout(coloraxis_showscale=False),
        height=360,
    ),
    width='stretch',
)

# ---- 地区维度 ---------------------------------------------------------------
st.subheader("地区分布")
region = (
    df.groupby("province")
    .agg(项目数=("title", "count"), 总金额=("amount_wan", "sum"))
    .sort_values("总金额", ascending=False)
    .reset_index()
)
c1, c2 = st.columns(2)
c1.plotly_chart(
    styled_fig(
        px.bar(
            region, x="province", y="项目数",
            labels={"province": "省份", "项目数": "项目数"},
            color="项目数", color_continuous_scale=["#dbeafe", "#2563eb"],
        ).update_layout(coloraxis_showscale=False),
        height=360,
    ),
    width='stretch',
)
c2.plotly_chart(
    styled_fig(
        px.bar(
            region, x="province", y="总金额",
            labels={"province": "省份", "总金额": "中标总金额（万元）"},
            color="总金额", color_continuous_scale=["#ede9fe", "#7c3aed"],
        ).update_layout(coloraxis_showscale=False),
        height=360,
    ),
    width='stretch',
)

# ---- 时间趋势 ---------------------------------------------------------------
st.subheader("公告时间趋势")
daily = df.groupby(df["date"].dt.strftime("%m-%d")).agg(
    项目数=("title", "count"), 总金额=("amount_wan", "sum")
).reset_index().rename(columns={"date": "日期"})
c1, c2 = st.columns(2)
c1.plotly_chart(
    styled_fig(
        px.line(daily, x="日期", y="项目数", markers=True,
                labels={"项目数": "项目数"}),
        height=320,
    ),
    width='stretch',
)
c2.plotly_chart(
    styled_fig(
        px.area(daily, x="日期", y="总金额",
                labels={"总金额": "中标总金额（万元）"}),
        height=320,
    ),
    width='stretch',
)

# ---- 金额与技术 ---------------------------------------------------------------
c1, c2 = st.columns(2)
with c1:
    st.subheader("中标金额分布")
    st.plotly_chart(
        styled_fig(
            px.histogram(
                df, x="amount_wan", nbins=12,
                labels={"amount_wan": "中标金额（万元）"},
                color_discrete_sequence=["#0891b2"],
            ),
            height=340,
        ),
        width='stretch',
    )
with c2:
    st.subheader("技术标签热度")
    tags = df.explode("tech_tags_list")["tech_tags_list"].dropna().value_counts().head(15)
    st.plotly_chart(
        styled_fig(
            px.bar(
                x=tags.values, y=tags.index, orientation="h",
                labels={"x": "出现次数", "y": ""},
                color=tags.values, color_continuous_scale=["#dcfce7", "#059669"],
            ).update_layout(coloraxis_showscale=False, yaxis=dict(autorange="reversed")),
            height=340,
        ),
        width='stretch',
    )

# ---- 明细表 ---------------------------------------------------------------
st.subheader("中标明细")
show_cols = ["title", "canonical_name", "amount_wan", "announcement_date_normalized", "industry", "province", "city"]
st.dataframe(
    df[show_cols].rename(columns={
        "title": "项目名称", "canonical_name": "中标公司", "amount_wan": "金额（万元）",
        "announcement_date_normalized": "公告日期", "industry": "行业", "province": "省", "city": "市",
    }).sort_values("金额（万元）", ascending=False),
    width='stretch',
    height=420,
    column_config={
        "金额（万元）": st.column_config.NumberColumn(format="%.1f"),
    },
    hide_index=True,
)
