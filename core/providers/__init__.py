"""翻译 / 释义提供方。

真正定义接口与数据结构的是 :mod:`core.providers.base`，这里只做再导出，
让调用方可以写 ``from core.providers import Translator``。
"""

from .base import (
    ChainedTranslator,
    GlossResult,
    ProviderNotConfigured,
    Translation,
    Translator,
    WordGloss,
)
from .llm import OpenAICompatibleTranslator
from .mymemory import MyMemoryTranslator
from .offline import LiteralGlossTranslator

__all__ = [
    "ChainedTranslator",
    "GlossResult",
    "LiteralGlossTranslator",
    "MyMemoryTranslator",
    "OpenAICompatibleTranslator",
    "ProviderNotConfigured",
    "Translation",
    "Translator",
    "WordGloss",
]
