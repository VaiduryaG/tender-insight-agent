"""公告正文字段提取（D4）：从原始正文提取 中标供应商/金额/采购人/项目名/编号。

三层策略（按优先级，命中即止，每字段记录来源便于质量分析）：
1. table_zone  —— "三、中标（成交）信息"表格区：表头行后按扁平化顺序取数据行
2. kv_zone     —— 全文"键行\\n值行"扫描（公告概要区即此结构）
3. inline      —— "键：值"同行冒号写法

提取失败 → 字段置 None 并进 missing（needs_review），不猜测。
金额统一交 normalizer.normalize_amount 归一化为元。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.data_processing.normalizer import normalize_amount

# ---------------------------------------------------------------------------
# 键词典（键行的标准写法 → 统一字段名）。匹配时行 strip 后全等或以"键"开头带冒号
# ---------------------------------------------------------------------------

KEY_ALIASES: dict[str, str] = {
    # 供应商（多别名，按优先级排列）
    "中标供应商名称": "supplier",
    "中标（成交）供应商名称": "supplier",
    "成交供应商名称": "supplier",
    "供应商名称": "supplier",
    # 金额
    "总中标金额": "amount",
    "总成交金额": "amount",
    "中标金额": "amount",
    "成交金额": "amount",
    "中标（成交）金额": "amount",
    "中标（成交）金额(元)": "amount",
    # 其他字段
    "采购项目名称": "project_name",
    "项目名称": "project_name",
    "采购单位": "purchaser",
    "采购单位名称": "purchaser",
    "采购人名称": "purchaser",
    "采购人": "purchaser",
    "行政区域": "region",
    "公告时间": "announce_time",
    "项目编号": "project_id",
}

# 序号前缀/尾冒号归一化（"一、项目编号：" → "项目编号"）
_SECTION_PREFIX_RE = re.compile(r"^(?:[一二三四五六七八九十]+、|\d+\.)\s*")

# 金额值行的"总价"模式（"单价：A（元），总价：B（元）"——取总价而非单价）
_TOTAL_PRICE_RE = re.compile(r"总价：([\d,]+(?:\.\d+)?)（元）")

# 表头词（表格区连续出现的列名，值判定时须跳过）
_TABLE_HEADERS = {
    "序号", "中标（成交）金额(元)", "中标供应商名称", "供应商名称",
    "中标供应商地址", "供应商地址", "评审总得分", "备注",
}

# 供应商名判定：以企业/机构后缀结尾
_SUPPLIER_SUFFIX_RE = re.compile(
    r"[一-龥A-Za-z0-9（）()·]+(?:有限公司|有限责任公司|集团|股份公司|中心|研究院|研究所"
    r"|事务所|医院|学校|大学|学院|合作社|分公司|管理局|委员会)"
)

# 表格金额：'单价：X（元），总价：Y（元）' 或 '总价：X（元）'
_TABLE_AMOUNT_RE = re.compile(r"总价：([\d,]+(?:\.\d+)?)（元）")

# 行内"键：值"（如 供应商名称：XX公司）
_INLINE_KV_RE = re.compile(r"^(.*?)[:：]\s*(.+)$")


@dataclass
class ExtractionResult:
    """单篇正文提取结果。"""

    fields: dict = field(default_factory=dict)    # supplier/amount/project_name/purchaser/region/project_id/announce_time
    amount_normalized: float | None = None        # 金额归一化（元）
    sources: dict = field(default_factory=dict)   # 字段 -> table_zone/kv_zone/inline
    missing: list[str] = field(default_factory=list)  # 未能提取的字段（needs_review）


class FieldExtractor:
    """正文规则提取器。纯文本输入输出，无网络无IO，可单测。"""

    def extract(self, body: str) -> ExtractionResult:
        lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
        result = ExtractionResult()

        kv = self._scan_kv(lines)
        table = self._parse_table_zone(lines)

        # ---- 供应商：表格区 > 键值 > 行内冒号 --------------------------------
        supplier, supplier_src = self._pick(
            (table.get("supplier"), "table_zone"),
            (kv.get("supplier"), "kv_zone"),
            (self._inline_value(lines, "supplier"), "inline"),
        )
        self._assign(result, "supplier", supplier, supplier_src)

        # ---- 金额：概要总金额 > 表格总价 > 键值金额 --------------------------
        # 概要"总中标金额"为 ￥0 万元 时（分标/按实结算类公告的常见写法），
        # 真实金额在中标信息表格里 → 回退表格区
        amount, amount_src = self._pick(
            (kv.get("amount"), "kv_zone"),
            (table.get("amount"), "table_zone"),
            (self._inline_value(lines, "amount"), "inline"),
        )
        if amount and "总价：" in amount:
            m = _TOTAL_PRICE_RE.search(amount)
            if m:  # "单价：A（元），总价：B（元）"——必须取总价而非单价
                amount = m.group(1) + "元"
        if amount:
            normalized = normalize_amount(amount)
            if normalized == 0 and table.get("amount"):  # 概要0元 → 回退表格真实金额
                amount, amount_src = table["amount"], "table_zone"
        self._assign(result, "amount", amount, amount_src)
        if amount:
            result.amount_normalized = normalize_amount(amount)
            if result.amount_normalized is None:
                result.missing.append("amount_unparsable")

        # ---- 其余字段：键值 > 行内 -------------------------------------------
        for name in ("project_name", "purchaser", "region", "project_id", "announce_time"):
            value, source = self._pick(
                (kv.get(name), "kv_zone"),
                (self._inline_value(lines, name), "inline"),
            )
            self._assign(result, name, value, source)

        return result

    # ------------------------------------------------------------------
    # 策略1：表格区（"三、中标（成交）信息" 表头→数据行）
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_table_zone(lines: list[str]) -> dict:
        """定位中标信息区表头，表头行之后按顺序找供应商/金额数据行。

        D4 样本结构（扁平化表格）：
            序号 / 中标（成交）金额(元) / 中标供应商名称 / 中标供应商地址 / 评审总得分   ← 表头
            1 / 单价：2230000（元），总价：2230000（元） / 杭州恒益医疗器械有限公司 / 地址 / 87.14  ← 数据
        """
        out: dict = {}
        try:
            start = next(i for i, ln in enumerate(lines) if "中标（成交）信息" in ln or "中标信息" in ln)
        except StopIteration:
            return out

        # 找表头中的供应商列名，其后10行内找第一个"企业后缀行"为供应商
        header_idx = next(
            (i for i in range(start, min(start + 15, len(lines)))
             if lines[i] in ("中标供应商名称", "供应商名称", "中标（成交）供应商名称")),
            None,
        )
        if header_idx is not None:
            for ln in lines[header_idx + 1: header_idx + 11]:
                if ln in _TABLE_HEADERS or _SUPPLIER_SUFFIX_RE.fullmatch(ln) is None:
                    continue
                out["supplier"] = ln
                break

        # 金额：表头"中标（成交）金额(元)"之后第一个 总价/单价 格式行
        amount_header_idx = next(
            (i for i in range(start, min(start + 15, len(lines)))
             if lines[i].startswith("中标（成交）金额")),
            None,
        )
        if amount_header_idx is not None:
            for ln in lines[amount_header_idx + 1: amount_header_idx + 11]:
                m = _TABLE_AMOUNT_RE.search(ln)
                if m:
                    out["amount"] = m.group(1) + "元"
                    break

        return out

    # ------------------------------------------------------------------
    # 策略2：键值对扫描（"键行"→值行）
    # ------------------------------------------------------------------

    @staticmethod
    def _canonical_key(line: str) -> str:
        """键行归一化：剥"一、/1."序号前缀与尾冒号（"一、项目编号：" → "项目编号"）。"""
        return _SECTION_PREFIX_RE.sub("", line).rstrip("：:").strip()

    def _scan_kv(self, lines: list[str]) -> dict:
        """扫描键行，取其后的值行。

        值行判定因字段而异：
        - supplier：扁平化表格的表头列名不完备（供应商名称→供应商地址→评审方法→…），
          必须用"企业后缀"正则判定真值行，窗口15行
        - 其他字段：概要区结构稳定，取键后3行内第一个非键/非表头/非序号行
        """
        out: dict = {}
        for i, ln in enumerate(lines):
            fname = KEY_ALIASES.get(self._canonical_key(ln))
            if not fname or fname in out:
                continue
            for nxt in lines[i + 1: i + 16]:
                if fname == "supplier":
                    if _SUPPLIER_SUFFIX_RE.fullmatch(nxt):
                        out[fname] = nxt
                        break
                    continue  # 表头列名/序号/金额描述等一律跳过，直到找到企业名
                cn = self._canonical_key(nxt)
                if nxt in KEY_ALIASES or cn in KEY_ALIASES or nxt in _TABLE_HEADERS or nxt.isdigit():
                    continue
                out[fname] = nxt
                break
        return out

    # ------------------------------------------------------------------
    # 策略3：行内"键：值"
    # ------------------------------------------------------------------

    def _inline_value(self, lines: list[str], field_name: str) -> str | None:
        aliases = {k for k, v in KEY_ALIASES.items() if v == field_name}
        for ln in lines:
            if ln in aliases or ("：" not in ln and ":" not in ln):
                continue
            m = _INLINE_KV_RE.match(ln)
            if not m:
                continue
            if self._canonical_key(m.group(1).strip()) in aliases:
                value = m.group(2).strip()
                if value and value not in _TABLE_HEADERS and not value.isdigit():
                    return value
        return None

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    @staticmethod
    def _pick(*pairs: tuple[str | None, str | None]) -> tuple[str | None, str | None]:
        """按优先级取第一个非空 (值, 来源标签) 对。"""
        for value, source in pairs:
            if value:
                return value, source
        return None, None

    @staticmethod
    def _assign(result: ExtractionResult, name: str, value: str | None, source: str | None) -> None:
        if value:
            result.fields[name] = value
            result.sources[name] = source or "unknown"
        else:
            result.missing.append(name)
