#!/bin/bash
# 正确激活项目虚拟环境的脚本
# 解决全局Python环境干扰问题

echo "正在激活项目虚拟环境..."

# 保存当前环境变量（以便退出时恢复）
export _OLD_VIRTUAL_ENV="$VIRTUAL_ENV"
export _OLD_PYTHONPATH="$PYTHONPATH"

# 清除全局环境变量，避免干扰
unset VIRTUAL_ENV
unset PYTHONPATH

# 激活项目虚拟环境
source .venv/bin/activate

echo ""
echo "✅ 虚拟环境已激活！"
echo ""
echo "环境信息："
echo "  Python路径: $(which python)"
echo "  Python版本: $(python --version)"
echo "  包安装路径: $(python -c 'import sys; print(sys.prefix)')"
echo ""
echo "验证包安装："
echo "  - jieba: $(python -c 'import jieba; print("✅ 已安装")' 2>&1 || echo '❌ 未安装')"
echo "  - pandas: $(python -c 'import pandas; print("✅ 已安装")' 2>&1 || echo '❌ 未安装')"
echo "  - requests: $(python -c 'import requests; print("✅ 已安装")' 2>&1 || echo '❌ 未安装')"
echo "  - neo4j: $(python -c 'import neo4j; print("✅ 已安装")' 2>&1 || echo '❌ 未安装')"
echo "  - psycopg2: $(python -c 'import psycopg2; print("✅ 已安装")' 2>&1 || echo '❌ 未安装')"
echo ""
echo "接下来你可以："
echo "  1. 安装更多依赖: pip install -r requirements.txt"
echo "  2. 验证项目: python scripts/verify_setup.py"
echo "  3. 开始开发: 阅读 docs/development.md"
echo ""
echo "退出虚拟环境请运行: deactivate"