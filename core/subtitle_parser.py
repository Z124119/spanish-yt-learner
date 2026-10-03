"""SRT / VTT 字幕解析。

这条路径是「字幕无法通过 YouTube 接口获取」时的回退方案，所以容错性优先：
* 自动识别格式（SRT / WebVTT）；
* 时间戳同时接受 `HH:MM:SS,mmm`、`HH:MM:SS.mmm`、`MM:SS.mmm` 三种写法；
* 忽略 VTT 的 `NOTE` / `STYLE` / `REGION` 块与 `WEBVTT` 头；
* 清理内联标签（`<i>`、`<c.colorE5E5E5>`）与 HTML 实体；
* 编码按 utf-8-sig → utf-8 → latin-1 依次尝试。

解析失败会抛出 :class:`InvalidSubtitleFile`，消息面向最终用户，可直接展示。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

# 一条时间轴：00:00:01,000 --> 00:00:04,500  或  00:01.000 --> 00:04.000
_TIME_PART = r"\d{1,3}:\d{2}(?::\d{2})?[.,]\d{1,3}"
_TIMING_RE = re.compile(
    r"^(?P<start>" + _TIME_PART + r")\s*-->\s*(?P<end>" + _TIME_PART + r")"
    r"(?:\s+(?P<settings>\S.*))?$"
)

_INLINE_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_ASS_TAG_RE = re.compile(r"\{\\[^}]*\}")
_WHITESPACE_RE = re.compile(r"[ \t]+")

# 不作为字幕文本的块关键字
_SKIPPED_BLOCK_PREFIXES = ("NOTE", "STYLE", "REGION")


class InvalidSubtitleFile(ValueError):
    """字幕文件无法解析（空文件、无条目、时间戳非法等）。"""

    def __init__(self, message: str, *, source_name: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.source_name = source_name


@dataclass(frozen=True)
class SubtitleCue:
    """一条字幕。统一使用「起始秒 + 持续秒」，与 youtube-transcript-api 对齐。"""

    start: float
    duration: float
    text: str

    @property
    def end(self) -> float:
        return self.start + max(0.0, self.duration)


@dataclass
class SubtitleDocument:
    """解析结果：条目 + 非致命警告 + 识别到的格式。"""

    cues: List[SubtitleCue] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    format: str = "unknown"


def parse_clock(value: str) -> Optional[float]:
    """把 `00:01:02,500` / `01:02.500` 解析为秒；失败返回 None。"""
    if not value:
        return None
    text = value.strip().replace(",", ".")
    parts = text.split(":")
    if not 1 <= len(parts) <= 3:
        return None
    try:
        numbers = [float(p) for p in parts]
    except ValueError:
        return None
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def _clean_text(lines: List[str]) -> str:
    """清理内联标签、ASS 标签、HTML 实体，并合并空白。"""
    joined = " ".join(line.strip() for line in lines if line.strip())
    joined = _ASS_TAG_RE.sub("", joined)
    joined = _INLINE_TAG_RE.sub("", joined)
    joined = html.unescape(joined)
    return _WHITESPACE_RE.sub(" ", joined).strip()


def _detect_format(text: str) -> str:
    head = text.lstrip("\ufeff").lstrip()
    if head.upper().startswith("WEBVTT"):
        return "vtt"
    if "-->" in text:
        return "srt"
    return "unknown"


def parse_subtitles(text: str, *, source_name: str = "字幕") -> SubtitleDocument:
    """解析字幕文本，返回 :class:`SubtitleDocument`。

    :raises InvalidSubtitleFile: 内容为空或解析不出任何有效条目。
    """
    if text is None:
        raise InvalidSubtitleFile(f"{source_name}内容为空。", source_name=source_name)

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if not normalized.strip():
        raise InvalidSubtitleFile(f"{source_name}内容为空。", source_name=source_name)

    detected = _detect_format(normalized)
    cues: List[SubtitleCue] = []
    warnings: List[str] = []
    skipped = 0

    # 按空行切块；SRT 与 VTT 都以空行分隔条目。
    for block in re.split(r"\n\s*\n", normalized):
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue

        first = lines[0].strip()
        if first.upper() == "WEBVTT" or first.upper().startswith(_SKIPPED_BLOCK_PREFIXES):
            continue

        # 找到携带 " --> " 的那一行
        timing_index = next(
            (i for i, line in enumerate(lines) if "-->" in line), None
        )
        if timing_index is None:
            # 既不是时间轴也不是已知头块：可能是序号行单独成块，忽略即可
            continue

        match = _TIMING_RE.match(lines[timing_index].strip())
        if not match:
            skipped += 1
            continue

        start = parse_clock(match.group("start"))
        end = parse_clock(match.group("end"))
        if start is None or end is None:
            skipped += 1
            continue

        payload = lines[timing_index + 1 :]
        clean = _clean_text(payload)
        if not clean:
            # 空文本条目没有学习价值，但仍记录为跳过
            skipped += 1
            continue

        cues.append(
            SubtitleCue(start=start, duration=max(0.0, end - start), text=clean)
        )

    if not cues:
        raise InvalidSubtitleFile(
            f"在{source_name}里解析不到任何字幕条目。请确认这是 SRT 或 VTT 格式，"
            "且包含类似 `00:00:01,000 --> 00:00:04,000` 的时间轴行。",
            source_name=source_name,
        )

    if skipped:
        warnings.append(f"有 {skipped} 条字幕因时间戳或内容为空被跳过。")

    cues.sort(key=lambda cue: cue.start)
    return SubtitleDocument(cues=cues, warnings=warnings, format=detected)


def decode_subtitle_bytes(data: bytes) -> str:
    """按 utf-8-sig → utf-8 → latin-1 依次尝试解码字幕字节。"""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    # latin-1 理论上不会失败，这里只是兜底
    return data.decode("utf-8", errors="replace")


def load_subtitle_file(path: str | Path) -> SubtitleDocument:
    """读取并解析本地字幕文件。

    :raises InvalidSubtitleFile: 文件不存在、为空或无法解析。
    """
    file_path = Path(path)
    name = file_path.name or "字幕文件"

    if not file_path.exists():
        raise InvalidSubtitleFile(f"找不到文件：{name}", source_name=name)
    if file_path.is_dir():
        raise InvalidSubtitleFile(f"{name} 是一个目录，不是字幕文件。", source_name=name)

    suffix = file_path.suffix.lower()
    if suffix and suffix not in (".srt", ".vtt", ".txt", ".sbv"):
        raise InvalidSubtitleFile(
            f"不支持的字幕格式 `{suffix}`，目前支持 .srt 与 .vtt。", source_name=name
        )

    try:
        raw = file_path.read_bytes()
    except OSError as exc:  # pragma: no cover - 依赖文件系统状态
        raise InvalidSubtitleFile(f"读取 {name} 失败：{exc}", source_name=name) from exc

    if not raw.strip():
        raise InvalidSubtitleFile(f"{name} 是空文件。", source_name=name)

    return parse_subtitles(decode_subtitle_bytes(raw), source_name=name)


def parse_uploaded_subtitle(content: bytes, filename: str = "") -> SubtitleDocument:
    """解析上传上来的字幕字节流（供 Web 接口使用）。"""
    name = filename or "上传的字幕文件"
    if not content or not content.strip():
        raise InvalidSubtitleFile(f"{name} 是空文件。", source_name=name)
    return parse_subtitles(decode_subtitle_bytes(content), source_name=name)


def _parse_srt_and_vtt_are_same_api() -> Tuple[str, str]:  # pragma: no cover
    """占位：SRT 与 VTT 共用同一解析器，保留此函数仅为文档可读性。"""
    return ("srt", "vtt")
