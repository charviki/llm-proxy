"""Pytest 配置和共享 fixtures"""
import sys
from pathlib import Path

# 确保项目根目录在 path 中
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
