#!/usr/bin/env bash
# 项目统一运行入口
# 作用：屏蔽公司全局环境预设的 PYTHONPATH（其优先级高于 .venv，
#       会导致 pandas/langgraph 等包解析到全局旧版本），确保优先使用 .venv
#
# 用法: ./run.sh scripts/clean_data.py
#       ./run.sh -m streamlit run src/web/app.py
exec env -u PYTHONPATH .venv/bin/python "$@"
