"""pytest 共享夹具。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

from core.pipeline import MaterialBuilder  # noqa: E402


@pytest.fixture(scope="session")
def builder() -> MaterialBuilder:
    """进程内共享的构建器：强制离线（不碰大模型），保证测试可复现。"""
    return MaterialBuilder(want_llm=False)


@pytest.fixture(scope="session")
def demo_b1(builder: MaterialBuilder):
    """内置示例材料（B1 档），供多个测试复用。"""
    return builder.build_demo("B1")
