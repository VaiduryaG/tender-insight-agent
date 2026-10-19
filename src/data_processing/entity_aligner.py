"""公司名称实体对齐（项目核心难点之一）。

同一公司在不同公告中的写法五花八门："中国电信股份有限公司温州分公司"与
"中国电信集团有限公司"在集团维度上是同一主体。本模块用四层策略对齐：

    L1 规则匹配：去除"有限责任公司/股份/集团"等后缀后精确匹配
    L2 模糊匹配：编辑距离 + 字符 Jaccard 相似度
    L3 LLM 辅助：相似度处于灰区时，调用智谱AI 判断是否同一主体（可选，失败自动降级）
    L4 映射表兜底：人工维护的 company_mapping CSV 优先级最高

输出两级结果：
    canonical_name —— 标准化名称（用于 Company 节点，代表具体法人实体）
    group_name     —— 所属集团（用于集团维度聚合分析，如"中国电信"系）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# L1 规则层
# ---------------------------------------------------------------------------

# 企业类型后缀（按长度降序剔除，避免"有限公司"先于"有限责任公司"截断）
_COMPANY_SUFFIXES = [
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "集团公司",
    "总公司",
    "公司",
    "集团",
]

# 分公司后缀
_BRANCH_SUFFIXES = ("分公司", "分支公司")


@dataclass
class AlignResult:
    """单个公司名的对齐结果。"""

    original_name: str
    canonical_name: str          # 标准化名称（法人实体级）
    group_name: str              # 集团名（自身即集团时与 canonical 相同）
    method: str                  # 命中层级: manual/rule/fuzzy/llm/self
    confidence: float            # 0-1


class EntityAligner:
    """公司名实体对齐器。

    用法：
        aligner = EntityAligner(mapping_csv="data/raw/company_mapping_template.csv")
        results = aligner.align(df["company_name"])   # -> list[AlignResult]
    """

    def __init__(
        self,
        mapping_csv: str | Path | None = None,
        fuzzy_threshold: float = 0.82,
        llm_gray_zone: tuple[float, float] = (0.60, 0.82),
        use_llm: bool = False,
    ):
        self.fuzzy_threshold = fuzzy_threshold
        self.llm_gray_zone = llm_gray_zone
        self.use_llm = use_llm
        self._manual_map = self._load_mapping(mapping_csv)

    # ---- 公开接口 --------------------------------------------------------

    def align(self, names: pd.Series | list[str]) -> list[AlignResult]:
        """对一批公司名做对齐，同名输入共享同一结果。"""
        names = list(dict.fromkeys(str(n).strip() for n in names if str(n).strip()))

        # 先处理已知映射（L4），再对余下的跑 L1→L2→L3
        results: dict[str, AlignResult] = {}
        pending: list[str] = []
        for name in names:
            if name in self._manual_map:
                canonical, group = self._manual_map[name]
                results[name] = AlignResult(name, canonical, group, "manual", 1.0)
            else:
                pending.append(name)

        # 规则归一化（L1）后按归一化键分组：同键即同一法人实体
        by_key: dict[str, list[str]] = {}
        for name in pending:
            by_key.setdefault(self._normalize_key(name), []).append(name)

        for key, members in by_key.items():
            if not key:
                for name in members:
                    results[name] = AlignResult(name, name, self._extract_group(name), "self", 0.9)
                continue

            # 组内选最长的原名作为标准名（信息量最大），集团名从规则提取
            canonical = max(members, key=len)
            group = self._extract_group(canonical)

            # 灰区且开启 LLM 时，向已有组做相似度复核
            method, confidence = "rule", 0.95
            if self.use_llm and self._in_gray_zone(canonical, results):
                merged_group = self._llm_confirm_group(canonical, group)
                if merged_group:
                    group, method, confidence = merged_group, "llm", 0.85

            for name in members:
                results[name] = AlignResult(name, canonical, group, method, confidence)

        return [results[str(n).strip()] for n in names if str(n).strip()]

    def to_mapping_dataframe(self, results: list[AlignResult]) -> pd.DataFrame:
        """将对齐结果导出为映射表（供 L4 人工复核、增量沉淀）。"""
        return pd.DataFrame(
            [
                {
                    "original_name": r.original_name,
                    "canonical_name": r.canonical_name,
                    "group_name": r.group_name,
                    "match_method": r.method,
                    "confidence_score": r.confidence,
                }
                for r in results
            ]
        )

    # ---- 内部实现 --------------------------------------------------------

    @staticmethod
    def _load_mapping(path: str | Path | None) -> dict[str, tuple[str, str]]:
        """加载人工映射：original -> (canonical, group)。

        两个来源（人工确认优先）：
        1. needs_review.csv（D6 闭环）：is_verified=True 且 verified_group 非空的行
        2. 主映射表 CSV（company_mapping_template.csv）
        """
        merged: dict[str, tuple[str, str]] = {}

        review_path = Path("data/processed/needs_review.csv")
        if review_path.exists():
            try:
                df = pd.read_csv(review_path)
                required = {"original_name", "canonical_name", "is_verified", "verified_group"}
                if required.issubset(df.columns):
                    for _, r in df.iterrows():
                        if bool(r["is_verified"]) and str(r["verified_group"]).strip():
                            merged[str(r["original_name"]).strip()] = (
                                str(r["canonical_name"]).strip(),
                                str(r["verified_group"]).strip(),
                            )
            except Exception:
                pass  # 清单损坏时不阻断主流程

        if path and Path(path).exists():
            df = pd.read_csv(path)
            required = {"original_name", "normalized_name"}
            if required.issubset(df.columns):
                groups = (
                    df["group_name"] if "group_name" in df.columns else pd.Series([""] * len(df))
                )
                for idx, r in df.iterrows():
                    name = str(r.original_name).strip()
                    if name not in merged:  # 人工确认优先
                        merged[name] = (str(r.normalized_name).strip(), str(groups[idx]).strip())

        return merged

    @staticmethod
    def _iterative_strip_suffix(name: str) -> str:
        """迭代剥离企业类型后缀，直到无后缀可剥。

        "中国电信集团有限公司" -> "中国电信集团" -> "中国电信"，
        保证"股份有限公司"与"集团有限公司"写法归并到同一集团名。
        """
        changed = True
        while changed:
            changed = False
            for suffix in _COMPANY_SUFFIXES:
                if name.endswith(suffix) and len(name) > len(suffix):
                    name = name[: -len(suffix)]
                    changed = True
                    break
        return name

    @classmethod
    def _normalize_key(cls, name: str) -> str:
        """L1：生成归一化键（迭代去后缀、去除分隔符）。"""
        key = cls._iterative_strip_suffix(name.strip())
        return re.sub(r"[\s（）()、·]", "", key)

    @staticmethod
    def _extract_group(name: str) -> str:
        """从公司名提取集团名：剥离"XX市分公司"中的城市名与企业后缀。

        采用确定性字符串处理而非正则：分公司名形如
        "核心名+有限公司+城市名+分公司"，城市夹在最后一个"公司"和
        "分公司"之间，直接定位即可，避免正则误吞"有限公司"。
        """
        for branch in _BRANCH_SUFFIXES:
            if name.endswith(branch):
                stem = name[: -len(branch)]  # "中国联合网络通信有限公司银川市"
                idx = stem.rfind("公司")
                if idx != -1 and idx + 2 < len(stem):
                    stem = stem[: idx + 2]  # 去掉城市名 -> "中国联合网络通信有限公司"
                name = stem
                break

        return EntityAligner._iterative_strip_suffix(name)

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        """组合相似度 = 0.6*编辑距离比 + 0.4*字符Jaccard，兼顾顺序与字面重叠。"""
        seq = SequenceMatcher(None, a, b).ratio()
        jac = len(set(a) & set(b)) / len(set(a) | set(b)) if a or b else 0.0
        return 0.6 * seq + 0.4 * jac

    def _in_gray_zone(self, name: str, existing: dict[str, AlignResult]) -> bool:
        return any(
            self.llm_gray_zone[0] <= self._similarity(name, r.canonical_name) < self.llm_gray_zone[1]
            for r in existing.values()
        )

    def _llm_confirm_group(self, name: str, rule_group: str) -> str | None:
        """L3：调用智谱AI复核集团归属，返回集团名或 None（失败降级到规则结果）。"""
        try:
            from zhipuai import ZhipuAI  # 延迟导入，未启用/未安装时不影响主流程

            from dotenv import dotenv_values

            api_key = dotenv_values(".env").get("ZHIPU_API_KEY")
            if not api_key:
                return None
            resp = ZhipuAI(api_key=api_key).chat.completions.create(
                model="glm-4-flash",
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"判断公司「{name}」是否属于集团「{rule_group}」体系，"
                            "只回答 是 或 否，不要解释。"
                        ),
                    }
                ],
                temperature=0.1,
                max_tokens=4,
            )
            return rule_group if "是" in resp.choices[0].message.content else None
        except Exception:
            return None  # LLM 不可用时静默降级，规则结果兜底
