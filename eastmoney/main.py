"""兼容入口：优先转发到 Eastmoney.main，失败时走文件级回退。"""

from __future__ import annotations

import importlib.util
import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _load_main_from_file(module_path: str):
    spec = importlib.util.spec_from_file_location("eastmoney_compat_main", module_path)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"无法从文件加载模块: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "main"):
        raise AttributeError(f"模块缺少 main() 入口: {module_path}")
    return module.main


try:
    from Eastmoney.main import main
except ModuleNotFoundError:
    fallback_path = os.path.join(PROJECT_ROOT, "Eastmoney", "main.py")
    if os.path.exists(fallback_path):
        main = _load_main_from_file(fallback_path)
    else:
        raise


if __name__ == "__main__":
    main()
