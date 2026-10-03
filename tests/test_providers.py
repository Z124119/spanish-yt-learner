"""翻译提供方测试：离线逐词直译 + LLM（成功/重试/回退/脱敏，全部 mock HTTP）。"""

from __future__ import annotations

import json

import pytest

from core.glossary import Glossary
from core.providers.base import ProviderNotConfigured, Translation
from core.providers.llm import LEVEL_HINTS, OpenAICompatibleTranslator
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


class FakeResponse:
    """requests.Response 的最小替身。"""

    def __init__(self, status_code=200, content="Hola"):
        self.status_code = status_code
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}

    def raise_for_status(self):
        import requests

        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    """按脚本依次返回响应，并记录调用。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.payloads = []

    def post(self, url, *, headers=None, data=None, timeout=None):
        import requests

        self.calls += 1
        self.payloads.append(json.loads(data) if data else {})
        item = self.script.pop(0) if self.script else self.script[-1]
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        pass


def _configured_translator(monkeypatch, session, secret="sk-test-secret-value-123"):
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", secret)
    monkeypatch.setenv("LLM_MODEL", "test-model")
    return OpenAICompatibleTranslator(session=session)


class TestLlmSuccessAndRetry:
    def test_translate_success(self, monkeypatch):
        session = FakeSession([FakeResponse(200, content="早上好")])
        translator = _configured_translator(monkeypatch, session)
        translation = translator.translate("Buenos días.")
        assert translation is not None
        assert translation.text == "早上好"
        assert translation.approximate is False
        assert translation.source == "llm-openai-compatible"
        # 请求体里带模型名与消息，但密钥只在 headers，不进 payload
        assert session.payloads[0]["model"] == "test-model"
        assert "sk-test-secret-value-123" not in json.dumps(session.payloads)

    def test_retry_on_500_then_success(self, monkeypatch):
        monkeypatch.setattr("core.providers.llm.time.sleep", lambda s: None)
        session = FakeSession([FakeResponse(500), FakeResponse(200, content="译文")])
        translator = _configured_translator(monkeypatch, session)
        translation = translator.translate("Hola.")
        assert session.calls == 2
        assert translation is not None and translation.text == "译文"

    def test_no_retry_on_401(self, monkeypatch):
        session = FakeSession([FakeResponse(401)])
        translator = _configured_translator(monkeypatch, session)
        with pytest.raises(ProviderNotConfigured):
            translator.translate("Hola.")
        assert session.calls == 1  # 4xx 不重试

    def test_retry_exhausted_on_429(self, monkeypatch):
        from core.providers.llm import MAX_ATTEMPTS

        monkeypatch.setattr("core.providers.llm.time.sleep", lambda s: None)
        session = FakeSession([FakeResponse(429)] * MAX_ATTEMPTS)
        translator = _configured_translator(monkeypatch, session)
        with pytest.raises(ProviderNotConfigured):
            translator.translate("Hola.")
        assert session.calls == MAX_ATTEMPTS

    def test_timeout_env_var(self, monkeypatch):
        monkeypatch.setenv("LLM_TIMEOUT", "7")
        translator = OpenAICompatibleTranslator()
        assert translator._timeout == 7

    def test_level_hint_in_system_prompt(self, monkeypatch):
        session = FakeSession([FakeResponse(200, content="x")])
        translator = _configured_translator(monkeypatch, session)
        translator.translate("Hola.", level="A1")
        system = session.payloads[0]["messages"][0]["content"]
        assert LEVEL_HINTS["A1"] in system
