"""知识图谱可视化页：Pyvis 交互式 ego 网络，支持公司/行业视角与展开深度。

渲染降噪三原则（针对"线条多、看着乱"的迭代结论）：
1. COMPETES_WITH（竞对两两全连接）默认隐藏，侧边栏开关打开才显示——
   它占子图边数 60%+，是"毛线球"观感的主因
2. 边标签默认不显示（悬停可见），箭头仅悬停显示——静态画面只剩干净的结构
3. 边缘低信息节点（地区/技术）淡化缩小，视觉焦点留给公司/项目/行业
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # 项目根

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

from src.knowledge_graph.schema import NodeType, node_id
from src.web.common import TYPE_COLORS, apply_theme, load_graph, require_data_ready, sidebar_logo

st.set_page_config(page_title="知识图谱", page_icon="🕸️", layout="wide")
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

st.title("🕸️ 知识图谱可视化")

if not require_data_ready():
    st.stop()

store = load_graph()
companies = [k for k, _ in store.nodes(NodeType.COMPANY)]
industries = [k for k, _ in store.nodes(NodeType.INDUSTRY)]

# ---- 侧边栏：图谱探索控制（导航之后） ---------------------------------------------
with st.sidebar:
    st.header("图谱探索")
    mode = st.radio("视角", ["公司", "行业"], horizontal=True)
    if mode == "公司":
        target_type = NodeType.COMPANY
        target = st.selectbox("选择公司", sorted(companies))
    else:
        target_type = NodeType.INDUSTRY
        target = st.selectbox("选择行业", sorted(industries))
    depth = st.slider("展开深度（跳数）", 1, 3, 2,
                      help="1=直接邻居；公司视角2可看到行业/技术/地区，3可看到竞对的公司")
    show_competes = st.toggle(
        "显示竞对关系线", value=False,
        help="竞对为公司两两连接，线条极多。默认关闭保持画面清爽；打开可查看竞争网络"
    )
    st.caption(
        "节点颜色：🔴 公司　🔵 项目　🟠 行业　🟢 技术　🟣 地区\n\n"
        "拖拽移动节点、滚轮缩放、**悬停节点/边查看详情**。"
    )

sub = store.subgraph(target_type, target, depth=depth)
if not sub["nodes"]:
    st.warning("该节点无邻居，请调整展开深度")
    st.stop()

# ---- 边过滤与统计 ---------------------------------------------------------------
ALL_EDGES = len(sub["edges"])
edges_drawn = [e for e in sub["edges"] if show_competes or e["type"] != "COMPETES_WITH"]
hidden_edges = ALL_EDGES - len(edges_drawn)

# ---- 构建 Pyvis 图 ---------------------------------------------------------------
nt = Network(height="620px", bgcolor="#ffffff", font_color="#334155", select_menu=False)
nt.barnes_hut(gravity=-8000, central_gravity=0.3, spring_length=120)

# 预聚合（IT-024 续）：行业/技术/地区节点本身无属性（金额/日期都在项目节点上），
# 悬停时从子图的边反向聚合出真实统计——行业=关联项目数+中标总额，
# 技术=需求项目数，地区=落地项目数。修"悬停只显示【行业】两个字"的问题
_proj_by_id = {n["id"]: n for n in sub["nodes"] if n["type"] == "Project"}
_industry_agg: dict[str, dict] = {}   # 行业节点id -> {count, amount}
_tech_agg: dict[str, int] = {}        # 技术节点id -> 需求项目数
_region_agg: dict[str, int] = {}      # 地区节点id -> 落地项目数
for e in sub["edges"]:
    if e["type"] == "BELONGS_TO":
        p = _proj_by_id.get(e["source_id"])
        agg = _industry_agg.setdefault(e["target_id"], {"count": 0, "amount": 0.0})
        if p is not None:
            agg["count"] += 1
            agg["amount"] += float(p.get("amount") or 0)
    elif e["type"] == "REQUIRES":
        _tech_agg[e["target_id"]] = _tech_agg.get(e["target_id"], 0) + 1
    elif e["type"] == "LOCATED_IN":
        _region_agg[e["target_id"]] = _region_agg.get(e["target_id"], 0) + 1

# 子图中实际出现在所画边上的节点才展示（竞对关闭时隐藏孤立的竞对公司）
drawn_node_ids = {e["source_id"] for e in edges_drawn} | {e["target_id"] for e in edges_drawn}
# 视角中心节点始终展示
center_id = node_id(target_type, target)
drawn_node_ids.add(center_id)

# 边类型 → 显示样式（中文标签/颜色/宽度）
EDGE_STYLE = {
    "WON": {"label": "中标", "color": "#dc2626", "width": 2.5},
    "BELONGS_TO": {"label": "属于行业", "color": "#d97706", "width": 1.6},
    "REQUIRES": {"label": "需要技术", "color": "#059669", "width": 1.4},
    "LOCATED_IN": {"label": "位于", "color": "#7c3aed", "width": 1.4},
    "COMPETES_WITH": {"label": "竞争", "color": "#cbd5e1", "width": 1.0},
}

for node in sub["nodes"]:
    ntype = node["type"]
    label = str(node["key"])
    if len(label) > 16:
        label = label[:15] + "…"
    # 悬停卡片：按节点类型挑 2-3 个关键字段，长文本（summary/title）不进悬浮框，
    # 避免"一连串数据"的观感；完整字段在下方「子图节点明细」表里看。
    # 注意（IT-024）：vis-network 9.x 对字符串 title 走 innerText 纯文本渲染，
    # <b>/<br> 等 HTML 标签会原样显示——必须用 \n 换行的纯文本
    type_names = {"Company": "公司", "Project": "项目", "Industry": "行业",
                  "Technology": "技术", "Region": "地区"}
    title_parts = [f"【{type_names.get(ntype, ntype)}】"]
    hover_fields = {
        "Company": ["group", "province"],
        "Project": ["amount", "date", "purchaser"],
        "Industry": [], "Technology": [], "Region": [],
    }.get(ntype, [])
    if ntype == "Project" and node.get("title"):
        title_parts.append(node["title"][:40] + ("…" if len(node["title"]) > 40 else ""))
    # 行业/技术/地区：预聚合的真实统计（节点自身无属性，金额都在项目上）
    if ntype == "Industry" and node["id"] in _industry_agg:
        agg = _industry_agg[node["id"]]
        title_parts.append(f"关联项目：{agg['count']} 个")
        if agg["amount"]:
            title_parts.append(f"中标总额：{agg['amount'] / 10_000:.1f} 万元")
    elif ntype == "Technology" and node["id"] in _tech_agg:
        title_parts.append(f"需求项目：{_tech_agg[node['id']]} 个")
    elif ntype == "Region" and node["id"] in _region_agg:
        title_parts.append(f"落地项目：{_region_agg[node['id']]} 个")
    for field in hover_fields:
        value = node.get(field)
        if value is None or value == "":
            continue
        if field == "amount":
            value = f"{float(value) / 10_000:.1f} 万元"
        field_names = {"group": "集团", "province": "省份", "amount": "金额", "date": "日期", "purchaser": "采购人"}
        title_parts.append(f"{field_names.get(field, field)}：{value}")
    title_lines = "\n".join(title_parts)

    size = 22 if ntype in ("Company", "Industry") else (16 if ntype == "Project" else 11)
    if node.get("type") == "Project" and node.get("amount"):
        size = 12 + min(10, node["amount"] / 1_000_000)  # 金额越大节点越大

    is_center = node["id"] == center_id
    is_edge_node = node["id"] in drawn_node_ids
    if is_center:
        opacity, border_w = 1.0, 3  # 中心节点加重描边
    elif is_edge_node:
        opacity, border_w = 1.0, 1
    else:
        opacity, border_w = 0.25, 0  # 被隐藏关系波及的节点淡化
    color = TYPE_COLORS.get(ntype, "#95a5a6")
    nt.add_node(
        node["id"], label=label, title=title_lines,
        color={
            "border": color if is_center else "#ffffff",
            "background": color,
            "highlight": {"border": "#0f172a", "background": color},
        },
        size=size + (4 if is_center else 0),
        opacity=opacity,
        borderWidth=border_w,
        font={
            "color": "#0f172a" if ntype in ("Company", "Industry", "Project") else "#94a3b8",
            "size": 13 if is_center else 12,
            "face": '"PingFang SC", "Microsoft YaHei", sans-serif',
        },
    )

for edge in edges_drawn:
    style = EDGE_STYLE.get(edge["type"], {"label": edge["type"], "color": "#94a3b8", "width": 1})
    nt.add_edge(
        edge["source_id"], edge["target_id"],
        # 标签放 hover title 而非常驻——常驻标签是杂乱感的第二来源
        title=f"{style['label']}",
        color={"color": style["color"], "highlight": "#0f172a", "opacity": 0.65},
        width=style["width"],
        arrows={"to": {"enabled": False, "scaleFactor": 0.6}},  # 静态无箭头，选中才显示
        hoverWidth=1.2,
    )

with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
    nt.write_html(f.name)
    html = Path(f.name).read_text(encoding="utf-8")

# ---- 顶部统计条 + 图渲染（stat-bar：小号副信息样式，见主题 CSS） -----------------------
company_display = target if len(target) <= 20 else target[:19] + "…"
st.markdown(
    f"""
<div class="stat-bar">
  <div class="stat-item"><span class="stat-label">节点数</span><span class="stat-value">{len(sub["nodes"])}</span></div>
  <div class="stat-item"><span class="stat-label">关系数</span><span class="stat-value">{len(edges_drawn)}</span></div>
  <div class="stat-item"><span class="stat-label">视角节点</span><span class="stat-value">{company_display}</span></div>
  <div class="stat-item"><span class="stat-label">展开深度</span><span class="stat-value">{depth} 跳</span></div>
</div>
""",
    unsafe_allow_html=True,
)

components.html(html, height=640, scrolling=False)

if hidden_edges:
    st.info(f"🔒 已隐藏 {hidden_edges} 条竞对关系线（可在左侧「显示竞对关系线」开关打开）")

# 节点明细展开
with st.expander("子图节点明细"):
    detail_cols = ["type", "key"] + [
        c for c in ("group", "amount", "date", "industry", "province")
        if any(c in n for n in sub["nodes"])
    ]
    st.dataframe(pd.DataFrame(sub["nodes"])[detail_cols], width='stretch', height=300, hide_index=True)
