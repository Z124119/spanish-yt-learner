"""YouTube 链接解析：从各种形态的链接中取出 video_id 与起始秒数。

设计要点（对应验收点「能解析常见 YouTube 链接并正确形成时间跳转链接」）：
* 支持 watch / youtu.be / embed / v / shorts / live 以及带 www. / m. / music. 的域名；
* 支持 youtube-nocookie.com 的 embed 链接；
* 支持直接粘贴 11 位 video_id；
* 起始时间同时支持 `t=`（watch/youtu.be）与 `start=`（embed）两种参数名；
* 时间值支持 `123`、`123s`、`1h2m3s`、`1m30s`、`2h` 等写法；
* 输入里若夹带文字（例如整段 iframe 代码或笔记），会先尝试提取其中的 URL。

本模块不发起任何网络请求。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse

# YouTube 的 video_id 固定为 11 个字符，字符集为 base64url 的一个子集。
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")

# 从一段任意文本中找出第一个 http(s) 链接（用于容忍整段 iframe 代码 / 笔记）。
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

# 允许的域名后缀（判 host 时用 endswith，可同时覆盖 www. / m. / music. 等子域）。
_ALLOWED_HOSTS = (
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "youtube-nocookie.com".replace("youtube.com", "youtube.com"),  # 保持可读性
)

# 路径中直接携带 video_id 的形态：/<prefix>/<id>
_PATH_ID_PREFIXES = ("embed", "v", "shorts", "live")

# 时间写法：1h2m3s / 1m30s / 123s / 123
_TIME_TOKEN = re.compile(
    r"^(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s?)?$", re.IGNORECASE
)


class InvalidYouTubeUrl(ValueError):
    """输入无法被解释为有效的 YouTube 引用。"""

    def __init__(self, message: str, *, raw: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.raw = raw


def parse_timestamp(value: Optional[str]) -> Optional[int]:
    """把 `1h2m3s` / `90s` / `90` 之类的时间写法解析成整数秒。

    解析失败返回 ``None``（由调用方决定是忽略还是报错），不抛异常。
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    # 纯数字视为秒
    if text.isdigit():
        return int(text)

    match = _TIME_TOKEN.match(text)
    if not match or not any(match.groupdict().values()):
        return None

    hours = int(match.group("h") or 0)
    minutes = int(match.group("m") or 0)
    seconds = int(match.group("s") or 0)
    total = hours * 3600 + minutes * 60 + seconds
    return total if total >= 0 else None


def _safe_timestamp(value: Optional[str]) -> int:
    """解析时间戳；无法解析时按 0 处理（不因为一个坏参数就让整个链接解析失败）。"""
    parsed = parse_timestamp(value)
    return parsed if parsed is not None else 0


def _extract_candidate(text: str) -> str:
    """从可能夹带说明文字的输入里取出真正的链接或 video_id。"""
    candidate = (text or "").strip()
    if not candidate:
        return ""
    found = _URL_IN_TEXT.search(candidate)
    if found:
        return found.group(0).rstrip(".,;)")
    return candidate


def _host_matches(host: str) -> bool:
    host = (host or "").lower().split(":")[0]
    if not host:
        return False
    return host == "youtu.be" or host == "youtube.com" or host.endswith(
        ".youtube.com"
    ) or host == "youtube-nocookie.com" or host.endswith(".youtube-nocookie.com")


@dataclass(frozen=True)
class VideoRef:
    """一个 YouTube 视频引用：video_id + 可选的起始秒数。"""

    video_id: str
    start_seconds: int = 0

    @property
    def watch_url(self) -> str:
        """不带时间的标准观看链接。"""
        return f"https://www.youtube.com/watch?v={self.video_id}"

    def url_at(self, seconds: int) -> str:
        """生成跳转到指定秒数的观看链接（用于「跳转」按钮的降级方案）。"""
        return f"https://www.youtube.com/watch?v={self.video_id}&t={int(seconds)}s"

    def embed_url(self, *, autoplay: bool = False, enable_js_api: bool = True) -> str:
        """生成内嵌播放器链接。"""
        params = ["rel=0"]
        if enable_js_api:
            params.append("enablejsapi=1")
        if autoplay:
            params.append("autoplay=1")
        if self.start_seconds:
            params.append(f"start={int(self.start_seconds)}")
        return (
            f"https://www.youtube-nocookie.com/embed/{self.video_id}?"
            + "&".join(params)
        )


def parse_youtube_url(raw: str) -> VideoRef:
    """把用户输入解析成 :class:`VideoRef`。

    支持的输入形态::

        https://www.youtube.com/watch?v=rKhPeDyKJ1g&t=123s
        https://youtu.be/rKhPeDyKJ1g?t=1m30s
        https://www.youtube.com/shorts/rKhPeDyKJ1g
        https://www.youtube.com/embed/rKhPeDyKJ1g?start=90
        https://m.youtube.com/watch?v=rKhPeDyKJ1g
        rKhPeDyKJ1g

    :raises InvalidYouTubeUrl: 输入为空、域名不是 YouTube、或取不到合法 video_id。
    """
    candidate = _extract_candidate(raw)
    if not candidate:
        raise InvalidYouTubeUrl("链接为空，请粘贴一个 YouTube 视频链接。")

    # 1) 允许直接粘贴裸 video_id
    if VIDEO_ID_PATTERN.match(candidate):
        return VideoRef(video_id=candidate, start_seconds=0)

    # 2) 补全协议，方便没有写 https:// 的输入
    normalized = candidate
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", normalized):
        normalized = "https://" + normalized

    parsed = urlparse(normalized)
    if not _host_matches(parsed.netloc):
        raise InvalidYouTubeUrl(
            f"这不是一个 YouTube 链接（域名：{parsed.netloc or '空'}）。"
            "请粘贴 youtube.com 或 youtu.be 的链接。",
            raw=raw,
        )

    query = parse_qs(parsed.query)
    host = parsed.netloc.lower().split(":")[0]
    path_parts = [p for p in parsed.path.split("/") if p]

    video_id: Optional[str] = None
    if host == "youtu.be" or host.endswith(".youtu.be"):
        # 短链：第一个路径段就是 video_id
        if path_parts:
            video_id = path_parts[0]
    elif path_parts and path_parts[0] in _PATH_ID_PREFIXES and len(path_parts) >= 2:
        # /embed/ID、/shorts/ID、/live/ID、/v/ID
        video_id = path_parts[1]
    elif "v" in query:
        # 标准 /watch?v=ID
        video_id = query["v"][0]

    # 兜底：某些分享链接把 id 放在路径里
    if not video_id and path_parts:
        for part in path_parts:
            if VIDEO_ID_PATTERN.match(part):
                video_id = part
                break

    if not video_id:
        raise InvalidYouTubeUrl(
            "链接里找不到视频 ID，请确认是完整的视频链接（通常形如 watch?v=…）。",
            raw=raw,
        )

    video_id = video_id.strip()
    if not VIDEO_ID_PATTERN.match(video_id):
        raise InvalidYouTubeUrl(
            f"视频 ID 格式不正确：{video_id!r}（应为 11 位字母/数字/下划线/连字符）。",
            raw=raw,
        )

    # t= 用于 watch/youtu.be，start= 用于 embed；两者都接受。
    start_seconds = 0
    for key in ("t", "start"):
        if key in query:
            start_seconds = _safe_timestamp(query[key][0])
            break

    return VideoRef(video_id=video_id, start_seconds=start_seconds)


def format_timestamp(seconds: float) -> str:
    """把秒数格式化成 `MM:SS` 或 `H:MM:SS`，用于界面上的时间点按钮。"""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
