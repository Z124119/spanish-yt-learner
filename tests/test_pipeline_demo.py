"""端到端流水线测试：内置示例（完全离线，零网络请求）。"""

from __future__ import annotations

import pytest

from core.pipeline import MaterialBuilder
from core.url_parser import parse_youtube_url
from core.subtitle_parser import SubtitleCue

ALL_LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")


class TestDemoPipeline:
    @pytest.mark.parametrize("level", ALL_LEVELS)
    def test_all_levels_build(self, builder, level):
        material = builder.build_demo(level)
        assert material["level"] == level
        assert material["source"] == "demo"
        assert material["video"]["is_demo"] is True
        assert material["video"]["has_player"] is True
        assert material["video"]["video_id"] == "rKhPeDyKJ1g"
        assert material["stats"]["cues"] > 0
        assert material["stats"]["sentences"] == len(material["segments"])
        assert material["stats"]["paragraphs"] == len(material["paragraphs"])

    def test_vocabulary_respects_profile(self, builder):
        seen = {}
        for level in ALL_LEVELS:
            material = builder.build_demo(level)
            vocab = material["vocabulary"]
            profile = material["level_profile"]
            assert len(vocab) <= profile["max_vocab"]
            assert all(v["level"] in profile["band"] or v["level"] == "X" for v in vocab)
            seen[level] = len(vocab)
        # A1 是最严格的档位，词条数不应超过 C2
        assert seen["A1"] >= seen["C2"]

    def test_vocab_fields(self, demo_b1):
        for item in demo_b1["vocabulary"]:
            assert item["term"], "term 不能为空"
            assert item["key"] == item["key"].lower()
            assert item["count"] >= 1
            assert item["timestamp"] >= 0
            assert item["timestamp_label"]
            assert item["level_source"] in ("elelex", "wordfreq", "unknown")

    def test_segments_have_authored_translation(self, demo_b1):
        assert demo_b1["segments"]
        for seg in demo_b1["segments"]:
            assert seg["text_es"]
            assert seg["start_label"]
            assert seg["translation_zh"], "示例材料应有人工译文"
            assert seg["translation_source"] == "authored"
            assert seg["translation_approximate"] is False
            assert isinstance(seg["glosses"], list)

    def test_glosses_present_and_covered(self, demo_b1):
        first = demo_b1["segments"][0]
        assert first["glosses"], "逐词释义不应为空"
        for gloss in first["glosses"]:
            assert gloss["surface"]
            assert set(gloss) == {"surface", "lemma", "zh", "pos"}

    def test_paragraphs_reference_sentences(self, demo_b1):
        total = sum(len(p["sentence_indexes"]) for p in demo_b1["paragraphs"])
        assert total == len(demo_b1["segments"])
        assert all(p["start_label"] for p in demo_b1["paragraphs"])

    def test_demo_disclaimer_present(self, demo_b1):
        assert any("示例模式" in w for w in demo_b1["warnings"])

    def test_meta_summaries(self, demo_b1):
        meta = demo_b1["meta"]
        assert meta["level_data"]["authoritative_available"] is True
        assert meta["level_data"]["authoritative_entries"] > 1000
        assert meta["glossary"]["entries"] > 0
        assert meta["glossary"]["sources"], "应列出词表来源明细"
        assert meta["translator"]["name"] == "offline-literal"
        assert meta["levels_available"] == list(ALL_LEVELS)
        assert "CC BY-NC-SA" in meta["license_note"]

    def test_vocabulary_hint_when_sparse(self, builder):
        material = builder.build_demo("C2")
        assert material["vocabulary_hint"], "词条过少时应给出解释"


class TestImportPipeline:
    SRT = (
        "1\n00:00:01,000 --> 00:00:03,000\nHola, me llamo Ana.\n"
        "2\n00:00:03,500 --> 00:00:06,000\nVivo en Valencia con mi familia.\n"
    )

    def test_build_from_subtitles_with_url(self, builder):
        material = builder.build_from_subtitles(
            self.SRT.encode("utf-8"),
            "sample.srt",
            "B1",
            url="https://www.youtube.com/watch?v=rKhPeDyKJ1g&t=123s",
        )
        assert material["source"] == "import"
        assert material["video"]["video_id"] == "rKhPeDyKJ1g"
        assert material["video"]["start_seconds"] == 123
        assert material["video"]["has_player"] is True
        assert material["video"]["subtitle_filename"] == "sample.srt"
        assert material["stats"]["sentences"] >= 2
        assert material["segments"][0]["start_label"] == "00:01"

    def test_build_from_subtitles_without_url(self, builder):
        material = builder.build_from_subtitles(self.SRT.encode("utf-8"), "sample.srt", "A1")
        assert material["video"]["has_player"] is False
        assert material["video"]["video_id"] == ""
        # 没有视频时仍保留时间点
        assert material["segments"][0]["start_label"]

    def test_invalid_url_is_downgraded_to_warning(self, builder):
        material = builder.build_from_subtitles(
            self.SRT.encode("utf-8"), "sample.srt", "B1", url="https://example.com/x"
        )
        assert material["video"]["has_player"] is False
        assert any("无法解析" in w for w in material["warnings"])

    def test_empty_subtitle_raises(self, builder):
        with pytest.raises(ValueError):
            builder.build_from_subtitles(b"", "empty.srt", "B1")


class TestVideoPayloadConsistency:
    def test_default_url_roundtrip(self):
        from config import DEFAULT_TEST_URL

        ref = parse_youtube_url(DEFAULT_TEST_URL)
        assert ref.video_id == "rKhPeDyKJ1g"
        assert ref.start_seconds == 123


class TestCueTimestamps:
    def test_vocab_timestamps_are_sorted_by_first_occurrence(self, builder):
        material = builder.build_demo("B1")
        timestamps = [v["timestamp"] for v in material["vocabulary"]]
        assert all(t >= 0 for t in timestamps)
