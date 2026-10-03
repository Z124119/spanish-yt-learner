"""pipeline 与 LLM 提供方的集成测试（全离线，注入 Fake 翻译器）。"""

from __future__ import annotations

import time

import pytest

from core.pipeline import MaterialBuilder
from core.providers.base import ProviderNotConfigured, Translation
from core.subtitle_parser import SubtitleCue


class FakeLLM:
    """可编程的 LLM 替身：记录调用次数，可切换失败模式。"""

    name = "llm-openai-compatible"
    _timeout = 5

    def __init__(self, *, fail=False, delay=0.0):
        self.calls = 0
        self.fail = fail
        self.delay = delay

    def translate(self, text, *, level=None):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise ProviderNotConfigured("模拟故障")
        return Translation(text=f"[AI] {text}", approximate=False, source=self.name)

    def status(self):
        return {"available": True, "model": "fake-model", "has_api_key": True}


@pytest.fixture()
def cues_12():
    """12 条字幕 → 若干句子。"""
    return [
        SubtitleCue(start=i * 3.0, duration=2.5, text=f"La casa es grande numero {i}.")
        for i in range(12)
    ]


class TestPipelineWithLlm:
    def test_llm_segments_marked(self, cues_12):
        builder = MaterialBuilder(translator=FakeLLM())
        material = builder._assemble(cues=cues_12, level="B1", source="import")
        sources = {s["translation_source"] for s in material["segments"]}
        assert sources == {"llm-openai-compatible"}
        for seg in material["segments"]:
            assert seg["translation_approximate"] is False
            assert seg["translation_zh"].startswith("[AI] ")
        assert material["meta"]["translator"]["is_llm"] is True

    def test_translator_summary_contains_model(self, cues_12):
        builder = MaterialBuilder(translator=FakeLLM())
        material = builder._assemble(cues=cues_12, level="B1", source="import")
        note = material["meta"]["translator"]["note"]
        assert "fake-model" in note

    def test_fallback_short_note_and_warning(self, cues_12):
        builder = MaterialBuilder(translator=FakeLLM(fail=True))
        material = builder._assemble(cues=cues_12, level="B1", source="import")
        assert material["stats"]["sentences_translated"] == len(material["segments"])
        for seg in material["segments"]:
            assert seg["translation_source"] == "offline-literal"
            assert seg["translation_approximate"] is True
        # 汇总 warning 只出现一条，且不含异常详情
        fallback_warnings = [w for w in material["warnings"] if "回退" in w]
        assert len(fallback_warnings) == 1
        assert "模拟故障" not in fallback_warnings[0]

    def test_cache_dedup_same_sentence(self):
        translator = FakeLLM()
        builder = MaterialBuilder(translator=translator)
        texts = ["Hola, me llamo Ana."] * 3
        results = builder._translate_all(texts, level="B1", authored={})
        assert set(texts) == set(results)  # 每种原文都有译文
        assert translator.calls == 1  # 只真正调用一次（去重 + 缓存）

    def test_cache_reused_across_builds(self, cues_12):
        translator = FakeLLM()
        builder = MaterialBuilder(translator=translator)
        builder._assemble(cues=cues_12, level="B1", source="import")
        calls_after_first = translator.calls
        builder._assemble(cues=cues_12, level="B1", source="import")
        assert translator.calls < calls_after_first * 2  # 第二轮走缓存

    def test_concurrent_many_sentences(self):
        translator = FakeLLM(delay=0.02)
        builder = MaterialBuilder(translator=translator)
        texts = [f"Sentencia numero {i} sobre la vida." for i in range(50)]
        results = builder._translate_all(texts, level="B1", authored={})
        assert len(results) == 50
        assert all(r.source == "llm-openai-compatible" for r in results.values())

    def test_authored_priority_over_llm(self):
        translator = FakeLLM()
        builder = MaterialBuilder(translator=translator)
        authored = {"hola, me llamo ana.": {"zh": "人工译文。", "notes": []}}
        results = builder._translate_all(
            ["Hola, me llamo Ana.", "La casa es blanca."], level="B1", authored=authored
        )
        assert results["Hola, me llamo Ana."].source == "authored"
        assert results["La casa es blanca."].source == "llm-openai-compatible"
        assert translator.calls == 1
