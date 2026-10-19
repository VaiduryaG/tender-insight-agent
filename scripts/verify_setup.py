#!/usr/bin/env python3
"""环境验证脚本：检查目录结构、.venv 依赖、配置文件与阶段产物。

运行: ./run.sh scripts/verify_setup.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REQUIRED_DIRS = [
    "data/raw",
    "data/processed",
    "src/data_processing",
    "src/knowledge_graph",
    "src/vector_search",
    "src/mcp_tools",
    "src/agent",
    "src/web",
    "tests",
    "scripts",
    "docs",
]

REQUIRED_FILES = ["README.md", "requirements.txt", ".env", "run.sh"]

# 核心依赖（应全部安装在 .venv 中）
CORE_PACKAGES = {
    "pandas": "数据处理",
    "jieba": "中文分词",
    "networkx": "知识图谱",
    "chromadb": "向量检索",
    "zhipuai": "智谱AI SDK",
    "streamlit": "Web界面",
}

# 各阶段产物
ARTIFACTS = {
    "阶段1 标准化数据": "data/processed/bidding_stage1.parquet",
    "阶段1 实体映射表": "data/processed/entity_mapping.csv",
    "阶段2 图谱持久化": "data/graph/bidding_graph.json",
    "阶段3 向量库": "data/vector/chroma",
}


def check_dirs_and_files() -> bool:
    print("\n[1/4] 目录与文件")
    ok = True
    for d in REQUIRED_DIRS:
        if Path(d).is_dir():
            print(f"  ✅ {d}/")
        else:
            print(f"  ❌ 缺失目录: {d}/")
            ok = False
    for f in REQUIRED_FILES:
        if Path(f).exists():
            print(f"  ✅ {f}")
        else:
            print(f"  ❌ 缺失文件: {f}")
            ok = False
    return ok


def check_packages() -> bool:
    print("\n[2/4] 核心依赖（.venv）")
    ok = True
    for pkg, desc in CORE_PACKAGES.items():
        r = subprocess.run(
            [".venv/bin/python", "-c", f"import {pkg}"],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0:
            print(f"  ✅ {pkg} ({desc})")
        else:
            print(f"  ❌ {pkg} ({desc}) 未安装")
            ok = False
    return ok


def check_env() -> bool:
    print("\n[3/4] 配置")
    env = Path(".env")
    if not env.exists():
        print("  ❌ .env 不存在")
        return False
    content = env.read_text(encoding="utf-8")
    if "ZHIPU_API_KEY=" in content and "your_" not in content.split("ZHIPU_API_KEY=")[1][:40]:
        print("  ✅ ZHIPU_API_KEY 已配置")
        return True
    print("  ⚠️  ZHIPU_API_KEY 未配置或为占位值（Agent/Embedding 功能不可用）")
    return False


def check_artifacts() -> None:
    print("\n[4/4] 阶段产物")
    for name, path in ARTIFACTS.items():
        mark = "✅" if Path(path).exists() else "⬜"
        print(f"  {mark} {name}: {path}")
    print("  （缺失时按顺序运行 run_stage1.py → build_graph.py → build_vector_index.py）")


def main() -> None:
    print("=" * 50)
    print("招投标智能分析 Agent — 环境验证")
    print(f"Python: {sys.version.split()[0]} | 解释器: {sys.executable}")
    print("=" * 50)

    results = {
        "目录与文件": check_dirs_and_files(),
        "核心依赖": check_packages(),
        "配置": check_env(),
    }
    check_artifacts()

    print("\n" + "=" * 50)
    for name, ok in results.items():
        print(f"{name}: {'✅ 通过' if ok else '❌ 未通过'}")
    print("=" * 50)
    if all(results.values()):
        print("✅ 环境就绪！")
    else:
        print("⚠️  存在未通过项，请按上方提示修复")
        sys.exit(1)


if __name__ == "__main__":
    main()
