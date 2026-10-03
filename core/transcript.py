"""通过 youtube-transcript-api 获取字幕，并把底层异常翻译成用户可读的原因与建议。

对应验收点「处理字幕不可用的情况」：所有失败路径都会返回明确的
``error_code`` / ``message`` / ``hint``，并在可能时附带该视频**实际可用**的字幕语言列表，
这样用户能立刻知道「是视频没有西语字幕」还是「视频根本没开字幕」还是「IP 被限制」。

> 依赖说明：字幕抓取能力由开源库 ``youtube-transcript-api``（MIT）提供。
> 它使用的是 YouTube 未公开接口，YouTube 随时可能改动或限制；官方文档也提示
> 云服务商 IP 容易被封。因此本工具**始终**提供 SRT/VTT 导入与内置示例作为回退。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .subtitle_parser import SubtitleCue

# 西语语言代码前缀（es / es-ES / es-MX / es-419 / es-US …）
SPANISH_PREFIX = "es"

DEFAULT_LANGUAGE_PRIORITY: Sequence[str] = ("es", "es-ES", "es-MX", "es-419", "es-US")

# 用户的个人代理（可选）。云服务器上抓取失败时可用，配置方式见 README。
PROXY_ENV_VAR = "YT_PROXY_URL"


class TranscriptError(Exception):
    """字幕获取失败。``message`` 与 ``hint`` 面向最终用户，可直接显示在界面上。"""

    def __init__(
        self,
        error_code: str,
        message: str,
        hint: str = "",
        *,
        available_languages: Optional[List[Dict[str, object]]] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.hint = hint
        self.available_languages = available_languages or []

    def to_dict(self) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "error_code": self.error_code,
            "message": self.message,
            "hint": self.hint,
        }
        if self.available_languages:
            payload["available_languages"] = self.available_languages
        return payload


@dataclass
class TranscriptResult:
    """一次成功的字幕获取结果。"""

    cues: List[SubtitleCue] = field(default_factory=list)
    language_code: str = ""
    language: str = ""
    is_generated: bool = False
    source: str = "youtube"
    warnings: List[str] = field(default_factory=list)


def _describe_languages(items: Sequence[object]) -> List[Dict[str, object]]:
    described: List[Dict[str, object]] = []
    for item in items:
        described.append(
            {
                "code": getattr(item, "language_code", ""),
                "name": getattr(item, "language", ""),
                "generated": bool(getattr(item, "is_generated", False)),
            }
        )
    return described


def _format_available(languages: Sequence[Dict[str, object]]) -> str:
    if not languages:
        return ""
    parts = []
    for entry in languages[:8]:
        tag = "自动" if entry.get("generated") else "人工"
        parts.append(f"{entry.get('name') or entry.get('code')}（{tag}）")
    return "、".join(parts)


def _build_api():
    """构造 YouTubeTranscriptApi 实例；若配置了 YT_PROXY_URL 则启用代理。"""
    from youtube_transcript_api import YouTubeTranscriptApi

    proxy_url = (os.environ.get(PROXY_ENV_VAR) or "").strip()
    if proxy_url:
        try:
            from youtube_transcript_api.proxies import GenericProxyConfig

            return YouTubeTranscriptApi(
                proxy_config=GenericProxyConfig(http_url=proxy_url, https_url=proxy_url)
            )
        except Exception:
            # 代理配置不可用时退回直连，不阻断主流程
            pass
    return YouTubeTranscriptApi()


def _pick_transcript(items: Sequence[object], *, spanish_only: bool = True):
    """在可用字幕里挑选：优先人工西语 → 自动西语 → （可选）其它语言。"""
    spanish = [
        item
        for item in items
        if str(getattr(item, "language_code", "")).lower().startswith(SPANISH_PREFIX)
    ]

    def split(pool):
        manual = [i for i in pool if not getattr(i, "is_generated", False)]
        generated = [i for i in pool if getattr(i, "is_generated", False)]
        return manual, generated

    manual, generated = split(spanish)
    if manual:
        return manual[0]
    if generated:
        return generated[0]

    if spanish_only:
        return None

    manual_all, generated_all = split(list(items))
    if manual_all:
        return manual_all[0]
    if generated_all:
        return generated_all[0]
    return None


def _to_cues(fetched) -> List[SubtitleCue]:
    cues: List[SubtitleCue] = []
    # FetchedTranscript 支持迭代；同时兼容 to_raw_data() 的字典形态
    for snippet in fetched:
        text = getattr(snippet, "text", None)
        start = getattr(snippet, "start", None)
        duration = getattr(snippet, "duration", None)
        if text is None and isinstance(snippet, dict):
            text, start, duration = (
                snippet.get("text"),
                snippet.get("start"),
                snippet.get("duration"),
            )
        if not text:
            continue
        cues.append(
            SubtitleCue(
                start=float(start or 0.0),
                duration=float(duration or 0.0),
                text=" ".join(str(text).split()),
            )
        )
    cues.sort(key=lambda cue: cue.start)
    return cues


def _map_exception(exc: Exception) -> TranscriptError:
    """把 youtube-transcript-api 的异常翻译成中文原因 + 可执行建议。"""
    name = type(exc).__name__

    if name == "TranscriptsDisabled":
        return TranscriptError(
            "subtitles_disabled",
            "该视频关闭了字幕功能，因此拿不到任何字幕。",
            "可以改用「导入字幕文件」，粘贴你自己找到的 SRT/VTT；或点「载入示例」先体验界面。",
        )
    if name == "NoTranscriptFound":
        return TranscriptError(
            "no_spanish_transcript",
            "这个视频没有可用的西语字幕。",
            "可以导入 SRT/VTT 字幕文件，或换一个带西语字幕的视频再试。",
        )
    if name == "VideoUnavailable":
        return TranscriptError(
            "video_unavailable",
            "视频不可用（可能已删除、设为私享或地区受限）。",
            "请检查链接是否正确，或换一个视频。",
        )
    if name in ("RequestBlocked", "IpBlocked"):
        return TranscriptError(
            "ip_blocked",
            "YouTube 限制了当前网络的 IP（常见于云服务器或短时间内请求过多）。",
            "请稍后重试、换网络，或直接使用「导入字幕文件」；本地运行通常不会被拦。",
        )
    if name == "AgeRestricted":
        return TranscriptError(
            "age_restricted",
            "该视频有年龄限制，无法匿名获取字幕。",
            "请改用「导入字幕文件」。",
        )
    if name == "InvalidVideoId":
        return TranscriptError(
            "invalid_video_id",
            "视频 ID 无效，链接可能不完整。",
            "请粘贴完整的 YouTube 视频链接。",
        )
    if name in ("PoTokenRequired", "YouTubeDataUnparsable", "YouTubeRequestFailed"):
        return TranscriptError(
            "youtube_api_error",
            "YouTube 接口返回了意料之外的结果（该接口未公开，随时可能变动）。",
            "可以改用「导入字幕文件」，或稍后重试。",
        )
    if isinstance(exc, OSError):
        return TranscriptError(
            "network_error",
            f"网络请求失败：{exc}",
            "请检查网络连接后重试，或使用「导入字幕文件」。",
        )

    return TranscriptError(
        "transcript_error",
        f"获取字幕失败：{exc}",
        "可以改用「导入字幕文件」，或点「载入示例」先体验界面。",
    )


def fetch_transcript(
    video_id: str,
    *,
    allow_fallback_language: bool = True,
    languages: Sequence[str] = DEFAULT_LANGUAGE_PRIORITY,
) -> TranscriptResult:
    """获取指定视频的字幕。

    :param allow_fallback_language: 没有西语字幕时，是否退而使用其它语言的字幕
        （会写入 ``warnings``，界面上会显著提示"这不是西语字幕"）。
    :raises TranscriptError: 任何失败路径。
    """
    if not video_id:
        raise TranscriptError("invalid_video_id", "缺少视频 ID。", "请提供完整的 YouTube 链接。")

    try:
        from youtube_transcript_api import YouTubeTranscriptApiException  # noqa: F401
        api = _build_api()
    except Exception as exc:  # pragma: no cover - 依赖缺失
        raise TranscriptError(
            "dependency_missing",
            f"未能加载字幕依赖库 youtube-transcript-api：{exc}",
            "请先执行 `pip install -r requirements.txt`。",
        ) from exc

    items: List[object] = []
    try:
        items = list(api.list(video_id))
    except Exception as exc:
        mapped = _map_exception(exc)
        # NoTranscriptFound 在"完全没有任何字幕"时也会抛出，这里补一次说明
        raise mapped from exc

    if not items:
        raise TranscriptError(
            "no_transcript",
            "该视频没有任何字幕（既没有人工字幕也没有自动字幕）。",
            "可以导入 SRT/VTT 字幕文件，或点「载入示例」体验完整流程。",
        )

    available = _describe_languages(items)

    transcript = _pick_transcript(items, spanish_only=True)
    warnings: List[str] = []

    if transcript is None:
        if not allow_fallback_language:
            raise TranscriptError(
                "no_spanish_transcript",
                "这个视频没有西语字幕。可用字幕：" + (_format_available(available) or "无"),
                "可以导入 SRT/VTT 字幕文件，或换一个带西语字幕的视频。",
                available_languages=available,
            )
        transcript = _pick_transcript(items, spanish_only=False)
        if transcript is None:
            raise TranscriptError(
                "no_transcript",
                "该视频没有可用的字幕。",
                "可以导入 SRT/VTT 字幕文件。",
                available_languages=available,
            )
        warnings.append(
            "该视频没有西语字幕，已回退到 "
            f"{getattr(transcript, 'language', '')}（{getattr(transcript, 'language_code', '')}）字幕，"
            "内容不是西语，仅供流程演示。"
        )

    try:
        fetched = transcript.fetch()
    except Exception as exc:
        mapped = _map_exception(exc)
        mapped.available_languages = mapped.available_languages or available
        raise mapped from exc

    cues = _to_cues(fetched)
    if not cues:
        raise TranscriptError(
            "empty_transcript",
            "字幕接口返回了空内容。",
            "可以导入 SRT/VTT 字幕文件，或点「载入示例」。",
            available_languages=available,
        )

    if getattr(transcript, "is_generated", False):
        warnings.append("使用的是 YouTube 自动生成字幕，可能存在识别误差与断句问题。")
    if getattr(transcript, "language_code", "").lower() not in ("es", "es-es"):
        warnings.append(
            f"字幕语言代码为 {getattr(transcript, 'language_code', '')}，可能不是标准西班牙语。"
        )

    return TranscriptResult(
        cues=cues,
        language_code=getattr(transcript, "language_code", ""),
        language=getattr(transcript, "language", ""),
        is_generated=bool(getattr(transcript, "is_generated", False)),
        source="youtube",
        warnings=warnings,
    )


def list_available_languages(video_id: str) -> List[Dict[str, object]]:
    """列出某视频所有可用字幕（供界面在失败时提示）。失败时返回空列表。"""
    try:
        api = _build_api()
        return _describe_languages(list(api.list(video_id)))
    except Exception:
        return []
