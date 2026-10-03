"""默认（离线）翻译提供方：查内置词表 + 逐词直译。

**为什么是逐词直译**：在不接入大模型的前提下，把西语句子翻译成通顺中文并不可靠。
与其编造一个看起来通顺、实则有错的译文，不如老老实实给出「逐词对照」并明确标注
「仅作参考」——这对学习者其实更有用（能看清每个词对应什么）。

整句的高质量译文在两个地方提供：

* **内置示例模式**：译文由本项目作者撰写，质量可靠（见 ``data/demo/``）；
* **接入大模型后**（可选，见 ``providers/llm.py``）：可得到真正的整句翻译。

本模块不发起任何网络请求。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..glossary import Glossary, guess_pos_label
from ..lemmatizer import is_stopword, lemmatize, normalize_token, tokenize
from .base import GlossResult, Translation, WordGloss

# 虚词释义表：逐词直译时，冠词、介词这类功能词若完全不译，读起来会莫名其妙。
# 这里给最常见的功能词补一份中文标注（仅为辅助理解，不代表语法功能）。
FUNCTION_WORD_GLOSS: Dict[str, str] = {
    "el": "（定冠词）", "la": "（定冠词）", "los": "（定冠词·复）", "las": "（定冠词·复）",
    "un": "一个", "una": "一个", "unos": "几个", "unas": "几个",
    "de": "的／从", "del": "的（de+el）", "a": "到／向", "al": "到（a+el）",
    "en": "在", "con": "和／带着", "sin": "没有", "por": "因为／通过", "para": "为了／给",
    "y": "和", "e": "和", "o": "或", "u": "或", "pero": "但是", "sino": "而是",
    "que": "（引导从句）", "como": "像／作为", "si": "如果", "ni": "也不",
    "no": "不", "sí": "是", "más": "更", "menos": "更少", "muy": "很",
    "ya": "已经", "también": "也", "tampoco": "也不", "aún": "还／仍然",
    "se": "（自复／被动标记）", "me": "我", "te": "你", "nos": "我们", "os": "你们",
    "le": "他／她（与格）", "les": "他们（与格）", "lo": "它／那",
    "yo": "我", "tú": "你", "usted": "您", "él": "他", "ella": "她",
    "nosotros": "我们", "vosotros": "你们", "ustedes": "您们", "ellos": "他们", "ellas": "她们",
    "mi": "我的", "tu": "你的", "su": "他／她的", "mis": "我的", "tus": "你的", "sus": "他／她的",
    "este": "这个", "esta": "这个", "esto": "这个", "ese": "那个", "esa": "那个", "eso": "那个",
    "es": "是", "son": "是（复数）", "hay": "有（存在）", "está": "在／处于",
    "están": "在（复数）", "ser": "是", "estar": "在",
    "qué": "什么", "quién": "谁", "cómo": "怎样", "cuándo": "什么时候", "dónde": "哪里",
    "cuanto": "多少", "mucho": "很多", "poco": "少", "todo": "全部", "otro": "另一个",
}


def gloss_function_word(token: str) -> Optional[str]:
    """查虚词释义；不是虚词或未收录时返回 ``None``。"""
    key = normalize_token(token)
    if key in FUNCTION_WORD_GLOSS:
        return FUNCTION_WORD_GLOSS[key]
    # 去重音查不到时，再按原样（保留重音）查一次，覆盖 está / sí 这类
    return FUNCTION_WORD_GLOSS.get((token or "").strip().lower())


def build_word_glosses(
    text: str,
    glossary: Glossary,
    *,
    include_function_words: bool = True,
    override: Optional[Dict[str, dict]] = None,
) -> GlossResult:
    """把一个句子拆成逐词释义。

    :param include_function_words: 是否也给冠词/介词等虚词附上中文标注。
    :param override: 优先使用的词表（键为规范形式，值为 ``{"zh": [...], "pos": ...}``）。
        示例模式下传入自撰词表，这样自撰释义会盖过通用词表。
    """
    result = GlossResult()
    override = override or {}

    for token in tokenize(text):
        key = normalize_token(token)

        if is_stopword(token):
            if not include_function_words:
                continue
            # 虚词若在 override 里有更贴切的释义，优先用
            custom = override.get(key)
            meaning = (custom.get("zh") or [""])[0] if custom else gloss_function_word(token)
            if meaning:
                result.glosses.append(
                    WordGloss(
                        surface=token,
                        lemma=key,
                        zh=meaning,
                        pos=(custom.get("pos") if custom else "功能词") or "功能词",
                    )
                )
            continue

        if len(token) < 2:
            continue

        lemma = lemmatize(token).lemma
        lemma_key = normalize_token(lemma)

        custom = override.get(lemma_key)
        if custom and custom.get("zh"):
            result.glosses.append(
                WordGloss(
                    surface=token,
                    lemma=lemma,
                    zh=(custom.get("zh") or [""])[0],
                    pos=custom.get("pos", ""),
                )
            )
            continue

        senses = glossary.lookup(lemma)
        if senses:
            result.glosses.append(
                WordGloss(
                    surface=token,
                    lemma=lemma,
                    zh=senses[0].meaning,
                    pos=guess_pos_label(senses[0].pos),
                )
            )
        else:
            result.glosses.append(WordGloss(surface=token, lemma=lemma, zh="", pos=""))
            result.missing.append(token)

    return result


class LiteralGlossTranslator:
    """逐词直译翻译器（离线）。

    它不是真正的机器翻译，只是把每个词的中文释义按顺序串起来，并标记为近似译文。
    """

    name = "offline-literal"
    APPROX_NOTE = (
        "逐词直译：按原文顺序把每个词的中文释义串起来，仅供参考。"
        "西班牙语与中文语序不同，冠词、代词等虚词的处理也有限，"
        "因此这不是通顺译文；如需整句翻译可接入大模型（见 .env.example）。"
    )

    def __init__(self, glossary: Glossary) -> None:
        self._glossary = glossary

    @property
    def available(self) -> bool:
        return True

    def translate(self, text: str) -> Optional[Translation]:
        result = build_word_glosses(text, self._glossary)
        meanings = [g.zh for g in result.glosses if g.zh]
        if not meanings:
            return None
        return Translation(
            text=" ／ ".join(meanings),
            approximate=True,
            source=self.name,
            note=self.APPROX_NOTE,
        )

    def glosses_for(self, text: str) -> List[WordGloss]:
        return build_word_glosses(text, self._glossary).glosses
