#!/bin/bash
# 快速激活虚拟环境脚本

echo "激活虚拟环境..."
source .venv/bin/activate

echo "✅ 虚拟环境已激活！"
echo "Python路径: $(which python)"
echo "Python版本: $(python --version)"
echo ""
echo "接下来你可以："
echo "1. 安装依赖: pip install -r requirements.txt"
echo "2. 验证项目: python scripts/verify_setup.py"
echo "3. 开始开发"