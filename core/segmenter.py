"""把字幕 cue 切分成「句子」与「段落」，并保留时间戳。

对应验收点「词汇、段落都保留原字幕时间戳」：句子取所在首个 cue 的开始时间，
所以任何一段都能映射回视频里的具体位置。

切句规则（有意保持保守，宁少切不错切）：
* 句末标点：`. ! ? …` 以及西语的 `¿ ¡` 引导；
* 小数点不切句（`3.5`、`1.000`）；
* 常见缩写不切句（`Sr.`、`Sra.`、`Dr.`、`etc.`、`p. ej.` …）；
* 单字母缩写（`J.`、`M.`）不切句。

段落规则：相邻句子时间间隔不超过 `paragraph_gap` 秒，且不违反句子数/字符数上限时合并。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Sequence

from .subtitle_parser import SubtitleCue

# 句末标点
_TERMINAL_CHARS = ".!?…"
# 引号与括号：出现在句末标点之后时，仍算作本句的一部分
_TRAILING_CHARS = "\"'»”’)]}" + "\u00bb\u201d"

# 常见西语缩写（小写、不含点），命中则不视为句末
_ABBREVIATIONS = {
    "sr", "sra", "srta", "dr", "dra", "prof", "profa", "ud", "uds",
    "etc", "ej", "vs", "aprox", "num", "núm", "pág", "pag", "máx", "min",
    "izq", "dcha", "tel", "avda", "dpto", "gral", "cap", "art", "fig",
    "ee", "uu", "am", "pm", "s", "a", "c", "d", "l", "x",
}

_WHITESPACE_RE = re.compile(r"\s+")
_WORD_BEFORE_DOT_RE = re.compile(r"([\wÁÉÍÓÚÜÑáéíóúüñ.\-]+)\.$")


@dataclass
class Sentence:
    """一个完整句子，带时间戳。"""

    index: int
    start: float
    end: float
    text: str
    cue_indices: List[int] = field(default_factory=list)


@dataclass
class Paragraph:
    """由若干连续句子组成的段落，时间戳取首句开始时间。"""

    index: int
    start: float
    end: float
    text: str
    sentence_indices: List[int] = field(default_factory=list)
    cue_indices: List[int] = field(default_factory=list)


def _is_abbreviation(text_so_far: str) -> bool:
    """判断句末的 `.` 是否属于缩写。"""
    match = _WORD_BEFORE_DOT_RE.search(text_so_far.rstrip())
    if not match:
        return False
    token = match.group(1).lower().strip(".")
    if not token:
        return False
    # 单字母（人名首字母缩写）
    if len(token) == 1:
        return True
    return token in _ABBREVIATIONS


def _is_decimal(text: str, dot_index: int) -> bool:
    """判断 `.` 是否是小数点：前后都是数字。"""
    if dot_index <= 0 or dot_index + 1 >= len(text):
        return False
    # 兼容千分位 1.000,5 这类混写：只要前后都是数字就当作非句末
    return text[dot_index - 1].isdigit() and text[dot_index + 1].isdigit()


def split_sentences(text: str) -> List[str]:
    """把一段文本切成句子（保留标点与引号）。"""
    if not text or not text.strip():
        return []

    sentences: List[str] = []
    buffer_start = 0
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        if char not in _TERMINAL_CHARS:
            index += 1
            continue

        if char == "." and (
            _is_decimal(text, index) or _is_abbreviation(text[buffer_start : index + 1])
        ):
            index += 1
            continue

        # 连续句末标点（如 "?!"、"..."）一并吞掉
        end = index + 1
        while end < length and text[end] in _TERMINAL_CHARS:
            if text[end] == "." and _is_decimal(text, end):
                break
            end += 1
        # 吞掉紧随其后的引号/括号
        while end < length and text[end] in _TRAILING_CHARS:
            end += 1

        piece = text[buffer_start:end].strip()
        if piece:
            sentences.append(piece)
        buffer_start = end
        index = end

    tail = text[buffer_start:].strip()
    if tail:
        # 末尾没有终止标点的残句，若已存句子则以逗号形式并入上一句更自然？
        # 这里选择独立成句，避免丢失内容；时间戳仍准确。
        sentences.append(tail)

    return sentences


def segment_cues(
    cues: Sequence[SubtitleCue],
    *,
    paragraph_gap: float = 2.5,
    max_sentences_per_paragraph: int = 4,
    max_chars_per_paragraph: int = 260,
) -> tuple[List[Sentence], List[Paragraph]]:
    """把 cue 序列切成句子与段落。

    :return: ``(sentences, paragraphs)``；两者都带时间戳，段落内含句子索引。
    """
    sentences: List[Sentence] = []
    buffer: List[str] = []
    buffer_start: float | None = None
    buffer_end = 0.0
    buffer_cues: List[int] = []

    for cue_index, cue in enumerate(cues):
        text = _WHITESPACE_RE.sub(" ", (cue.text or "").replace("\n", " ")).strip()
        if not text:
            continue

        pieces = split_sentences(text)
        if not pieces:
            continue

        for piece in pieces:
            if buffer_start is None:
                buffer_start = cue.start
            buffer.append(piece)
            buffer_end = cue.end
            if cue_index not in buffer_cues:
                buffer_cues.append(cue_index)

            # 只有以终止标点结束的片段才算一个完整句子
            if piece and piece[-1] in _TERMINAL_CHARS + _TRAILING_CHARS:
                sentences.append(
                    Sentence(
                        index=len(sentences),
                        start=buffer_start,
                        end=buffer_end,
                        text=" ".join(buffer).strip(),
                        cue_indices=list(buffer_cues),
                    )
                )
                buffer = []
                buffer_start = None
                buffer_cues = []

    # 收尾：最后一段没有终止标点的内容也不丢弃
    if buffer and buffer_start is not None:
        sentences.append(
            Sentence(
                index=len(sentences),
                start=buffer_start,
                end=buffer_end,
                text=" ".join(buffer).strip(),
                cue_indices=list(buffer_cues),
            )
        )

    paragraphs: List[Paragraph] = []
    current: List[Sentence] = []

    def flush() -> None:
        if not current:
            return
        cue_ids: List[int] = []
        for sentence in current:
            for cue_id in sentence.cue_indices:
                if cue_id not in cue_ids:
                    cue_ids.append(cue_id)
        paragraphs.append(
            Paragraph(
                index=len(paragraphs),
                start=current[0].start,
                end=current[-1].end,
                text=" ".join(s.text for s in current).strip(),
                sentence_indices=[s.index for s in current],
                cue_indices=cue_ids,
            )
        )

    for sentence in sentences:
        if current:
            gap = sentence.start - current[-1].end
            chars = sum(len(s.text) for s in current) + len(sentence.text)
            if (
                gap > paragraph_gap
                or len(current) >= max_sentences_per_paragraph
                or chars > max_chars_per_paragraph
            ):
                flush()
                current = []
        current.append(sentence)

    flush()
    return sentences, paragraphs
