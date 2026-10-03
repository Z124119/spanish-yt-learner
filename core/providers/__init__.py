"""翻译 / 释义提供方。

真正定义接口与数据结构的是 :mod:`core.providers.base`，这里只做再导出，
让调用方可以写 ``from core.providers import Translator``。
"""

from .base import (
    GlossResult,
    ProviderNotConfigured,
    Translation,
    Translator,
    WordGloss,
)

__all__ = [
    "GlossResult",
    "ProviderNotConfigured",
    "Translation",
    "Translator",
    "WordGloss",
]
