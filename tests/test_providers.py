"""翻译提供方测试：离线逐词直译 + LLM 状态脱敏。"""

from __future__ import annotations

import pytest

from core.glossary import Glossary
from core.providers.base import ProviderNotConfigured
from core.providers.llm import OpenAICompatibleTranslator
from core.providers.offline import (
    LiteralGlossTranslator,
    build_word_glosses,
    gloss_function_word,
)


@pytest.fixture(scope="module")
def glossary():
    return Glossary().load()


class TestOfflineTranslator:
    def test_name_and_availability(self, glossary):
        translator = LiteralGlossTranslator(glossary)
        assert translator.name == "offline-literal"
        assert translator.available

    def test_translate_marks_approximate(self, glossary):
        translation = LiteralGlossTranslator(glossary).translate("La casa es grande.")
        assert translation is not None
        assert translation.approximate is True

    def test_glosses_cover_content_words(self, glossary):
        result = build_word_glosses("La casa es grande.", glossary)
        by_surface = {g.surface: g for g in result.glosses}
        assert "casa" in by_surface
        assert by_surface["casa"].zh
        # es 是不规则动词，应能还原并给释义
        assert "es" in by_surface

    def test_override_takes_priority(self, glossary):
        result = build_word_glosses(
            "La casa es grande.", glossary, override={"casa": {"pos": "n.", "zh": ["自定义释义"]}}
        )
        casa = next(g for g in result.glosses if g.surface == "casa")
        assert casa.zh == "自定义释义"

    def test_function_words(self):
        assert gloss_function_word("de")
        assert gloss_function_word("la")
        assert gloss_function_word("Yo") or gloss_function_word("yo")
        assert not gloss_function_word("casa")


class TestLlmTranslator:
    def test_not_configured_by_default(self, monkeypatch):
        for var in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
            monkeypatch.delenv(var, raising=False)
        translator = OpenAICompatibleTranslator()
        assert not translator.available
        with pytest.raises(ProviderNotConfigured):
            translator.translate("Hola.")

    def test_configured_when_all_vars_set(self, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "test-key-123")
        monkeypatch.setenv("LLM_MODEL", "test-model")
        translator = OpenAICompatibleTranslator()
        assert translator.available
        status = translator.status()
        assert status["available"] is True
        assert status["has_api_key"] is True
        assert status["model"] == "test-model"

    def test_status_never_leaks_key(self, monkeypatch):
        secret = "sk-super-secret-value"
        monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("LLM_API_KEY", secret)
        monkeypatch.setenv("LLM_MODEL", "test-model")
        translator = OpenAICompatibleTranslator()
        import json

        dumped = json.dumps(translator.status(), ensure_ascii=False)
        assert secret not in dumped
        assert "has_api_key" in dumped  # 只暴露布尔标记

    def test_failure_raises_without_leaking_key(self, monkeypatch):
        secret = "sk-another-secret"
        monkeypatch.setenv("LLM_BASE_URL", "https://127.0.0.1:1/v1")
        monkeypatch.setenv("LLM_API_KEY", secret)
        monkeypatch.setenv("LLM_MODEL", "test-model")
        translator = OpenAICompatibleTranslator()
        with pytest.raises(ProviderNotConfigured) as excinfo:
            translator.translate("Hola.")
        assert secret not in str(excinfo.value)
