"""D7 基准集工具：生成人工标注模板（预填机器结果）+ 评估提取准确率。

用法：
    1. 生成标注模板（预填机器提取结果，人工只需核对/纠正）:
       ./run.sh scripts/golden_set.py --make --sample 30
    2. 人工核对 data/processed/golden_set.csv：
       机器结果正确的行把 is_correct 留 True；错误的改为 False 并在 correct_value 填正确值
    3. 评估准确率:
       ./run.sh scripts/golden_set.py --eval
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from loguru import logger

from src.crawler.extract import FieldExtractor
from src.crawler.storage import RawStore

GOLDEN_PATH = Path("data/processed/golden_set.csv")

# 参与基准评估的字段
EVAL_FIELDS = ["supplier", "amount", "purchaser", "project_id"]


def make_golden_set(sample_n: int) -> None:
    """抽样生成标注模板：预填机器提取结果 + is_correct 列。"""
    records = store_records()
    if len(records) < sample_n:
        sample_n = len(records)
    random.seed(42)  # 可复现抽样
    sampled = random.sample(records, sample_n)

    extractor = FieldExtractor()
    rows = []
    for d in sampled:
        r = extractor.extract(d["body_text"])
        for field_name in EVAL_FIELDS:
            rows.append(
                {
                    "url": d["url"],
                    "title": d["title"][:40],
                    "field": field_name,
                    "machine_value": r.fields.get(field_name, ""),
                    "is_correct": bool(r.fields.get(field_name)),  # 未提取出的行必须人工填
                    "correct_value": r.fields.get(field_name, ""),
                    "_sample_id": sampled.index(d),
                }
            )
    df = pd.DataFrame(rows)
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(GOLDEN_PATH, index=False, encoding="utf-8")
    logger.info("标注模板 -> {}（{}条公告 × {}字段 = {} 个标注项）",
                GOLDEN_PATH, sample_n, len(EVAL_FIELDS), len(df))
    print("标注方法：逐行核对 machine_value；正确保留 is_correct=True，"
          "错误的改 is_correct=False 并在 correct_value 填正确值（未提取的必须补填）")


def evaluate() -> None:
    """对比人工标注 vs 机器提取，输出各字段准确率。"""
    if not GOLDEN_PATH.exists():
        logger.error("基准集不存在，先运行 --make")
        sys.exit(1)
    df = pd.read_csv(GOLDEN_PATH)
    if not df["is_correct"].notna().all():
        logger.warning("存在未核对的行（is_correct 为空），结果可能偏乐观")

    print("=" * 50)
    print("提取准确率评估（人工基准集）")
    print("=" * 50)
    overall_ok = overall_n = 0
    for field_name in EVAL_FIELDS:
        sub = df[df["field"] == field_name]
        if sub.empty:
            continue
        ok = int(sub["is_correct"].fillna(False).sum())
        n = len(sub)
        overall_ok += ok
        overall_n += n
        pct = ok / n * 100 if n else 0
        mark = "✅" if pct >= 90 else "⚠️"
        print(f"  {mark} {field_name:12} {ok}/{n}  {pct:.0f}%")
        wrong = sub[~sub["is_correct"].fillna(False)]
        for _, w in wrong.head(5).iterrows():
            print(f"       ✗ 机器: {str(w['machine_value'])[:30]!r:34} 人工: {str(w['correct_value'])[:30]!r}")

    pct = overall_ok / overall_n * 100 if overall_n else 0
    print("-" * 50)
    print(f"  总体: {overall_ok}/{overall_n}  {pct:.1f}%（验收线 ≥90%）")
    if pct < 90:
        print("  ⚠️ 低于验收线：优先修复上面列出的错误样本对应的提取模板")
    print("=" * 50)


def store_records() -> list[dict]:
    store = RawStore()
    return [a.__dict__ for a in store.load_all()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--make", action="store_true", help="生成标注模板")
    parser.add_argument("--eval", action="store_true", help="评估准确率")
    parser.add_argument("--sample", type=int, default=30, help="抽样条数")
    args = parser.parse_args()

    if args.make:
        make_golden_set(args.sample)
    if args.eval:
        evaluate()
    if not (args.make or args.eval):
        parser.print_help()


if __name__ == "__main__":
    main()
