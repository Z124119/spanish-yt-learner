"""MyMemory 免费机翻提供方（无需注册、无需密钥）。

MyMemory（https://mymemory.translated.net）是一个公开的翻译记忆服务，
提供免费的机器翻译 REST 接口：

* 匿名调用限额约 **5000 字符/天/IP**；在 `MYMEMORY_EMAIL` 里填一个邮箱
  可提升到约 50000 字符/天（仅用于他们的配额标识，见官方文档）。
* 接口地址：``https://api.mymemory.translated.net/get?langpair=es|zh-CN&q=...``

设计约定：

* 没有任何必填配置，``available`` 恒为 ``True``（实际可用性取决于网络）；
* 失败（网络错误、限额耗尽、空译文）统一抛 :class:`ProviderNotConfigured`，
  由上层（ChainedTranslator / pipeline）回退到下一级；
* 译文 ``approximate=False``（它是完整句译文而非逐词直译），前端按
  ``source == "mymemory-free"`` 显示「机翻译文」徽标。
"""

from __future__ import annotations

import os
import urllib.parse

from .base import ProviderNotConfigured, Translation

EMAIL_ENV = "MYMEMORY_EMAIL"
ENDPOINT = "https://api.mymemory.translated.net/get"
LANGPAIR = "es|zh-CN"
# MyMemory 单次查询的文本长度上限（官方文档约 500 字节）
MAX_QUERY_BYTES = 480


class MyMemoryTranslator:
    """调用 MyMemory 免费接口做整句机器翻译。"""

    name = "mymemory-free"
    concurrent = True  # 联网提供方，pipeline 可将其放入线程池

    def __init__(self, *, email: str = "", timeout: int = 15, session=None) -> None:
        self._email = (email if email else os.environ.get(EMAIL_ENV, "") or "").strip()
        self._timeout = max(1, int(timeout))
        self._session = session

    @property
    def available(self) -> bool:
        return True  # 无需配置；网络问题在调用时才暴露并触发回退

    def status(self) -> dict:
        """供 /api/health 展示的状态（不含任何隐私信息）。"""
        return {
            "available": True,
            "endpoint": ENDPOINT,
            "email_set": bool(self._email),
        }

    def translate(self, text: str, *, level: str | None = None) -> Translation | None:
        """翻译一句西语为简体中文；失败抛 ProviderNotConfigured。"""
        import requests

        query = (text or "").strip()
        if not query:
            return None
        if len(query.encode("utf-8")) > MAX_QUERY_BYTES:
            # 单句过长直接放弃，交给回退链
            raise ProviderNotConfigured("句子超出 MyMemory 单次查询长度上限")

        params = {"langpair": LANGPAIR, "q": query}
        if self._email:
            params["de"] = self._email

        try:
            response = (self._session or requests).get(
                ENDPOINT, params=params, timeout=self._timeout
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001 - 统一回退，不向外泄内部细节
            raise ProviderNotConfigured(
                f"MyMemory 请求失败（{type(exc).__name__}）"
            ) from None

        status = data.get("responseStatus")
        # 限流时 status 可能是字符串 "429"，因此统一转字符串比较
        if str(status) not in ("200", "0"):
            if "QUOTA" in str(data.get("responseDetails", "")).upper() or str(status) == "429":
                raise ProviderNotConfigured("MyMemory 每日限额已用尽")
            raise ProviderNotConfigured(f"MyMemory 返回异常状态（{status}）")
        if data.get("quotaFinished"):
            raise ProviderNotConfigured("MyMemory 每日限额已用尽")

        translated = str(data.get("responseData", {}).get("translatedText", "") or "").strip()
        # MyMemory 对未知内容可能原样返回大写提示（如 "QUERY LENGTH LIMIT EXCEEDED"）
        if not translated or translated.upper() == query.upper():
            raise ProviderNotConfigured("MyMemory 未给出有效译文")
        if not any("\u4e00" <= ch <= "\u9fff" for ch in translated):
            raise ProviderNotConfigured("MyMemory 译文中不含中文，视为失败")

        return Translation(
            text=translated,
            approximate=False,
            source=self.name,
            note="机翻译文（MyMemory 免费接口，仅供参考）",
        )
