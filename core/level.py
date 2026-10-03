"""CEFR 等级判定与「等级画像」。

对应需求「让用户可切换 A1–C2，词汇筛选和讲解深度随等级调整」。

**两个数据源，按优先级聚合：**

1. **ELELex（权威）** —— UCLouvain · CENTAL 的 CEFRLex 项目成果，覆盖 A1–C1，
   是真正按 CEFR 分级标注的西班牙语词汇表。许可证 CC BY-NC-SA 4.0（非商业），
   详见 ``data/third_party/elelex/NOTICE.md``。
2. **wordfreq（兜底）** —— 用 Zipf 词频映射到近似档位。ELELex **没有 C2**，
   所以 C2 以及未被 ELELex 收录的词都走这条路。

> 重要：wordfreq 档位是**频率近似**，不等同于权威 CEFR 标注；界面上会按来源区分显示。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .lemmatizer import normalize_token

CEFR_LEVELS: Tuple[str, ...] = ("A1", "A2", "B1", "B2", "C1", "C2")

# 超出 C2 的词（未被任何词表收录的专名/生僻词）
LEVEL_BEYOND = "X"

# Zipf 词频 → 近似 CEFR 档位。阈值参考 SUBTLEX 的 Zipf 量纲（0–8）。
ZIPF_BANDS: Tuple[Tuple[float, str], ...] = (
    (6.0, "A1"),
    (5.0, "A2"),
    (4.0, "B1"),
    (3.3, "B2"),
    (2.7, "C1"),
    (0.0, "C2"),
)

# --- ELELex 等级推导参数 --------------------------------------------------
#
# ELELex 给出的是「同一个词在 A1 / A2 / B1 / B2 / C1 各级语料中的归一化频率」，
# 而不是一个现成的等级标签，所以需要自己推导。
#
# 直观的做法「取频率最大的那一档」是**错的**：像 `agua`、`tiempo`、`casa` 这类
# 高频词在每个等级都出现得很频繁（agua: a1=372, a2=381, b1=342, b2=451, c1=402），
# 取最大值等于让噪声决定等级。
#
# 改用「**最早被习得**」的语义：从 A1 开始往上扫，取**第一个**频率达到
# `RELATIVE_THRESHOLD × 最高频率` 的等级。这样「处处高频」的词会落到 A1，
# 而只在高级语料里才频繁的词（如 `vocabulario`、`atardecer`）才会落到 C1 / B2。
ELELEX_LEVEL_ORDER: Tuple[str, ...] = ("A1", "A2", "B1", "B2", "C1")
ELELEX_RELATIVE_THRESHOLD = 0.6
# 绝对频率下限，用于挡掉只在极少文档中出现的极小值（0.0x 量级）造成误判
ELELEX_MIN_ABS_FREQ = 0.5

ELELEX_DEFAULT_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "third_party" / "elelex" / "ELELex.tsv"
)


def zipf_to_cefr(zipf: float) -> Optional[str]:
    """把 Zipf 词频映射为近似 CEFR 档位；``<= 0`` 表示未被词频表收录。"""
    if zipf is None or zipf <= 0:
        return None
    for threshold, level in ZIPF_BANDS:
        if zipf >= threshold:
            return level
    return "C2"


@dataclass
class LevelEstimate:
    """单个词的等级判定结果。"""

    level: str
    source: str  # "elelex" | "wordfreq" | "unknown"
    zipf: float = 0.0
    note: str = ""

    @property
    def is_authoritative(self) -> bool:
        return self.source == "elelex"


def _elelex_word_to_key(word: str) -> str:
    """ELELex 用下划线连接多词条（`a_el_aire_libre`），转成空格形式便于展示与查表。"""
    return normalize_token((word or "").replace("_", " "))


def pick_elelex_level(level_freqs: Dict[str, float]) -> Optional[str]:
    """从 ELELex 的「等级 → 归一化频率」分布推导出单一等级。

    规则：从 A1 往上扫，返回**第一个**频率 ≥ ``ELELEX_RELATIVE_THRESHOLD × 最高频率``
    且 ≥ ``ELELEX_MIN_ABS_FREQ`` 的等级（即「最早被习得」的等级）。
    全部不满足时返回 ``None``（该词不在 ELELex 的可靠覆盖范围内）。
    """
    if not level_freqs:
        return None
    max_freq = max(level_freqs.values())
    if max_freq <= 0:
        return None
    threshold = max_freq * ELELEX_RELATIVE_THRESHOLD
    for level in ELELEX_LEVEL_ORDER:
        value = level_freqs.get(level, 0.0)
        if value >= threshold and value >= ELELEX_MIN_ABS_FREQ:
            return level
    return None


def load_elelex_levels(path: str | Path | None = None) -> Tuple[Dict[str, str], List[str]]:
    """读取 ELELex.tsv，返回 ``(lemma → level, warnings)``。

    ELELex 中同一个词可能出现多行（不同词性，例如 `casa` 同时有 NCF 与 NCM），
    因此先按词聚合：每一档取该词所有行里的**最大**频率，再交给
    :func:`pick_elelex_level` 推导等级。直接逐行覆盖会让后一行把前一行冲掉。

    文件缺失或格式异常时不抛错，而是返回空表 + 警告——工具必须能在没有该数据时继续工作。
    """
    warnings: List[str] = []
    target = Path(path) if path else ELELEX_DEFAULT_PATH

    if not target.exists():
        warnings.append(
            f"未找到 ELELex 数据文件（{target}），等级判定将只使用 wordfreq 词频近似。"
        )
        return {}, warnings

    try:
        raw = target.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        warnings.append(f"读取 ELELex 数据失败：{exc}")
        return {}, warnings

    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        warnings.append("ELELex 数据文件为空。")
        return {}, warnings

    header = [cell.strip().strip('"').lower() for cell in lines[0].split("\t")]
    if "word" not in header:
        warnings.append("ELELex 数据格式与预期不符（缺少 word 列），已跳过。")
        return {}, warnings
    word_idx = header.index("word")

    level_columns: List[Tuple[int, str]] = []
    for level in ELELEX_LEVEL_ORDER:
        column = f"level_freq@{level.lower()}"
        if column in header:
            level_columns.append((header.index(column), level))
    if not level_columns:
        warnings.append("ELELex 数据格式与预期不符（缺少 level_freq 列），已跳过。")
        return {}, warnings

    # 先按词聚合每档的最大频率（同一个词可能有多行不同词性）
    aggregated: Dict[str, Dict[str, float]] = {}
    for line in lines[1:]:
        cells = line.split("\t")
        if len(cells) <= word_idx:
            continue
        key = _elelex_word_to_key(cells[word_idx].strip().strip('"'))
        if not key:
            continue
        bucket = aggregated.setdefault(key, {})
        for index, level in level_columns:
            if index >= len(cells):
                continue
            try:
                value = float(cells[index].strip().strip('"'))
            except ValueError:
                continue
            if value > bucket.get(level, 0.0):
                bucket[level] = value

    levels: Dict[str, str] = {}
    for key, freqs in aggregated.items():
        level = pick_elelex_level(freqs)
        if level:
            levels[key] = level

    if not levels:
        warnings.append("ELELex 数据中没有解析到有效的等级条目。")
    return levels, warnings


class LevelResolver:
    """把「词」解析成 CEFR 等级，并暴露数据源状态供界面展示。"""

    def __init__(
        self,
        *,
        elelex_path: str | Path | None = None,
        scorer: Optional[Callable[[str], float]] = None,
        use_elelex: bool = True,
    ) -> None:
        self._scorer = scorer
        self._elelex: Dict[str, str] = {}
        self.warnings: List[str] = []

        if use_elelex:
            self._elelex, self.warnings = load_elelex_levels(elelex_path)

    # -- 状态 ---------------------------------------------------------------

    @property
    def elelex_available(self) -> bool:
        return bool(self._elelex)

    @property
    def elelex_entries(self) -> int:
        return len(self._elelex)

    @property
    def elelex_covers(self) -> str:
        return "A1–C1" if self._elelex else "不可用"

    # -- 打分 ---------------------------------------------------------------

    def zipf(self, word: str) -> float:
        if self._scorer is not None:
            try:
                return float(self._scorer(word))
            except Exception:
                return 0.0
        try:
            from wordfreq import zipf_frequency
        except Exception:  # pragma: no cover - wordfreq 缺失时退化为未知
            return 0.0
        return float(zipf_frequency(word, "es"))

    def resolve(self, lemma: str) -> LevelEstimate:
        """先查 ELELex，未命中再用 wordfreq 词频近似。"""
        key = normalize_token(lemma)
        if not key:
            return LevelEstimate(level=LEVEL_BEYOND, source="unknown")

        if key in self._elelex:
            return LevelEstimate(
                level=self._elelex[key], source="elelex", note="ELELex 等级标注"
            )

        # 多词条：ELELex 里可能以空格形式存在
        zipf = self.zipf(key)
        approx = zipf_to_cefr(zipf)
        if approx is None:
            return LevelEstimate(level=LEVEL_BEYOND, source="unknown", zipf=0.0,
                                 note="未被任何词表收录（可能是专有名词或极生僻词）")
        return LevelEstimate(
            level=approx, source="wordfreq", zipf=round(zipf, 2),
            note="按词频近似（ELELex 未收录）",
        )


# --------------------------------------------------------------------------
# 等级画像：控制「筛哪些词」与「讲多深」
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LevelProfile:
    """某个等级的学习者画像。"""

    level: str
    band: Tuple[str, ...]          # 要筛选出来的词等级区间（含超纲档 X）
    max_vocab: int                 # 词表最多展示多少条
    notes_depth: str               # basic | standard | nuanced
    label: str                     # 面向界面的中文说明

    def matches(self, level: str) -> bool:
        return level in self.band


PROFILES: Dict[str, LevelProfile] = {
    "A1": LevelProfile(
        level="A1",
        band=("A1", "A2"),
        max_vocab=15,
        notes_depth="basic",
        label="入门：只挑最基础的高频词，给出词性与阴阳性，句子给逐词直译。",
    ),
    "A2": LevelProfile(
        level="A2",
        band=("A2", "B1"),
        max_vocab=20,
        notes_depth="basic",
        label="初级：开始出现常用搭配与简单时态说明。",
    ),
    "B1": LevelProfile(
        level="B1",
        band=("B1", "B2"),
        max_vocab=25,
        notes_depth="standard",
        label="中级：补充固定搭配与介词用法，讲解时态变化。",
    ),
    "B2": LevelProfile(
        level="B2",
        band=("B2", "C1"),
        max_vocab=30,
        notes_depth="standard",
        label="中高级：强调搭配、语域与近义辨析。",
    ),
    "C1": LevelProfile(
        level="C1",
        band=("C1", "C2"),
        max_vocab=40,
        notes_depth="nuanced",
        label="高级：关注习语、隐含语气与文体差异。",
    ),
    "C2": LevelProfile(
        level="C2",
        band=("C2", LEVEL_BEYOND),
        max_vocab=50,
        notes_depth="nuanced",
        label="精通：只列出最生僻/超出 C2 的词，给出语域与修辞提示。",
    ),
}


def get_profile(level: str) -> LevelProfile:
    """按等级取画像；等级非法时抛 ``ValueError``。"""
    key = (level or "").strip().upper()
    if key not in PROFILES:
        raise ValueError(
            f"不支持的等级 {level!r}，可选值：{', '.join(CEFR_LEVELS)}。"
        )
    return PROFILES[key]


def level_sort_key(level: str) -> int:
    """等级排序用的序号（A1=0 … C2=5，超纲=6）。"""
    if level in CEFR_LEVELS:
        return CEFR_LEVELS.index(level)
    return len(CEFR_LEVELS)
