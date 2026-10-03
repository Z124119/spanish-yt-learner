"""URL 解析与时间戳测试。"""

from __future__ import annotations

import pytest

from core.url_parser import (
    InvalidYouTubeUrl,
    VideoRef,
    format_timestamp,
    parse_timestamp,
    parse_youtube_url,
)

DEFAULT_URL = "https://www.youtube.com/watch?v=rKhPeDyKJ1g&t=123s"


class TestParseTimestamp:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("123", 123),
            ("123s", 123),
            ("0", 0),
            ("1h2m3s", 3723),
            ("1m30s", 90),
            ("2h", 7200),
            ("90m", 5400),
            ("1h30", 3630),  # 省略末尾单位时按分钟处理
        ],
    )
    def test_valid(self, raw, expected):
        assert parse_timestamp(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "abc", "-5", "1.5h"])
    def test_invalid(self, raw):
        assert parse_timestamp(raw) is None


class TestParseYouTubeUrl:
    def test_default_test_url(self):
        ref = parse_youtube_url(DEFAULT_URL)
        assert ref.video_id == "rKhPeDyKJ1g"
        assert ref.start_seconds == 123

    @pytest.mark.parametrize(
        "raw,vid,start",
        [
            ("https://youtu.be/rKhPeDyKJ1g?t=45", "rKhPeDyKJ1g", 45),
            ("https://youtu.be/rKhPeDyKJ1g", "rKhPeDyKJ1g", 0),
            ("https://www.youtube.com/watch?v=rKhPeDyKJ1g", "rKhPeDyKJ1g", 0),
            ("https://www.youtube.com/watch?v=rKhPeDyKJ1g&start=200", "rKhPeDyKJ1g", 200),
            ("https://m.youtube.com/watch?v=rKhPeDyKJ1g&t=1m10s", "rKhPeDyKJ1g", 70),
            ("https://music.youtube.com/watch?v=rKhPeDyKJ1g", "rKhPeDyKJ1g", 0),
            ("https://www.youtube.com/embed/rKhPeDyKJ1g", "rKhPeDyKJ1g", 0),
            ("https://www.youtube.com/shorts/rKhPeDyKJ1g", "rKhPeDyKJ1g", 0),
            ("https://www.youtube.com/live/rKhPeDyKJ1g", "rKhPeDyKJ1g", 0),
            ("https://www.youtube-nocookie.com/embed/rKhPeDyKJ1g", "rKhPeDyKJ1g", 0),
            ("rKhPeDyKJ1g", "rKhPeDyKJ1g", 0),
            ("  https://www.youtube.com/watch?v=rKhPeDyKJ1g&t=123s  ", "rKhPeDyKJ1g", 123),
        ],
    )
    def test_supported_forms(self, raw, vid, start):
        ref = parse_youtube_url(raw)
        assert ref.video_id == vid
        assert ref.start_seconds == start

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "not a url",
            "https://example.com/watch?v=rKhPeDyKJ1g",
            "https://vimeo.com/12345",
            "https://www.youtube.com/watch?v=short",
            "https://www.youtube.com/watch?v=has!special1",
            "https://www.youtube.com/watch",
        ],
    )
    def test_rejected(self, raw):
        with pytest.raises(InvalidYouTubeUrl):
            parse_youtube_url(raw)

    def test_missing_id(self):
        with pytest.raises(InvalidYouTubeUrl):
            parse_youtube_url("https://www.youtube.com/watch?v=")


class TestVideoRef:
    def test_urls(self):
        ref = VideoRef(video_id="rKhPeDyKJ1g", start_seconds=123)
        assert ref.watch_url == "https://www.youtube.com/watch?v=rKhPeDyKJ1g"
        assert ref.url_at(200) == "https://www.youtube.com/watch?v=rKhPeDyKJ1g&t=200s"
        embed = ref.embed_url()
        assert embed.startswith("https://www.youtube-nocookie.com/embed/rKhPeDyKJ1g?")
        assert "enablejsapi=1" in embed
        assert "start=123" in embed


class TestFormatTimestamp:
    @pytest.mark.parametrize(
        "seconds,expected",
        [(0, "00:00"), (4.2, "00:04"), (65, "01:05"), (3661, "1:01:01")],
    )
    def test(self, seconds, expected):
        assert format_timestamp(seconds) == expected
