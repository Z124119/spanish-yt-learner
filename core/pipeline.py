"""编排层：把字幕 → 分段 → 翻译 → 词形还原 → 等级筛选 → 学习材料。

这是把各部件串起来的地方，也是 Web 接口唯一需要调用的入口：

* :meth:`MaterialBuilder.build_from_youtube` —— 粘贴链接：抓字幕 → 整理
* :meth:`MaterialBuilder.build_from_subtitles` —— 导入 SRT/VTT 文件
* :meth:`MaterialBuilder.build_demo` —— 内置示例（**零网络、零密钥**）

**所有时间戳都会被保留**：句子、段落、单词都能映射回视频中的具体秒数，
前端据此生成「点击跳转」按钮。
"""

from __future__ import annotations

import json
import os
from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
    TimeoutError as FuturesTimeoutError,
    as_completed,
)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import transcript as transcript_module
from .glossary import SOURCE_LABELS, Glossary, guess_pos_label
from .lemmatizer import IRREGULAR_FORMS, is_stopword, lemmatize, normalize_token, tokenize
from .level import CEFR_LEVELS, LevelResolver, get_profile, level_sort_key
from .providers.base import Translation
from .providers.llm import OpenAICompatibleTranslator
from .providers.offline import LiteralGlossTranslator, build_word_glosses
from .segmenter import Paragraph, Sentence, segment_cues
from .subtitle_parser import SubtitleCue, SubtitleDocument, load_subtitle_file, parse_uploaded_subtitle
from .url_parser import VideoRef, format_timestamp, parse_youtube_url

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = PROJECT_ROOT / "data" / "demo"
DEMO_SRT_PATH = DEMO_DIR / "demo_es.srt"
DEMO_TRANSLATIONS_PATH = DEMO_DIR / "demo_translations.json"
DEMO_GLOSSARY_PATH = DEMO_DIR / "demo_glossary.json"

DEMO_DISCLAIMER = (
    "示例模式：字幕与译文均为本项目作者自撰的教学文本，不包含任何第三方字幕内容；"
    "时间点仅用于演示「点击跳转」功能，并不对应视频中的真实画面。"
)

DEPTH_NOTE = {
    "basic": "A1–A2 讲解深度：只给最必要的信息（词性、核心词义、一句提示）。",
    "standard": "B1–B2 讲解深度：给出全部注释，强调搭配与语法点。",
    "nuanced": "C1–C2 讲解深度：给出全部注释，并提示语域与语气；更细的文体分析需接入大模型。",
}

# LLM 并发翻译的默认线程数（可用环境变量 LLM_CONCURRENCY 覆盖）
DEFAULT_LLM_CONCURRENCY = 4
# 缓存条目上限（超出后整体清空；键为 (demo_key, level)）
TRANSLATION_CACHE_LIMIT = 1024
# 并发翻译的整体时间预算上限（秒），防止个别慢请求拖死整个 HTTP 响应
TRANSLATION_DEADLINE_CAP = 180.0
# AI 失败回退时的短文案（不再把异常详情拼进每一句）
FALLBACK_NOTE = "AI 翻译失败，已回退逐词直译。"


def demo_key(text: str) -> str:
    """示例数据查表的键：转小写 + 合并空白（与 ``data/demo/*.json`` 中的键规则一致）。"""
    return " ".join((text or "").lower().split())


@dataclass
class DemoAssets:
    """内置示例素材（自撰，MIT）。"""

    doc: Optional[SubtitleDocument] = None
    translations: Dict[str, dict] = field(default_factory=dict)
    glosses: Dict[str, dict] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.doc is not None and bool(self.doc.cues)


def load_demo_assets(
    *,
    srt_path: Path | None = None,
    translations_path: Path | None = None,
    glossary_path: Path | None = None,
) -> DemoAssets:
    """加载内置示例素材；文件缺失时不抛错，而是返回带警告的空素材。"""
    assets = DemoAssets()
    srt = Path(srt_path) if srt_path else DEMO_SRT_PATH
    translations = Path(translations_path) if translations_path else DEMO_TRANSLATIONS_PATH
    glossary = Path(glossary_path) if glossary_path else DEMO_GLOSSARY_PATH

    if not srt.exists():
        assets.warnings.append(f"未找到内置示例字幕：{srt}")
        return assets

    try:
        assets.doc = load_subtitle_file(srt)
    except Exception as exc:  # noqa: BLE001 - 示例文件损坏也不应让服务起不来
        assets.warnings.append(f"内置示例字幕解析失败：{exc}")
        return assets

    for path, target, label in (
        (translations, assets.translations, "译文"),
        (glossary, assets.glosses, "示例词表"),
    ):
        if not path.exists():
            assets.warnings.append(f"未找到内置示例{label}：{path}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            assets.warnings.append(f"内置示例{label}读取失败：{exc}")
            continue
        key = "sentences" if label == "译文" else "entries"
        target.update(payload.get(key, {}) or {})

    return assets


def _looks_like_proper_noun(token: str, *, sentence_initial: bool) -> bool:
    """判断是否像专有名词：句子中间出现的大写开头单词（如 Ana、Valencia）。

    句首单词一律不判为专名（句首本来就会大写）。
    """
    if sentence_initial:
        return False
    if not token:
        return False
    return token[0].isupper()


def _apply_depth(notes: Sequence[str], depth: str) -> List[str]:
    """按等级深度裁剪讲解注释。"""
    notes = list(notes)
    if depth == "basic":
        return notes[:1]
    return notes


def _vocabulary_hint(vocabulary: Sequence[dict], profile) -> str:
    """当筛出的词数明显偏少时，给出解释，避免用户以为是出了故障。"""
    count = len(vocabulary)
    if count == 0:
        return (
            f"在 {profile.level} 这一档没有筛出需要学习的生词——"
            f"说明这份材料整体难度低于你的等级（当前档位区间：{'-'.join(profile.band)}）。"
            "可以调低等级，或换一段更难的视频。"
        )
    if count < profile.max_vocab * 0.4:
        return (
            f"只筛出 {count} 个 {profile.level} 档的生词（上限 {profile.max_vocab}），"
            "说明材料相对你的等级偏简单；调低等级可以看到更多词汇。"
        )
    return (
        f"按 {profile.level} 档筛出 {count} 个生词"
        f"（区间 {'-'.join(profile.band)}，上限 {profile.max_vocab}）。"
    )


class MaterialBuilder:
    """学习材料生成器。依赖通过构造函数注入，便于测试时替换。"""

    def __init__(
        self,
        *,
        glossary: Optional[Glossary] = None,
        resolver: Optional[LevelResolver] = None,
        translator=None,
        demo: Optional[DemoAssets] = None,
        want_llm: bool = True,
    ) -> None:
        self.glossary = glossary if glossary is not None else Glossary()
        self.resolver = resolver if resolver is not None else LevelResolver()
        self.demo = demo if demo is not None else load_demo_assets()

        if translator is not None:
            self.translator = translator
        elif want_llm:
            # 在线回退链：大模型（若配置齐全）→ MyMemory 免费机翻；
            # 两者都不可用或 want_llm=False 时退回离线逐词直译。
            from .providers.base import ChainedTranslator
            from .providers.mymemory import MyMemoryTranslator

            candidates = []
            llm_candidate = OpenAICompatibleTranslator()
            if llm_candidate.available:
                candidates.append(llm_candidate)
            candidates.append(MyMemoryTranslator())

            if len(candidates) == 1:
                self.translator = candidates[0]
            else:
                self.translator = ChainedTranslator(*candidates)
        else:
            self.translator = LiteralGlossTranslator(self.glossary)

        # ---- 翻译并发与缓存 ----
        try:
            workers = int(os.environ.get("LLM_CONCURRENCY", "") or DEFAULT_LLM_CONCURRENCY)
        except ValueError:
            workers = DEFAULT_LLM_CONCURRENCY
        self._llm_max_workers = max(1, workers)
        # 进程级缓存：键 (demo_key, level)，只缓存提供方成功结果（失败不缓存，保留自愈机会）
        self._translation_cache: Dict[Tuple[str, str], Translation] = {}
        # 共享的离线回退实例（替代每次失败新建）
        self._fallback = LiteralGlossTranslator(self.glossary)

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------

    def build_from_youtube(self, url: str, level: str) -> dict:
        """粘贴链接 → 抓字幕 → 生成学习材料。"""
        profile = get_profile(level)
        ref = parse_youtube_url(url)
        result = transcript_module.fetch_transcript(ref.video_id)

        material = self._assemble(
            cues=result.cues,
            level=profile.level,
            source="youtube",
            video=ref,
            warnings=list(result.warnings),
            language=result.language,
            language_code=result.language_code,
            is_generated=result.is_generated,
        )
        material["video"]["subtitle_origin"] = "人工字幕" if not result.is_generated else "自动生成字幕"
        return material

    def build_from_subtitles(
        self,
        content: bytes,
        filename: str,
        level: str,
        *,
        url: str = "",
    ) -> dict:
        """导入 SRT/VTT 字幕文件。可选带一个视频链接，用于时间跳转。"""
        profile = get_profile(level)
        doc = parse_uploaded_subtitle(content, filename)

        ref: Optional[VideoRef] = None
        warnings = list(doc.warnings)
        if url and url.strip():
            try:
                ref = parse_youtube_url(url)
            except Exception as exc:  # noqa: BLE001 - 链接无效不影响字幕整理
                warnings.append(f"附带链接无法解析，已忽略（{exc}）。时间点仍会保留，但无法生成跳转。")

        return self._assemble(
            cues=doc.cues,
            level=profile.level,
            source="import",
            video=ref,
            warnings=warnings,
            subtitle_filename=filename,
        )

    def build_demo(self, level: str) -> dict:
        """内置示例：不访问网络、不需要任何密钥。"""
        profile = get_profile(level)
        if not self.demo.available:
            raise RuntimeError("内置示例素材不可用：" + "；".join(self.demo.warnings))

        ref: Optional[VideoRef] = None
        try:
            # 用默认测试链接的 video_id 让「点击跳转」在界面上可见
            from config import DEFAULT_TEST_URL

            ref = parse_youtube_url(DEFAULT_TEST_URL)
        except Exception:  # pragma: no cover - 默认链接理论上一定可解析
            ref = None

        warnings = list(self.demo.warnings)
        warnings.append(DEMO_DISCLAIMER)

        material = self._assemble(
            cues=self.demo.doc.cues,
            level=profile.level,
            source="demo",
            video=ref,
            warnings=warnings,
            language="Español (示例)",
            language_code="es",
            is_generated=False,
            authored_translations=self.demo.translations,
            authored_glossary=self.demo.glosses,
        )
        material["video"]["subtitle_origin"] = "内置示例字幕（自撰）"
        material["video"]["is_demo"] = True
        return material

    # ------------------------------------------------------------------
    # 核心组装
    # ------------------------------------------------------------------

    def _assemble(
        self,
        *,
        cues: Sequence[SubtitleCue],
        level: str,
        source: str,
        video: Optional[VideoRef] = None,
        warnings: Optional[List[str]] = None,
        language: str = "",
        language_code: str = "",
        is_generated: bool = False,
        subtitle_filename: str = "",
        authored_translations: Optional[Dict[str, dict]] = None,
        authored_glossary: Optional[Dict[str, dict]] = None,
    ) -> dict:
        profile = get_profile(level)
        warnings = list(warnings or [])
        authored_translations = authored_translations or {}
        authored_glossary = authored_glossary or {}

        sentences, paragraphs = segment_cues(cues)
        if not sentences:
            raise ValueError("字幕里没有可用的文本内容。")

        # ---- 句子与逐词释义 ----
        segments: List[dict] = []
        authored_hits = 0
        translated = 0
        fallback_hits = 0

        # 先批量翻译（authored → 缓存 → 并发 LLM/离线），再组装
        translations = self._translate_all(
            [s.text for s in sentences], level=profile.level, authored=authored_translations
        )

        for sentence in sentences:
            translation = translations.get(sentence.text)
            if translation is not None:
                translated += 1
                if translation.source == "authored":
                    authored_hits += 1
                elif translation.source == "offline-literal":
                    fallback_hits += 1

            glosses = build_word_glosses(
                sentence.text, self.glossary, override=authored_glossary
            ).glosses
            notes_source = authored_translations.get(demo_key(sentence.text), {}) if authored_translations else {}
            notes = _apply_depth(notes_source.get("notes", []) or [], profile.notes_depth)

            segments.append(
                {
                    "index": sentence.index,
                    "start": round(sentence.start, 3),
                    "end": round(sentence.end, 3),
                    "start_label": format_timestamp(sentence.start),
                    "text_es": sentence.text,
                    "translation_zh": translation.text if translation else "",
                    "translation_approximate": bool(translation.approximate) if translation else True,
                    "translation_source": translation.source if translation else "none",
                    "notes": notes,
                    "glosses": [g.to_dict() for g in glosses],
                }
            )

        paragraph_payload = [
            {
                "index": p.index,
                "start": round(p.start, 3),
                "end": round(p.end, 3),
                "start_label": format_timestamp(p.start),
                "text_es": p.text,
                "sentence_indexes": list(p.sentence_indices),
            }
            for p in paragraphs
        ]

        # ---- 词汇筛选 ----
        vocabulary, vocab_stats = self._build_vocabulary(
            sentences, profile, authored_glossary=authored_glossary
        )

        covered = sum(1 for v in vocabulary if v["meaning_zh"])
        stats = {
            "cues": len(cues),
            "sentences": len(sentences),
            "paragraphs": len(paragraphs),
            "vocabulary": len(vocabulary),
            "vocabulary_with_meaning": covered,
            "sentences_translated": translated,
            "sentences_authored_translation": authored_hits,
            **vocab_stats,
        }

        if vocabulary and covered * 2 < len(vocabulary):
            warnings.append(
                f"词表中有 {len(vocabulary) - covered} 个词未收录中文释义。"
                "运行 `python tools/build_glossary.py` 可生成更完整的西汉词表。"
            )
        if fallback_hits and self._online_enabled:
            warnings.append(
                f"有 {fallback_hits} 句在线翻译失败或超时，已回退为逐词直译（仅供参考）。"
            )
        if profile.notes_depth != "basic" and not authored_hits:
            warnings.append(
                "当前材料的逐句讲解来自离线模式，内容有限；"
                "如需更细致的语法/语域讲解，可配置大模型（见 .env.example）。"
            )

        material = {
            "level": profile.level,
            "level_profile": {
                "level": profile.level,
                "band": list(profile.band),
                "max_vocab": profile.max_vocab,
                "notes_depth": profile.notes_depth,
                "label": profile.label,
                "depth_note": DEPTH_NOTE.get(profile.notes_depth, ""),
            },
            "vocabulary_hint": _vocabulary_hint(vocabulary, profile),
            "source": source,
            "video": self._video_payload(video, language, language_code, is_generated, subtitle_filename),
            "warnings": warnings,
            "stats": stats,
            "vocabulary": vocabulary,
            "segments": segments,
            "paragraphs": paragraph_payload,
            "meta": {
                "level_data": self._level_data_summary(),
                "glossary": self._glossary_summary(),
                "translator": self._translator_summary(),
                "levels_available": list(CEFR_LEVELS),
                "license_note": "第三等级数据 ELELex 为 CC BY-NC-SA 4.0（非商业），详见 data/third_party/elelex/NOTICE.md",
            },
        }
        return material

    # ------------------------------------------------------------------
    # 子步骤
    # ------------------------------------------------------------------

    def _translate_all(
        self,
        texts: Sequence[str],
        *,
        level: str,
        authored: Dict[str, dict],
    ) -> Dict[str, Translation]:
        """批量翻译：自撰译文 → 缓存 → 提供方（LLM 并发 / 离线串行）。

        返回 ``{原文: Translation}``（键为传入的原样文本）。
        失败的句子返回离线直译回退，保证每个句子都有条目。
        """
        results: Dict[str, Translation] = {}
        pending: Dict[str, str] = {}  # demo_key -> 该键首次出现的原样文本

        for text in texts:
            entry = authored.get(demo_key(text))
            if entry and entry.get("zh"):
                results[text] = Translation(
                    text=entry["zh"], approximate=False, source="authored",
                    note="译文由本项目作者撰写。",
                )
                continue
            cache_key = (demo_key(text), level)
            hit = self._translation_cache.get(cache_key)
            if hit is not None:
                results[text] = hit
                continue
            pending.setdefault(demo_key(text), text)

        if pending:
            keys = list(pending)
            if self._online_enabled and len(keys) > 1:
                self._translate_pending_concurrent(pending, level=level, results=results)
            else:
                for key in keys:
                    translation = self._translate_one(pending[key], level=level)
                    if translation is not None:
                        results[pending[key]] = translation

        return results

    def _translate_pending_concurrent(
        self,
        pending: Dict[str, str],
        *,
        level: str,
        results: Dict[str, Translation],
    ) -> None:
        """用线程池并发调用 LLM，带整体时间预算；超时的句子回退离线直译。"""
        timeout = getattr(self.translator, "_timeout", 30) or 30
        workers = min(self._llm_max_workers, len(pending))
        deadline = min(
            TRANSLATION_DEADLINE_CAP,
            max(60.0, len(pending) * timeout / workers + 15.0),
        )

        pool = ThreadPoolExecutor(max_workers=workers)
        futures: Dict[Future, str] = {
            pool.submit(self._translate_one, text, level=level): key
            for key, text in pending.items()
        }
        pool.shutdown(wait=False)  # 提交后不阻塞退出，靠 as_completed 收集

        try:
            for future in as_completed(futures, timeout=deadline):
                key = futures[future]
                try:
                    translation = future.result()
                except Exception:  # noqa: BLE001 - 单句失败不影响其它句子
                    translation = None
                results[pending[key]] = (
                    translation if translation is not None else self._offline_fallback(pending[key])
                )
        except FuturesTimeoutError:
            # 整体预算耗尽：未完成的句子立即回退离线，不硬杀后台线程
            pass
        finally:
            for future, key in futures.items():
                if key not in results and not future.done():
                    future.cancel()
                    fallback = self._offline_fallback(pending[key])
                    if fallback is not None:
                        results[pending[key]] = fallback

    def _translate_one(self, text: str, *, level: str) -> Optional[Translation]:
        """翻译单句：提供方成功则写缓存；任何失败回退离线直译（短文案）。"""
        try:
            translation = self.translator.translate(text, level=level)  # type: ignore[call-arg]
        except TypeError:
            # 提供方实现不支持 level 参数
            try:
                translation = self.translator.translate(text)
            except Exception:
                translation = None
        except Exception:  # noqa: BLE001 - ProviderNotConfigured/超时/HTTP 错误统一回退
            translation = None

        if translation is not None:
            cache_key = (demo_key(text), level)
            if len(self._translation_cache) >= TRANSLATION_CACHE_LIMIT:
                self._translation_cache.clear()
            self._translation_cache[cache_key] = translation
            return translation
        return self._offline_fallback(text)

    def _offline_fallback(self, text: str) -> Optional[Translation]:
        """离线逐词直译回退，note 用统一短文案。"""
        try:
            result = self._fallback.translate(text)
        except Exception:  # noqa: BLE001
            return None
        if result is not None:
            result.note = FALLBACK_NOTE
        return result

    @property
    def _online_enabled(self) -> bool:
        """翻译提供方是否联网（决定是否值得并发）。"""
        return bool(getattr(self.translator, "concurrent", False))

    def _build_vocabulary(
        self,
        sentences: Sequence[Sentence],
        profile,
        *,
        authored_glossary: Dict[str, dict],
    ) -> tuple[List[dict], dict]:
        """提取并筛选「符合当前等级」的词汇，全部保留首次出现的时间戳。

        ``term``/``lemma`` 保留**带重音**的规范形式（如 ``canción``）便于展示；
        内部查表统一使用去重音的 ``key``；``surface`` 记录视频里实际出现的形态。
        """
        counts: Dict[str, int] = {}
        surfaces: Dict[str, str] = {}
        lemmas: Dict[str, str] = {}
        first_ts: Dict[str, float] = {}
        proper_nouns: List[str] = []

        for sentence in sentences:
            tokens = tokenize(sentence.text)
            for position, token in enumerate(tokens):
                if len(token) < 2 or is_stopword(token):
                    continue
                if _looks_like_proper_noun(token, sentence_initial=(position == 0)):
                    proper_nouns.append(token)
                    continue

                result = lemmatize(token)
                key = normalize_token(result.lemma)
                if not key:
                    continue

                counts[key] = counts.get(key, 0) + 1
                surfaces.setdefault(key, token.lower())
                lemmas.setdefault(key, result.lemma or key)
                first_ts.setdefault(key, sentence.start)

        items: List[tuple[dict, str]] = []
        for key, count in counts.items():
            estimate = self.resolver.resolve(key)
            if not profile.matches(estimate.level):
                continue

            entry = authored_glossary.get(key) or {}
            senses = self.glossary.lookup(key)
            meanings = list(entry.get("zh", [])) or [s.meaning for s in senses]
            pos = entry.get("pos") or (guess_pos_label(senses[0].pos) if senses else "")
            lemma_display = lemmas.get(key, key)

            item = {
                "term": lemma_display,
                "lemma": lemma_display,
                "key": key,
                "surface": surfaces.get(key, key),
                "pos": pos,
                "level": estimate.level,
                "level_source": estimate.source,
                "level_note": estimate.note,
                "zipf": estimate.zipf,
                "count": count,
                "meaning_zh": meanings,
                "meaning_note": entry.get("note", ""),
                "timestamp": round(first_ts.get(key, 0.0), 3),
                "timestamp_label": format_timestamp(first_ts.get(key, 0.0)),
            }
            items.append((item, estimate.level))

        # 排序：出现次数多的优先；同次数时等级高的优先（更难、更值得学）
        items.sort(
            key=lambda pair: (
                -pair[0]["count"],
                -level_sort_key(pair[1]),
                pair[0]["term"],
            )
        )

        selected = [item for item, _ in items[: profile.max_vocab]]
        band_counts: Dict[str, int] = {}
        for _, level in items:
            band_counts[level] = band_counts.get(level, 0) + 1

        stats = {
            "vocabulary_candidates": len(items),
            "vocabulary_band_counts": band_counts,
            "proper_nouns_skipped": len(set(proper_nouns)),
        }
        return selected, stats

    def _video_payload(
        self,
        video: Optional[VideoRef],
        language: str,
        language_code: str,
        is_generated: bool,
        subtitle_filename: str,
    ) -> dict:
        payload = {
            "video_id": video.video_id if video else "",
            "start_seconds": video.start_seconds if video else 0,
            "watch_url": video.watch_url if video else "",
            "embed_url": video.embed_url() if video else "",
            "start_label": format_timestamp(video.start_seconds) if video else "00:00",
            "language": language,
            "language_code": language_code,
            "is_generated": bool(is_generated),
            "subtitle_filename": subtitle_filename,
            "has_player": bool(video),
            "is_demo": False,
        }
        return payload

    def _level_data_summary(self) -> dict:
        return {
            "authoritative_source": "ELELex (UCLouvain · CENTAL)",
            "authoritative_available": self.resolver.elelex_available,
            "authoritative_entries": self.resolver.elelex_entries,
            "authoritative_covers": self.resolver.elelex_covers,
            "approximate_source": "wordfreq (Zipf 词频近似)",
            "note": (
                "ELELex 只覆盖 A1–C1，因此 C2 及未被收录的词使用 wordfreq 词频近似档位；"
                "近似档位仅作参考，不等同于权威 CEFR 标注。"
            ),
        }

    def _glossary_summary(self) -> dict:
        # 先 load()，否则 source 可能还停留在初始值 "none"
        self.glossary.load()
        return {
            "source": self.glossary.source,
            "sources": [
                {
                    "name": str(item["name"]),
                    "label": SOURCE_LABELS.get(str(item["name"]), str(item["name"])),
                    "entries": item["entries"],
                }
                for item in self.glossary.sources_loaded
            ],
            "entries": self.glossary.entries,
            "warnings": list(self.glossary.warnings),
        }

    def _translator_summary(self) -> dict:
        name = getattr(self.translator, "name", "unknown")
        model = ""
        note = "离线模式：逐词直译（仅供参考）+ 内置示例的人工译文。"

        def _member_note(provider) -> str:
            member_name = getattr(provider, "name", "unknown")
            if member_name.startswith("llm"):
                member_status = getattr(provider, "status", None)
                member_model = (
                    (callable(member_status) and (member_status() or {}).get("model")) or ""
                )
                return f"大模型（{member_model}）" if member_model else "大模型"
            if member_name.startswith("mymemory"):
                return "MyMemory 免费机翻"
            return member_name

        if name == "translation-chain":
            members = getattr(self.translator, "_providers", [])
            chain = " → ".join(_member_note(p) for p in members)
            note = f"在线回退链：{chain} →（最终兜底）离线逐词直译。"
        elif name.startswith("llm"):
            status = getattr(self.translator, "status", None)
            model = ((callable(status) and (status() or {}).get("model")) or "")
            note = f"使用大模型（{model}）做整句翻译。" if model else "使用大模型做整句翻译。"
        elif name.startswith("mymemory"):
            note = "使用 MyMemory 免费机翻做整句翻译（每日限额，超限自动回退离线直译）。"

        info = {
            "name": name,
            "is_llm": name.startswith("llm") or name == "translation-chain",
            "note": note,
        }
        status = getattr(self.translator, "status", None)
        if callable(status):
            info["status"] = status()
        return info


def build_material(  # noqa: D401 - 便捷函数
    url: str, level: str, *, builder: Optional[MaterialBuilder] = None
) -> dict:
    """便捷入口：粘贴链接直接生成学习材料。"""
    return (builder or MaterialBuilder()).build_from_youtube(url, level)
