import os
import sys

# 后端模块之间用的是裸 import（import config / from normalizer import normalize），
# 依赖"backend 目录在 sys.path 上"。测试时把 backend 目录加进去，
# 这样 tests/ 里可以直接 import detector / normalizer / pipeline / config 等。
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
