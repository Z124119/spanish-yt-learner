"""句子切分与段落合并测试。"""

from __future__ import annotations

from core.segmenter import segment_cues, split_sentences
from core.subtitle_parser import SubtitleCue


class TestSplitSentences:
    def test_basic(self):
        assert split_sentences("Hola. ¿Cómo estás? Bien.") == ["Hola.", "¿Cómo estás?", "Bien."]

    def test_abbreviation_not_split(self):
        assert split_sentences("Es el Dr. García.") == ["Es el Dr. García."]
        assert split_sentences("Son las etc. cosas.") == ["Son las etc. cosas."]

    def test_decimal_not_split(self):
        assert split_sentences("Cuesta 3.5 euros.") == ["Cuesta 3.5 euros."]

    def test_question_and_exclamation(self):
        text = "¡Hola! ¿Qué tal?"
        assert split_sentences(text) == ["¡Hola!", "¿Qué tal?"]

    def test_no_terminal(self):
        assert split_sentences("hola mundo") == ["hola mundo"]


class TestSegmentCues:
    def test_merges_and_splits(self):
        cues = [
            SubtitleCue(0.0, 2.0, "Hola. Me llamo Ana."),
            SubtitleCue(2.5, 2.0, "Vivo en Valencia."),
            SubtitleCue(6.0, 2.0, "Trabajo por las mañanas."),
        ]
        sentences, paragraphs = segment_cues(cues)
        assert [s.text for s in sentences] == [
            "Hola.",
            "Me llamo Ana.",
            "Vivo en Valencia.",
            "Trabajo por las mañanas.",
        ]
        # 前两句在同一 cue 里，起点应该相同
        assert sentences[0].start == 0.0
        assert sentences[1].start == 0.0
        # 时间戳单调不减，且段落覆盖全部句子
        assert all(
            sentences[i].start <= sentences[i + 1].start for i in range(len(sentences) - 1)
        )
        assert sum(len(p.sentence_indices) for p in paragraphs) == len(sentences)
        assert paragraphs[0].start == 0.0

    def test_paragraph_gap(self):
        cues = [
            SubtitleCue(0.0, 2.0, "Primera frase."),
            SubtitleCue(20.0, 2.0, "Segunda frase."),
        ]
        sentences, paragraphs = segment_cues(cues)
        assert len(sentences) == 2
        assert len(paragraphs) == 2  # 间隔 17.5 秒 > 2.5 秒，应分段

    def test_empty(self):
        sentences, paragraphs = segment_cues([])
        assert sentences == []
        assert paragraphs == []
