"""查询改写（Query Rewrite）：把用户口语化提问翻译成检索友好的关键词。

RAG 检索优化的标准组件之一（另有 HyDE、Rerank）。解决的问题：
用户问"学校要购置一批器材，有没有相关招投标信息"——数据里没有一句
话能整串匹配；先让 LLM 把问题拆解成 3-6 个"可能字面出现在公告标题里"
的关键词（学校/器材/设备/采购…），Agent 调检索工具时就有词可用了。

设计约束：
- best-effort：改写失败（LLM 异常/输出为空/解析不出）一律返回 []，
  调用方退回原始问题——改写绝不能阻塞主流程
- 输出是"字面关键词"而非"改写后的问句"：search_projects 的 keyword
  匹配的是公告标题子串，问句形态没有检索价值
"""

from __future__ import annotations

import re

from src.agent.protocols import SupportsChat

REWRITE_PROMPT = """把用户的自然语言问题改写成适合在"政府采购中标公告标题"中检索的关键词。

要求：
- 输出 {max_keywords} 个以内的关键词，每行一个，不要编号、不要解释
- 关键词必须是可能在公告标题中字面出现的短词（例如：学校、器材、设备、采购、智慧教育）
- 覆盖问题的不同侧面：主体（谁在买）、客体（买什么）、同义词（近义说法）
- 不要输出动词短语或完整句子

用户问题：{question}"""

# 行首的编号/符号前缀（"1." "、" "-" "•"）与两侧标点
_LINE_PREFIX_RE = re.compile(r"^[\s\d\.\)、·•\-–—*]+")
_TRIM_CHARS = "，。；：、！？ \t\"'"


def _parse_keywords(content: str, max_keywords: int) -> list[str]:
    """LLM 输出 → 关键词列表（剥编号、去重、过滤过长/过短行）。"""
    keywords: list[str] = []
    for raw in content.splitlines():
        token = _LINE_PREFIX_RE.sub("", raw.strip()).strip(_TRIM_CHARS)
        # 中文检索词 2-12 字较合理；过短（单字）噪声大，过长是句子不是词
        if 2 <= len(token) <= 12 and token not in keywords:
            keywords.append(token)
        if len(keywords) >= max_keywords:
            break
    return keywords


def rewrite_query(llm: SupportsChat, question: str, max_keywords: int = 6) -> list[str]:
    """调用 LLM 把问题改写成检索关键词。任何失败返回 []（调用方回退原问题）。"""
    if not (question or "").strip():
        return []
    try:
        resp = llm.chat(
            [{"role": "user", "content": REWRITE_PROMPT.format(question=question, max_keywords=max_keywords)}],
            temperature=0.2,   # 低温度：同一问题改写结果稳定
            max_tokens=200,
        )
        content = resp.get("content") or ""
        return _parse_keywords(content, max_keywords)
    except Exception:
        return []
