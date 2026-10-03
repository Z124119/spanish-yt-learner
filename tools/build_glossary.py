#!/usr/bin/env python3
"""生成更完整的西汉词表（**可选步骤**，不运行任何模式也能正常使用本工具）。

两种模式，二选一：

``--from-llm``
    用**你自己配置的** OpenAI 兼容大模型，为「某段字幕里出现、但当前词表没有」
    的西语词生成中文释义，增量写入 ``data/llm_glossary.jsonl.gz``。

    必需环境变量（密钥只从环境变量读取，**绝不写入代码或仓库**）::

        LLM_BASE_URL   例如 https://api.openai.com/v1
        LLM_API_KEY    你的密钥
        LLM_MODEL      例如 gpt-4o-mini

    词汇来源二选一：``--srt 文件.srt``（本地字幕）或 ``--url <YouTube 链接>``。

``--from-kaikki``
    流式处理 kaikki.org 的**英文**词典 dump（约 3.1 GB），把每个英语词条里
    「**同一义项**下同时给出西语和中文翻译」的配对抽出来，按词频排序后写入
    ``data/es_zh_glossary.jsonl.gz``（数据授权 CC BY-SA 3.0，**不入库**）。

    为什么必须用英文 dump：英文维基词典只有「英语词条页」带跨语言 Translations
    区块；西语词条页（kaikki 的 Spanish dump，1.05 GB）完全不含 ``translations``
    字段（已实测验证）。因此西→中词表只能通过英语义项做枢纽对齐得到。

    * 默认**边下载边解析**，磁盘上只留下最终的 gzip 结果（不落盘 3.1 GB）。
    * ``--max-bytes N`` 可先截取前 N 字节小规模试跑，验证流程。
    * ``--input 文件.jsonl`` 可改用已下载好的本地文件。

两个模式都会：
  * 跳过词表里已有的词（增量合并，不会覆盖 ``core_glossary.json``）；
  * 用 wordfreq 给西语词标注 Zipf 词频（``z`` 字段）并按词频降序输出，
    这样词表文件的开头就是最常用的部分；
  * 打印生成文件的位置、条数与后续使用方式。

许可与署名
----------
* ``--from-kaikki`` 的产物源自 Wiktionary 文本，授权 **CC BY-SA 3.0**；
  脚本会同时写出 ``data/es_zh_glossary.SOURCE.txt``，记录来源 URL、抓取日期
  与署名要求。**该文件请勿提交到仓库**（已在 .gitignore 中排除）。
* ``--from-llm`` 的产物是你自己生成的数据，如何使用由你决定。

示例
----
::

    # 用大模型为示例字幕补全释义（最快见效）
    python tools/build_glossary.py --from-llm --srt data/demo/demo_es.srt

    # 先小规模试跑 kaikki 对齐（只读前 200 MB，约 1-2 分钟）
    python tools/build_glossary.py --from-kaikki --max-bytes 200000000

    # 完整跑（3.1 GB，视网速与机器约 10-30 分钟）
    python tools/build_glossary.py --from-kaikki
"""

from __future__ import annotations

import argparse
import codecs
import gzip
import json
import re
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.glossary import Glossary  # noqa: E402
from core.lemmatizer import content_lemmas, normalize_token  # noqa: E402
from core.segmenter import segment_cues  # noqa: E402
from core.subtitle_parser import load_subtitle_file, parse_uploaded_subtitle  # noqa: E402

LLM_GLOSSARY_PATH = PROJECT_ROOT / "data" / "llm_glossary.jsonl.gz"
FULL_GLOSSARY_PATH = PROJECT_ROOT / "data" / "es_zh_glossary.jsonl.gz"
FULL_GLOSSARY_NOTICE = PROJECT_ROOT / "data" / "es_zh_glossary.SOURCE.txt"

KAIKKI_ENGLISH_URL = "https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl"

# kaikki 英文 dump 里中文相关语言的 lang_code / lang 取值
ZH_LANG_CODES = ("zh", "zh-cn", "zh-tw", "zh-hans", "zh-hant", "zh-hak", "zh-nan", "zh-yue")
ZH_LANG_NAMES = ("chinese", "mandarin chinese", "literary chinese", "classical chinese")
# 东干语用西里尔字母书写，不是我们要的汉字词形
ZH_EXCLUDE_TAGS = {"dungan"}

KAikki_NOTICE = """西汉词表来源与许可说明
======================

生成时间 : {generated}
生成方式 : tools/build_glossary.py --from-kaikki
数据来源 : kaikki.org machine-readable English dictionary
           {url}
上游数据 : English Wiktionary（en.wiktionary.org），经 wiktextract 抽取
词条数量 : {count}
抽取规则 : 仅保留「同一义项下同时给出西语(es)与中文(zh)翻译」的配对，
           按西语词的 wordfreq Zipf 词频降序排列。
           每行 JSON 字段：w=西语词形, p=词性, zh=中文释义列表,
           tags=语法标签(如性别), z=Zipf 词频, src=来源标记。

授权与署名
----------
Wiktionary 的文本内容采用 **CC BY-SA 3.0** 许可：
  https://creativecommons.org/licenses/by-sa/3.0/
  https://en.wiktionary.org/wiki/Wiktionary:Copyright

使用本词表时请：
  1. 注明来源（Wiktionary / kaikki.org）；
  2. 以相同方式共享（ShareAlike）衍生数据；
  3. 本项目代码本身不包含该数据，因此仓库代码仍为 MIT。

请勿把本文件提交进 Git 仓库（已在 .gitignore 中排除）。
"""


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #


def _cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _clean_es_word(word: str) -> str:
    """去掉 "(el) agua" 这类括注，以及首尾空白。"""
    return re.sub(r"\([^)]*\)", "", word or "").strip()


def _normalize_zh(word: str) -> str:
    """Wiktionary 的中文翻译多写作「繁体 / 简体」（如 "詞 /词"）。

    面向简体中文学习者，取最后一个非空片段（通常是简体）；没有斜杠时原样保留。
    """
    parts = [p.strip() for p in str(word or "").split("/")]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    return parts[-1]


# 只保留对学习者有直接帮助的语法标签（性别）；语域类标签（colloquial 等）噪声太大
USEFUL_ES_TAGS = {"masculine", "feminine", "neuter", "common"}


def _zipf(word: str) -> float:
    try:
        from wordfreq import zipf_frequency

        return round(zipf_frequency(word, "es"), 3)
    except Exception:  # wordfreq 缺失时不阻塞主流程
        return 0.0


def _load_jsonl(path: Path) -> "OrderedDict[str, dict]":
    rows: "OrderedDict[str, dict]" = OrderedDict()
    if not path.exists():
        return rows
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:  # type: ignore[operator]
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = normalize_token(str(row.get("w", "")))
            if key:
                rows[key] = row
    return rows


def _write_jsonl_gz(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


# --------------------------------------------------------------------------- #
# 模式一：LLM 按需补全
# --------------------------------------------------------------------------- #


def collect_lemmas_from_srt(path: Path) -> List[str]:
    doc = load_subtitle_file(path)
    sentences, _ = segment_cues(doc.cues)
    lemmas: "OrderedDict[str, None]" = OrderedDict()
    for sentence in sentences:
        for lemma in content_lemmas(sentence.text):
            lemmas.setdefault(lemma, None)
    return list(lemmas)


def collect_lemmas_from_url(url: str) -> List[str]:
    from core.transcript import fetch_transcript
    from core.url_parser import parse_youtube_url

    ref = parse_youtube_url(url)
    result = fetch_transcript(ref.video_id)
    sentences, _ = segment_cues(result.cues)
    lemmas: "OrderedDict[str, None]" = OrderedDict()
    for sentence in sentences:
        for lemma in content_lemmas(sentence.text):
            lemmas.setdefault(lemma, None)
    return list(lemmas)


def build_llm_prompt(batch: Sequence[str]) -> str:
    words = "\n".join("- " + w for w in batch)
    return (
        "你是一名面向中国学习者的西班牙语词典编辑。"
        "下面是若干西班牙语词元（lemma，已小写、保留重音）。"
        "请为每个词给出简明的中文释义，输出**严格 JSON 数组**，不要任何解释文字。\n"
        '每条格式：{"word": "西语词", "pos": "词性缩写(n./v./adj./adv./prep./pron./conj./num./interj.)", '
        '"zh": ["中文释义1", "中文释义2"]}\n'
        "要求：\n"
        "1. zh 最多 3 条，每条尽量短（词组搭配可写在括号里，如「黄昏，傍晚（al atardecer = 在黄昏时）」）；\n"
        "2. 只解释这些词作为**西班牙语**单词的含义，不要解释成英语或其他语言；\n"
        "3. 若某词无法确定，zh 给 [\"（未确定）\"] 并保留该条；\n"
        "4. 不要新增列表之外的词。\n\n"
        "词元列表：\n" + words
    )


def _extract_json_array(text: str) -> Optional[list]:
    """从模型回复里稳健地取出 JSON 数组（容忍代码块围栏与前后杂讯）。"""
    if not text:
        return None
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def _safe_message(exc: Exception, api_key: str) -> str:
    message = str(exc)
    if api_key and api_key in message:
        message = message.replace(api_key, "***")
    return message


def run_llm_mode(args: argparse.Namespace) -> int:
    import os

    import requests

    from config import load_dotenv_if_present

    # .env 只提供默认值，已存在的环境变量优先；密钥不落盘、不打印
    load_dotenv_if_present()
    base_url = (args.llm_base_url or os.environ.get("LLM_BASE_URL") or "").strip().rstrip("/")
    api_key = (args.llm_api_key or os.environ.get("LLM_API_KEY") or "").strip()
    model = (args.llm_model or os.environ.get("LLM_MODEL") or "").strip()
    if not (base_url and api_key and model):
        print(
            "✗ 缺少大模型配置。请设置环境变量 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL\n"
            "  （可参考项目根目录的 .env.example，密钥只放在环境变量或 .env，不要写进代码）。",
            file=sys.stderr,
        )
        return 2

    # ---- 1. 收集需要补全的词 ---------------------------------------------
    if args.srt:
        print(f"读取字幕文件 {args.srt} …")
        lemmas = collect_lemmas_from_srt(Path(args.srt))
    elif args.url:
        print(f"抓取字幕 {args.url} …")
        lemmas = collect_lemmas_from_url(args.url)
    else:
        print("✗ --from-llm 需要 --srt 或 --url 指定词汇来源。", file=sys.stderr)
        return 2

    print(f"共提取到 {len(lemmas)} 个内容词元。")

    glossary = Glossary().load()
    missing = [w for w in lemmas if not glossary.has(w)]
    print(f"其中 {len(missing)} 个词当前词表没有释义。")
    if not missing:
        print("没有需要补全的词，结束。")
        return 0

    # 最常用的词优先补
    missing.sort(key=lambda w: -_zipf(w))
    if args.limit and len(missing) > args.limit:
        print(f"按 --limit {args.limit} 截断（最常用的优先）。")
        missing = missing[: args.limit]

    # ---- 2. 分批请求 -----------------------------------------------------
    rows = _load_jsonl(LLM_GLOSSARY_PATH)
    added = 0
    batch_size = max(5, args.batch_size)
    session = requests.Session()
    session.headers.update(
        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    )

    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        print(f"请求大模型：第 {start + 1}-{start + len(batch)} / {len(missing)} 个词 …",
              flush=True)
        try:
            resp = session.post(
                base_url + "/chat/completions",
                json={
                    "model": model,
                    "temperature": 0.2,
                    "messages": [{"role": "user", "content": build_llm_prompt(batch)}],
                },
                timeout=args.timeout,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            print(f"✗ 请求失败：{_safe_message(exc, api_key)}", file=sys.stderr)
            print("  已完成的部分仍会保存。", file=sys.stderr)
            break

        data = _extract_json_array(content)
        if not data:
            print("  ⚠ 回复里没有解析到 JSON 数组，跳过这一批。", file=sys.stderr)
            continue

        ok = 0
        for item in data:
            if not isinstance(item, dict):
                continue
            word = _clean_es_word(str(item.get("word", "")))
            zh = item.get("zh") or []
            if isinstance(zh, str):
                zh = [zh]
            zh = [str(m).strip() for m in zh if str(m).strip() and str(m).strip() != "（未确定）"]
            key = normalize_token(word)
            if not key or not zh or key in rows:
                continue
            rows[key] = {
                "w": word,
                "p": str(item.get("pos", "")).strip(),
                "zh": zh[:3],
                "z": _zipf(word),
                "src": "llm",
            }
            ok += 1
        added += ok
        print(f"  ✓ 解析到 {ok} 条。")

    if not added:
        print("没有新增词条。")
        return 1

    # ---- 3. 写盘（按词频降序） -------------------------------------------
    ordered = sorted(rows.values(), key=lambda r: -float(r.get("z") or 0.0))
    count = _write_jsonl_gz(LLM_GLOSSARY_PATH, ordered)
    print(f"\n✓ 已写入 {count} 条到 {LLM_GLOSSARY_PATH.relative_to(PROJECT_ROOT)}（本次新增 {added}）。")
    print("  重新启动 Web 服务后，这些词就会带中文释义。")
    print("  该文件由你的大模型生成，默认不入库（.gitignore 已排除）。")
    return 0


# --------------------------------------------------------------------------- #
# 模式二：kaikki 英文 dump 枢纽对齐
# --------------------------------------------------------------------------- #


def _is_zh(entry: dict) -> bool:
    code = str(entry.get("lang_code", "")).lower()
    lang = str(entry.get("lang", "")).lower()
    if code in ZH_LANG_CODES or lang in ZH_LANG_NAMES:
        tags = {str(t).lower() for t in (entry.get("tags") or [])}
        return not (tags & ZH_EXCLUDE_TAGS)
    return False


def _is_es(entry: dict) -> bool:
    return str(entry.get("lang_code", "")).lower() == "es"


def extract_pairs_from_entry(obj: dict) -> Iterator[Tuple[str, str, str, List[str]]]:
    """从单个英语词条里抽出 (西语词, 中文词, 词性, 语法标签) 配对。

    对齐依据：同一个 ``sense``（英语义项说明）下同时出现 es 与 zh 翻译。
    """
    pos = str(obj.get("pos", "")).strip()
    seen_senses: set = set()
    pools: List[List[dict]] = []

    for sense in obj.get("senses") or []:
        translations = sense.get("translations") or []
        if not translations:
            continue
        sense_key = str(sense.get("glosses") or sense.get("id") or id(sense))
        if sense_key in seen_senses:
            continue
        seen_senses.add(sense_key)
        pools.append(list(translations))

    top_level = obj.get("translations") or []
    if top_level:
        pools.append(list(top_level))

    for translations in pools:
        es_words: "OrderedDict[str, List[str]]" = OrderedDict()
        zh_words: "OrderedDict[str, None]" = OrderedDict()
        es_tags: List[str] = []

        for tr in translations:
            word = _clean_es_word(str(tr.get("word", "")))
            if not word:
                continue
            if _is_es(tr):
                es_words.setdefault(word, [])
                for tag in tr.get("tags") or []:
                    if str(tag) not in es_tags:
                        es_tags.append(str(tag))
            elif _is_zh(tr):
                zh_word = _normalize_zh(tr.get("word"))
                if _cjk(zh_word):
                    zh_words.setdefault(zh_word, None)

        if not es_words or not zh_words:
            continue

        for es_word in es_words:
            for zh_word in zh_words:
                yield es_word, zh_word, pos, es_tags


def _iter_jsonl_chunks(lines: Iterable[str]) -> Iterator[dict]:
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _stream_remote(url: str, max_bytes: int) -> Iterator[dict]:
    import requests

    print(f"流式下载 {url}")
    print("（不落盘：边下载边解析，磁盘上只留最终的 gzip 结果）")
    if max_bytes:
        print(f"本次截取前 {max_bytes / 1e6:.0f} MB 用于试跑。")

    with requests.get(url, stream=True, timeout=(20, 120)) as resp:
        resp.raise_for_status()
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        buffer = ""
        bytes_read = 0
        line_no = 0
        start = time.time()

        for chunk in resp.iter_content(chunk_size=1 << 20):
            bytes_read += len(chunk)
            buffer += decoder.decode(chunk, final=False)
            if "\n" not in buffer:
                if max_bytes and bytes_read >= max_bytes:
                    break
                continue
            *complete, buffer = buffer.split("\n")
            for line in complete:
                line_no += 1
                obj = None
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    obj = None
                if obj is not None:
                    yield obj
            if line_no and line_no % 200000 == 0:
                rate = line_no / max(time.time() - start, 0.001)
                print(f"  … {line_no} 行 | {bytes_read / 1e6:.0f} MB | {rate:,.0f} 行/秒",
                      flush=True)
            if max_bytes and bytes_read >= max_bytes:
                break


def _stream_local(path: Path) -> Iterator[dict]:
    print(f"读取本地文件 {path}")
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        line_no = 0
        start = time.time()
        for line in handle:
            line_no += 1
            if line_no % 200000 == 0:
                rate = line_no / max(time.time() - start, 0.001)
                print(f"  … {line_no} 行 | {rate:,.0f} 行/秒", flush=True)
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def run_kaikki_mode(args: argparse.Namespace) -> int:
    pairs: "OrderedDict[str, dict]" = OrderedDict()
    entries = 0
    started = time.time()

    source: Iterator[dict] = (
        _stream_local(Path(args.input))
        if args.input
        else _stream_remote(KAIKKI_ENGLISH_URL, args.max_bytes)
    )

    for obj in source:
        entries += 1
        if str(obj.get("lang_code", "")).lower() not in ("en", ""):
            continue
        if str(obj.get("pos", "")).lower() == "name":
            continue  # 专名（人名/地名）对学习者帮助有限，跳过
        for es_word, zh_word, pos, tags in extract_pairs_from_entry(obj):
            key = normalize_token(es_word)
            if not key:
                continue
            row = pairs.get(key)
            if row is None:
                row = {"w": es_word, "p": pos, "zh": [], "tags": [], "z": 0.0, "src": "wiktionary"}
                pairs[key] = row
            if zh_word not in row["zh"]:
                row["zh"].append(zh_word)
            if len(row["zh"]) >= 4:
                row["zh"] = row["zh"][:4]
            for tag in tags:
                if tag in USEFUL_ES_TAGS and tag not in row["tags"] and len(row["tags"]) < 3:
                    row["tags"].append(tag)

    print(f"\n扫描完成：{entries} 个词条，用时 {time.time() - started:.0f} 秒。")
    if not pairs:
        print("✗ 没有抽到任何西→中配对。若用了 --max-bytes，请调大后重试。",
              file=sys.stderr)
        return 1

    print("计算词频并排序 …")
    for row in pairs.values():
        row["z"] = _zipf(str(row["w"]))
    ordered = sorted(pairs.values(), key=lambda r: -float(r.get("z") or 0.0))

    count = _write_jsonl_gz(FULL_GLOSSARY_PATH, ordered)
    print(f"✓ 已写入 {count} 条到 {FULL_GLOSSARY_PATH.relative_to(PROJECT_ROOT)}")

    FULL_GLOSSARY_NOTICE.write_text(
        KAikki_NOTICE.format(
            generated=time.strftime("%Y-%m-%d %H:%M:%S"),
            url=KAIKKI_ENGLISH_URL,
            count=count,
        ),
        encoding="utf-8",
    )
    print(f"✓ 来源与许可说明已写入 {FULL_GLOSSARY_NOTICE.relative_to(PROJECT_ROOT)}")
    print("\n注意：该词表数据授权为 CC BY-SA 3.0，请勿提交进 Git 仓库（.gitignore 已排除）。")
    print("重新启动 Web 服务后即可生效。")
    return 0


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="生成更完整的西汉词表（可选步骤）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--from-llm", action="store_true",
                      help="用你自己配置的大模型为字幕里的生词补中文释义")
    mode.add_argument("--from-kaikki", action="store_true",
                      help="流式处理 kaikki.org 英文词典 dump，按义项对齐生成西→中词表")

    # --from-llm 的参数
    parser.add_argument("--srt", help="（llm）本地字幕文件，作为词汇来源")
    parser.add_argument("--url", help="（llm）YouTube 链接，作为词汇来源")
    parser.add_argument("--limit", type=int, default=300,
                        help="（llm）最多补全多少个词（默认 300，最常用的优先）")
    parser.add_argument("--batch-size", type=int, default=40,
                        help="（llm）每次请求携带的词数（默认 40）")
    parser.add_argument("--timeout", type=int, default=120,
                        help="（llm）单次请求超时秒数（默认 120）")
    parser.add_argument("--llm-base-url", default="",
                        help="（llm）覆盖环境变量 LLM_BASE_URL")
    parser.add_argument("--llm-api-key", default="",
                        help="（llm）覆盖环境变量 LLM_API_KEY（不建议在命令行明文传入）")
    parser.add_argument("--llm-model", default="",
                        help="（llm）覆盖环境变量 LLM_MODEL")

    # --from-kaikki 的参数
    parser.add_argument("--input", help="（kaikki）使用已下载好的本地 JSONL 文件，而不是联网下载")
    parser.add_argument("--max-bytes", type=int, default=0,
                        help="（kaikki）只处理前 N 字节，用于小规模试跑（默认 0 = 全量）")

    args = parser.parse_args(argv)

    if args.from_llm:
        return run_llm_mode(args)
    return run_kaikki_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
