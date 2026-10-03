"""SRT / WebVTT 字幕解析测试。"""

from __future__ import annotations

import pytest

from core.subtitle_parser import (
    InvalidSubtitleFile,
    decode_subtitle_bytes,
    parse_subtitles,
    parse_uploaded_subtitle,
)

SRT = """1
00:00:01,000 --> 00:00:03,000
Hola, me llamo Ana.

2
00:00:03,500 --> 00:00:06,000
Vivo en Valencia.

3
00:00:06,000 --> 00:00:09,250
<i>Trabajo por las mañanas.</i>
"""

VTT = """WEBVTT

00:00:01.000 --> 00:00:03.000
Hola, me llamo Ana.

00:00:03.500 --> 00:00:06.000
Vivo en Valencia.
"""


class TestParseSubtitles:
    def test_srt(self):
        doc = parse_subtitles(SRT, source_name="t.srt")
        assert doc.format.lower() == "srt"
        assert len(doc.cues) == 3
        assert doc.cues[0].start == 1.0
        assert doc.cues[0].duration == 2.0
        assert doc.cues[0].text == "Hola, me llamo Ana."
        # 内联标签应被剥掉
        assert doc.cues[2].text == "Trabajo por las mañanas."

    def test_vtt(self):
        doc = parse_subtitles(VTT, source_name="t.vtt")
        assert len(doc.cues) == 2
        assert doc.cues[1].start == 3.5
        assert doc.cues[1].end == 6.0

    def test_bad_content(self):
        with pytest.raises(InvalidSubtitleFile):
            parse_subtitles("hello world, no timestamps here", source_name="x.srt")

    def test_multi_line_cue(self):
        srt = (
            "1\n00:00:01,000 --> 00:00:03,000\nlínea uno\nlínea dos\n"
        )
        doc = parse_subtitles(srt, source_name="m.srt")
        assert doc.cues[0].text == "línea uno línea dos"


class TestDecode:
    def test_utf8_bom(self):
        data = "Hola\n".encode("utf-8-sig")
        assert decode_subtitle_bytes(data).startswith("Hola")

    def test_latin1_fallback(self):
        data = "mañana".encode("latin-1")
        assert "mañana" in decode_subtitle_bytes(data)


class TestUpload:
    def test_valid(self):
        doc = parse_uploaded_subtitle(VTT.encode("utf-8"), "x.vtt")
        assert len(doc.cues) == 2

    def test_invalid(self):
        with pytest.raises(InvalidSubtitleFile):
            parse_uploaded_subtitle(b"garbage", "x.srt")

    def test_empty(self):
        with pytest.raises(InvalidSubtitleFile):
            parse_uploaded_subtitle(b"", "x.srt")
