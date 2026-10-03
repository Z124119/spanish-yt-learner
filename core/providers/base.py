"""翻译 / 释义提供方的抽象接口。

设计意图：把「从哪里拿到中文」这件事抽成可替换的接口，于是——

* 默认实现是**完全离线**的（`providers/offline.py`）：查内置词表 + 逐词直译；
* 如果之后想接入大模型，只需实现同一个接口（`providers/llm.py` 已给出
  OpenAI 兼容实现），**不必改动 pipeline 与前端**。

首版按需求**默认不启用大模型**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, runtime_checkable


class ProviderNotConfigured(RuntimeError):
    """尝试使用一个尚未配置好的提供方（例如缺少 API 密钥）。"""


@dataclass
class Translation:
    """一段文本的译文。"""

    text: str
    approximate: bool = False
    source: str = "offline"
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "approximate": self.approximate,
            "source": self.source,
            "note": self.note,
        }


@dataclass
class WordGloss:
    """对单个词的逐词释义，用于在原文下方标注每个词的意思。"""

    surface: str
    lemma: str = ""
    zh: str = ""
    pos: str = ""

    def to_dict(self) -> dict:
        return {"surface": self.surface, "lemma": self.lemma, "zh": self.zh, "pos": self.pos}


@dataclass
class GlossResult:
    """逐词释义的完整结果。"""

    glosses: List[WordGloss] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)

    @property
    def covered(self) -> int:
        return len(self.glosses)


@runtime_checkable
class Translator(Protocol):
    """翻译提供方接口。"""

    name: str

    @property
    def available(self) -> bool:
        """当前是否可用（例如密钥是否配置齐全）。"""

    def translate(self, text: str) -> Optional[Translation]:
        """翻译一段文本；无法翻译时返回 ``None``。"""
