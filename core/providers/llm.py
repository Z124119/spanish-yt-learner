"""可选的大模型翻译提供方（OpenAI 兼容接口）。

**首版默认不启用。** 只有当你显式设置了下面三个环境变量时才会生效：

* ``LLM_BASE_URL`` —— 例如 ``https://api.deepseek.com/v1``、``https://api.openai.com/v1``，
  或任何兼容 ``/chat/completions`` 的服务地址；
* ``LLM_API_KEY`` —— 密钥（**只从环境变量读取，绝不写进代码或仓库**）；
* ``LLM_MODEL`` —— 模型名，例如 ``deepseek-chat``、``gpt-4o-mini``。

三者缺任意一个，``available`` 就是 ``False``，调用 ``translate()`` 会抛
:class:`ProviderNotConfigured`，由 pipeline 自动回退到离线路径。

实现上只用 ``requests``，不引入任何厂商 SDK，因此换厂商只需改环境变量。
本模块**任何情况下都不会把密钥写进日志或异常信息**。
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

from .base import ProviderNotConfigured, Translation

# 允许用环境变量覆盖的提示词前缀（可选）
SYSTEM_PROMPT_ENV = "LLM_SYSTEM_PROMPT"
# 单句请求超时的环境变量名
TIMEOUT_ENV = "LLM_TIMEOUT"

# 重试策略：1 次原始请求 + 2 次重试，退避 0.5s / 1s
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 0.5
# 可重试的 HTTP 状态码（限流与服务端临时故障）；4xx 属配置/密钥错误，不重试
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

DEFAULT_SYSTEM_PROMPT = (
    "你是一位面向中文母语者的西班牙语老师。请把用户给出的西班牙语句子翻译成自然、"
    "准确的简体中文。只输出译文本身，不要加解释、不要加引号、不要重复原文。"
)

LEVEL_HINTS = {
    "A1": "学习者处于 A1 入门水平，请用最简单的表达。",
    "A2": "学习者处于 A2 初级水平。",
    "B1": "学习者处于 B1 中级水平。",
    "B2": "学习者处于 B2 中高级水平。",
    "C1": "学习者处于 C1 高级水平，可保留原文的语域与语气。",
    "C2": "学习者处于 C2 精通水平，译文可体现文体与修辞层次。",
}


class OpenAICompatibleTranslator:
    """调用任何 OpenAI 兼容的 ``/chat/completions`` 接口做整句翻译。"""

    name = "llm-openai-compatible"

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
        session=None,
    ) -> None:
        self._base_url = (base_url if base_url is not None else os.environ.get("LLM_BASE_URL", "") or "").strip().rstrip("/")
        self._api_key = (api_key if api_key is not None else os.environ.get("LLM_API_KEY", "") or "").strip()
        self._model = (model if model is not None else os.environ.get("LLM_MODEL", "") or "").strip()
        if timeout is None:
            timeout = _env_int(TIMEOUT_ENV, 30)
        self._timeout = max(1, int(timeout))
        self._session = session

    # -- 状态 ---------------------------------------------------------------

    @property
    def available(self) -> bool:
        return bool(self._base_url and self._api_key and self._model)

    def status(self) -> dict:
        """供 ``/api/health`` 展示的状态（**不含密钥**）。"""
        return {
            "available": self.available,
            "base_url": self._base_url or None,
            "model": self._model or None,
            "has_api_key": bool(self._api_key),
        }

    @property
    def _endpoint(self) -> str:
        return f"{self._base_url}/chat/completions"

    # -- 请求 ---------------------------------------------------------------

    def _session_or_new(self):
        if self._session is not None:
            return self._session, False
        import requests

        return requests.Session(), True

    def _system_prompt(self, level: Optional[str]) -> str:
        prompt = (os.environ.get(SYSTEM_PROMPT_ENV) or "").strip() or DEFAULT_SYSTEM_PROMPT
        hint = LEVEL_HINTS.get((level or "").upper())
        return f"{prompt} {hint}" if hint else prompt

    def translate(self, text: str, *, level: Optional[str] = None) -> Optional[Translation]:
        """翻译一句西语。未配置时抛 :class:`ProviderNotConfigured`。"""
        if not self.available:
            raise ProviderNotConfigured(
                "大模型未配置：请设置 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 环境变量"
                "（见 .env.example）。当前将使用离线逐词直译。"
            )
        if not text or not text.strip():
            return None

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self._system_prompt(level)},
                {"role": "user", "content": text.strip()},
            ],
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        session, should_close = self._session_or_new()
        try:
            import requests

            content = ""
            last_exc: Optional[Exception] = None
            for attempt in range(MAX_ATTEMPTS):
                try:
                    response = session.post(
                        self._endpoint, headers=headers, data=json.dumps(payload), timeout=self._timeout
                    )
                    if response.status_code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS - 1:
                        time.sleep(BACKOFF_BASE_SECONDS * (2 ** attempt))
                        continue
                    response.raise_for_status()
                    data = response.json()
                    content = (
                        data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                    ).strip()
                    last_exc = None
                    break
                except (requests.Timeout, requests.ConnectionError) as exc:
                    # 网络类错误可重试
                    last_exc = exc
                    if attempt < MAX_ATTEMPTS - 1:
                        time.sleep(BACKOFF_BASE_SECONDS * (2 ** attempt))
                        continue
                except Exception as exc:  # noqa: BLE001 - HTTP 4xx/解析错误等，不重试
                    last_exc = exc
                    break
            if last_exc is not None and not content:
                # 注意：这里只暴露异常类型与简短描述，避免把请求头里的密钥泄进日志
                raise ProviderNotConfigured(
                    f"调用大模型失败（{type(last_exc).__name__}）：{_safe_message(last_exc)}"
                ) from None
        finally:
            if should_close:
                try:
                    session.close()
                except Exception:
                    pass

        if not content:
            return None
        return Translation(text=content, approximate=False, source=self.name)


def _safe_message(exc: Exception) -> str:
    """生成不包含密钥的简短错误描述。"""
    text = str(exc)
    for marker in ("Bearer ", "sk-", "api_key", "apikey", "Authorization"):
        if marker in text:
            return "请求被拒绝或失败（详细信息已省略，以免泄露密钥）"
    return text[:200]


def _env_int(name: str, default: int) -> int:
    """读取整型环境变量，非法值静默回退默认值。"""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
