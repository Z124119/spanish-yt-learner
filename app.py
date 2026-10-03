"""西语 YouTube 学习助手 —— Flask 入口。

运行::

    python app.py            # 开发服务器，默认 http://127.0.0.1:5000

接口一览
--------
============================  ==========================================================
方法 / 路径                    说明
============================  ==========================================================
``GET  /``                    渲染页面，预填默认测试链接与等级
``POST /api/material``        ``{url, level}`` → 学习材料 JSON
``POST /api/material/upload`` 上传 SRT/VTT 字幕（multipart）+ ``level``（可选 ``url``）
``GET  /api/demo``            ``?level=B1`` → 内置示例材料，**零网络请求、零密钥**
``GET  /api/health``          词表 / 等级数据 / LLM 配置状态（用于自检，不含密钥）
============================  ==========================================================

所有失败路径统一返回::

    {"error": true, "error_code": "...", "message": "...", "hint": "...",
     "available_languages": [...]}

设计原则
--------
* **密钥永不落盘、永不进日志**：只从环境变量读取（见 ``config.py``）。
* **没有网络也能演示**：``/api/demo`` 走内置自撰字幕；字幕抓取失败时界面会提示
  「导入字幕文件」与「载入示例」两条回退路径。
* **构建器只初始化一次**：ELELex 与 wordfreq 的加载较慢，用懒加载 + 线程锁缓存。
"""

from __future__ import annotations

import threading
from typing import Optional

from flask import Flask, jsonify, render_template, request

import config as app_config
from core import __version__
from core.level import CEFR_LEVELS, get_profile
from core.glossary import SOURCE_LABELS
from core.pipeline import MaterialBuilder
from core.subtitle_parser import InvalidSubtitleFile
from core.transcript import TranscriptError
from core.url_parser import InvalidYouTubeUrl

# --------------------------------------------------------------------------- #
# 应用与配置
# --------------------------------------------------------------------------- #

SETTINGS = app_config.Config.from_env()

app = Flask(__name__)
app.json.ensure_ascii = False  # 中文直接原样输出，便于肉眼检查接口
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 字幕文件上限 8 MB

_builder_lock = threading.Lock()
_builder: Optional[MaterialBuilder] = None


def get_builder() -> MaterialBuilder:
    """懒加载、进程内复用的 MaterialBuilder（首次调用会加载词表与等级数据）。"""
    global _builder
    if _builder is None:
        with _builder_lock:
            if _builder is None:
                _builder = MaterialBuilder()
    return _builder


# --------------------------------------------------------------------------- #
# 错误响应
# --------------------------------------------------------------------------- #


def error_response(
    error_code: str,
    message: str,
    hint: str = "",
    *,
    status: int = 400,
    available_languages: Optional[list] = None,
):
    payload = {"error": True, "error_code": error_code, "message": message, "hint": hint}
    if available_languages:
        payload["available_languages"] = available_languages
    return jsonify(payload), status


def _resolve_level(raw: Optional[str]) -> str:
    """把前端传来的等级收敛到合法值；非法值回退到默认等级。"""
    level = (raw or "").strip().upper()
    if level in CEFR_LEVELS:
        return level
    return SETTINGS.default_level if SETTINGS.default_level in CEFR_LEVELS else "B1"


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #


@app.get("/")
def index():
    return render_template(
        "index.html",
        default_url=SETTINGS.default_url,
        default_level=_resolve_level(SETTINGS.default_level),
        levels=list(CEFR_LEVELS),
        version=__version__,
    )


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


@app.post("/api/material")
def api_material():
    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    level = _resolve_level(payload.get("level"))

    if not url:
        return error_response(
            "missing_url", "请先填写一个 YouTube 链接。", "例如 " + SETTINGS.default_url
        )

    try:
        material = get_builder().build_from_youtube(url, level)
    except InvalidYouTubeUrl as exc:
        return error_response(
            "invalid_url",
            f"链接无法解析：{exc}",
            "支持 watch?v=、youtu.be/、/shorts/、/embed/ 等常见形式，也可以直接粘贴 11 位视频 ID。",
        )
    except TranscriptError as exc:
        body = exc.to_dict()
        return error_response(
            body["error_code"],
            body["message"],
            body["hint"],
            status=502,
            available_languages=body.get("available_languages"),
        )
    except ValueError as exc:
        return error_response("empty_material", str(exc), "换一个视频或导入字幕文件试试。")
    except Exception as exc:  # noqa: BLE001 - 兜底，避免把栈回溯暴露给前端
        app.logger.exception("生成材料失败")
        return error_response(
            "internal_error",
            f"服务端出错：{type(exc).__name__}",
            "请查看终端日志；也可以先用「载入示例」验证界面功能。",
            status=500,
        )

    return jsonify(material)


@app.post("/api/material/upload")
def api_material_upload():
    level = _resolve_level(request.form.get("level"))
    url = (request.form.get("url") or "").strip()

    uploaded = request.files.get("subtitle")
    if uploaded is None or not uploaded.filename:
        return error_response(
            "missing_file", "没有收到字幕文件。", "请选择一个 .srt 或 .vtt 文件后重试。"
        )

    content = uploaded.read()
    if not content:
        return error_response("empty_file", "字幕文件是空的。", "请检查文件内容后重新上传。")

    try:
        material = get_builder().build_from_subtitles(
            content, uploaded.filename, level, url=url
        )
    except InvalidSubtitleFile as exc:
        return error_response(
            "invalid_subtitle",
            f"字幕文件解析失败：{exc}",
            "请确认文件是标准的 SRT 或 WebVTT 格式。",
        )
    except ValueError as exc:
        return error_response("empty_material", str(exc), "换一个文件试试。")
    except Exception as exc:  # noqa: BLE001
        app.logger.exception("解析上传字幕失败")
        return error_response(
            "internal_error",
            f"服务端出错：{type(exc).__name__}",
            "请查看终端日志。",
            status=500,
        )

    material["video"]["subtitle_filename"] = uploaded.filename
    return jsonify(material)


@app.get("/api/demo")
def api_demo():
    level = _resolve_level(request.args.get("level"))
    try:
        material = get_builder().build_demo(level)
    except Exception as exc:  # noqa: BLE001
        app.logger.exception("加载示例失败")
        return error_response(
            "demo_unavailable",
            f"内置示例不可用：{type(exc).__name__}",
            "请确认 data/demo/ 下的示例文件完整。",
            status=500,
        )
    return jsonify(material)


@app.get("/api/health")
def api_health():
    builder = get_builder()
    resolver = builder.resolver
    glossary = builder.glossary
    glossary.load()  # 必须先加载，否则 source 会停留在初始值 "none"

    return jsonify(
        {
            "status": "ok",
            "version": __version__,
            "python_ok": True,
            "glossary": {
                "source": glossary.source,
                "sources": [
                    {
                        "name": str(item["name"]),
                        "label": SOURCE_LABELS.get(str(item["name"]), str(item["name"])),
                        "entries": item["entries"],
                    }
                    for item in glossary.sources_loaded
                ],
                "entries": glossary.entries,
                "available": glossary.available,
                "warnings": list(glossary.warnings),
                "note": (
                    "llm：用你自己的大模型生成（tools/build_glossary.py --from-llm）；"
                    "wiktionary：从 kaikki.org 英文词典按义项对齐（--from-kaikki，CC BY-SA 3.0）；"
                    "core：本项目自撰精编词表（MIT）。三者按优先级合并。"
                ),
            },
            "level_data": {
                "authoritative": "ELELex (UCLouvain · CENTAL)",
                "available": resolver.elelex_available,
                "entries": resolver.elelex_entries,
                "approximate": "wordfreq（Zipf 词频近似）",
            },
            "demo_available": bool(builder.demo.available),
            "llm": SETTINGS.llm_summary(),
            "default_url": SETTINGS.default_url,
            "default_level": _resolve_level(SETTINGS.default_level),
            "levels": [
                {"level": lv, "label": get_profile(lv).label} for lv in CEFR_LEVELS
            ],
            "license_note": (
                "等级数据 ELELex 为 CC BY-NC-SA 4.0（非商业），"
                "详见 data/third_party/elelex/NOTICE.md"
            ),
        }
    )


# --------------------------------------------------------------------------- #
# 本地启动
# --------------------------------------------------------------------------- #


def main() -> None:
    app_config.load_dotenv_if_present()
    # .env 可能在导入之后才被加载，这里重新同步一次配置
    fresh = app_config.Config.from_env()
    SETTINGS.host, SETTINGS.port, SETTINGS.debug = fresh.host, fresh.port, fresh.debug
    SETTINGS.llm_base_url = fresh.llm_base_url
    SETTINGS.llm_api_key = fresh.llm_api_key
    SETTINGS.llm_model = fresh.llm_model
    SETTINGS.llm_timeout = fresh.llm_timeout
    SETTINGS.yt_proxy_url = fresh.yt_proxy_url
    SETTINGS.default_url = fresh.default_url
    SETTINGS.default_level = fresh.default_level

    print(f"西语 YouTube 学习助手 v{__version__}")
    print(f"打开浏览器访问 http://{SETTINGS.host}:{SETTINGS.port}/")
    print(f"默认示例链接：{SETTINGS.default_url}")
    if not SETTINGS.llm_configured:
        print("提示：未配置大模型（LLM_* 环境变量），句子翻译走离线逐词直译 + 内置示例。")
    app.run(host=SETTINGS.host, port=SETTINGS.port, debug=SETTINGS.debug)


if __name__ == "__main__":
    main()
