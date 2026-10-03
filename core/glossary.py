"""西汉词表加载与查询。

三个数据源，按优先级**合并**（同一词以高优先级来源为准，低优先级来源补缺）：

1. ``data/llm_glossary.jsonl.gz`` —— 由 ``tools/build_glossary.py --from-llm``
   用用户自己配置的大模型生成的西汉词表（**不入库**，属于用户自己的产物）。
2. ``data/es_zh_glossary.jsonl.gz`` —— 由 ``tools/build_glossary.py --from-kaikki``
   从 Wiktionary（经由 kaikki.org 的英文词典 dump 按义项对齐）生成的西汉词表。
   **默认不入库**（体积较大且授权为 CC BY-SA 3.0），需要用户自行运行脚本生成。
3. ``data/core_glossary.json`` —— 本项目自撰的精编高频西汉词表（MIT），
   保证仓库 clone 下来、不联网、不运行任何脚本也能看到中文释义。

> 说明：kaikki.org 的「西班牙语词典」**不含**跨语言翻译字段（英文维基词典只有
> 英语词条页带 Translations 区块），因此西→中词表必须从英文词典 dump
> 按义项对齐得到，详见 ``tools/build_glossary.py`` 的文档字符串。

未收录的词会返回空列表，由上层标注为「未收录（可接入 LLM 补全）」。
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .lemmatizer import normalize_token, strip_accents

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

LLM_GLOSSARY_PATH = DATA_DIR / "llm_glossary.jsonl.gz"
FULL_GLOSSARY_PATH = DATA_DIR / "es_zh_glossary.jsonl.gz"
CORE_GLOSSARY_PATH = DATA_DIR / "core_glossary.json"

# 来源显示名（供界面使用）
SOURCE_LABELS = {
    "llm": "大模型补全",
    "wiktionary": "Wiktionary",
    "core": "内置精编",
}


@dataclass
class Sense:
    """一个词的中文义项。"""

    meaning: str
    pos: str = ""
    source: str = "core"
    tags: List[str] = field(default_factory=list)


class Glossary:
    """西汉词表。懒加载，线程内只读。"""

    def __init__(
        self,
        *,
        llm_path: Optional[Path] = None,
        full_path: Optional[Path] = None,
        core_path: Optional[Path] = None,
    ) -> None:
        self._llm_path = Path(llm_path) if llm_path else LLM_GLOSSARY_PATH
        self._full_path = Path(full_path) if full_path else FULL_GLOSSARY_PATH
        self._core_path = Path(core_path) if core_path else CORE_GLOSSARY_PATH
        self._index: Dict[str, List[Sense]] = {}
        self._compact: Optional[Dict[str, List[Sense]]] = None
        self._loaded = False
        self.source: str = "none"
        # 每个实际加载了的来源及其新增词条数（供界面/健康检查展示）
        self.sources_loaded: List[Dict[str, object]] = []
        self.warnings: List[str] = []

    # -- 加载 ---------------------------------------------------------------

    def load(self) -> "Glossary":
        """按优先级加载所有可用来源并合并。同一词先到先得，后来的只补缺。"""
        if self._loaded:
            return self

        plan = [
            ("llm", self._llm_path, self._load_jsonl_gz),
            ("wiktionary", self._full_path, self._load_jsonl_gz),
            ("core", self._core_path, self._load_core_json),
        ]

        for name, path, loader in plan:
            if not path.exists():
                continue
            try:
                added = loader(path)
            except Exception as exc:  # 损坏的文件不应让整个工具崩掉
                self.warnings.append(f"读取 {path.name} 失败：{exc}")
                continue
            if added:
                self.sources_loaded.append({"name": name, "entries": added})
            else:
                self.warnings.append(f"{path.name} 中没有解析到有效词条。")

        if self.sources_loaded:
            self.source = str(self.sources_loaded[0]["name"])
        else:
            self.source = "none"
            self.warnings.append(f"未找到任何词表文件（期望位于 {self._core_path}）。")
        self._loaded = True
        return self

    def _load_jsonl_gz(self, path: Path) -> int:
        """加载 gzip 压缩的 JSONL 词表；已存在的词跳过（合并语义）。"""
        count = 0
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                word = row.get("w") or row.get("word") or ""
                meanings = row.get("zh") or row.get("meanings") or []
                if isinstance(meanings, str):
                    meanings = [meanings]
                key = normalize_token(word)
                if not key or key in self._index:
                    continue
                senses = [
                    Sense(
                        meaning=str(m).strip(),
                        pos=str(row.get("p", "")),
                        source=str(row.get("src", "generated")),
                        tags=list(row.get("tags", []) or []),
                    )
                    for m in meanings
                    if str(m).strip()
                ]
                if senses:
                    self._index[key] = senses
                    count += 1
        return count

    def _load_core_json(self, path: Path) -> int:
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload.get("entries", payload)
        count = 0
        for word, value in entries.items():
            key = normalize_token(word)
            if not key or key in self._index:
                continue
            if isinstance(value, dict):
                meanings = value.get("zh") or value.get("meanings") or []
                pos = value.get("pos", "")
                tags = value.get("tags", []) or []
            elif isinstance(value, list):
                meanings, pos, tags = value, "", []
            else:
                meanings, pos, tags = [value], "", []
            if isinstance(meanings, str):
                meanings = [meanings]
            senses = [
                Sense(meaning=str(m).strip(), pos=pos, source="core", tags=list(tags))
                for m in meanings
                if str(m).strip()
            ]
            if senses:
                self._index[key] = senses
                count += 1
        return count

    # -- 查询 ---------------------------------------------------------------

    @property
    def available(self) -> bool:
        self.load()
        return bool(self._index)

    @property
    def entries(self) -> int:
        self.load()
        return len(self._index)

    def _compact_index(self) -> Dict[str, List[Sense]]:
        """懒构建的「去空格」二级索引：把多词短语的 O(n) 扫描变成 O(1)。"""
        if self._compact is None:
            compact: Dict[str, List[Sense]] = {}
            for key, senses in self._index.items():
                if " " in key:
                    compact.setdefault(key.replace(" ", ""), senses)
            self._compact = compact
        return self._compact

    def lookup(self, lemma: str, *, limit: int = 3) -> List[Sense]:
        """查询中文义项；未收录返回空列表。"""
        self.load()
        key = normalize_token(lemma)
        if not key:
            return []
        senses = self._index.get(key)
        if senses:
            return senses[:limit]

        # 去掉空格再试一次（多词条目可能写作 "a el aire libre" 这类形式）
        if " " in key:
            return []
        return self._compact_index().get(key, [])[:limit]

    def has(self, lemma: str) -> bool:
        return bool(self.lookup(lemma, limit=1))


def guess_pos_label(pos: str) -> str:
    """把词性缩写转成中文标签（供界面显示）。"""
    key = (pos or "").strip().lower().rstrip(".")
    mapping = {
        "n": "名词", "nf": "名词(阴)", "nm": "名词(阳)", "nc": "名词",
        "v": "动词", "verb": "动词", "adj": "形容词", "a": "形容词",
        "adv": "副词", "rg": "副词", "prep": "介词", "sp": "介词",
        "conj": "连词", "pron": "代词", "pp": "代词", "det": "限定词",
        "da": "冠词", "art": "冠词", "interj": "感叹词", "i": "感叹词",
        "num": "数词", "noun": "名词",
    }
    return mapping.get(key, pos or "")


def iter_words(senses: Iterable[Sense]) -> str:
    """把义项拼成一行中文，供快速展示。"""
    return "；".join(s.meaning for s in senses)
