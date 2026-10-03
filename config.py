"""运行配置：**所有敏感信息只从环境变量读取，绝不写入代码或仓库**。

支持的变量（全部可选，见 `.env.example`）：

============================  ====================================================
变量                          说明
============================  ====================================================
``SES_HOST`` / ``PORT``       Web 服务监听地址与端口（默认 127.0.0.1:5000）
``SES_DEBUG``                 是否开启 Flask 调试（默认 0）
``YT_PROXY_URL``             抓取 YouTube 字幕用的 HTTP(S) 代理，云服务器上可能需要
``LLM_BASE_URL``             可选：OpenAI 兼容接口的 base URL
``LLM_API_KEY``              可选：接口密钥（**只从环境变量读，不写进代码**）
``LLM_MODEL``                可选：模型名
``LLM_TIMEOUT``              可选：请求超时秒数（默认 30）
============================  ====================================================

> 首版**默认不调用任何大模型**：上面三个 ``LLM_*`` 变量不设置时，翻译走离线路径
> （逐词直译 + 内置示例），功能完全可用。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

# 需求中给出的测试链接（注意：不假设该视频一定有可获取的西语字幕）
DEFAULT_TEST_URL = "https://www.youtube.com/watch?v=rKhPeDyKJ1g&t=123s"
DEFAULT_LEVEL = "B1"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name).lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Config:
    """应用配置。``api_key`` 仅在内存中持有，日志与接口响应里一律脱敏。"""

    host: str = "127.0.0.1"
    port: int = 5000
    debug: bool = False

    yt_proxy_url: str = ""

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout: int = 30

    default_url: str = DEFAULT_TEST_URL
    default_level: str = DEFAULT_LEVEL

    extra: dict = field(default_factory=dict)

    @property
    def llm_configured(self) -> bool:
        """三个必要变量齐全才算配置完成；缺任何一项都视为「未配置」。"""
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)

    def llm_summary(self) -> dict:
        """供 ``/api/health`` 展示的 LLM 状态（**不含密钥**）。"""
        return {
            "configured": self.llm_configured,
            "base_url": self.llm_base_url or None,
            "model": self.llm_model or None,
            "api_key": "***已从环境变量读取***" if self.llm_api_key else None,
            "note": (
                "已配置：句子翻译与讲解可调用大模型。"
                if self.llm_configured
                else "未配置（默认离线模式）：逐词直译 + 内置示例，无需任何密钥即可使用。"
                "配置方式见 .env.example。"
            ),
        }

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            host=_env("SES_HOST", "127.0.0.1"),
            port=_env_int("SES_PORT", _env_int("PORT", 5000)),
            debug=_env_bool("SES_DEBUG", False),
            yt_proxy_url=_env("YT_PROXY_URL"),
            llm_base_url=_env("LLM_BASE_URL"),
            llm_api_key=_env("LLM_API_KEY"),
            llm_model=_env("LLM_MODEL"),
            llm_timeout=_env_int("LLM_TIMEOUT", 30),
            default_url=_env("SES_DEFAULT_URL", DEFAULT_TEST_URL),
            default_level=_env("SES_DEFAULT_LEVEL", DEFAULT_LEVEL),
        )


def load_dotenv_if_present(path: Optional[str] = None) -> bool:
    """如果项目根目录有 ``.env``，把它加载进环境变量。

    这是一个极简实现（不引入 python-dotenv 依赖）：仅支持 ``KEY=VALUE`` 与 ``#`` 注释。
    已有的环境变量优先，不会被覆盖。**返回值仅表示是否加载了文件，不打印任何内容**
    —— 避免把密钥写进日志。
    """
    from pathlib import Path

    env_path = Path(path) if path else Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return False

    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        return False
    return True
