"""CEFR 等级判定与等级画像测试。"""

from __future__ import annotations

import pytest

from core.level import (
    CEFR_LEVELS,
    LEVEL_BEYOND,
    LevelResolver,
    get_profile,
    level_sort_key,
    pick_elelex_level,
    zipf_to_cefr,
)


class TestZipfMapping:
    @pytest.mark.parametrize(
        "zipf,expected",
        [(6.5, "A1"), (5.5, "A2"), (4.5, "B1"), (3.6, "B2"), (3.0, "C1"), (2.0, "C2")],
    )
    def test_bands(self, zipf, expected):
        assert zipf_to_cefr(zipf) == expected

    @pytest.mark.parametrize("raw,expected", [(-1, None), (0, None)])
    def test_non_positive_is_unknown(self, raw, expected):
        assert zipf_to_cefr(raw) is expected

    def test_extremely_common_clamps_to_a1(self):
        assert zipf_to_cefr(9.0) == "A1"

    def test_extremely_rare_is_c2(self):
        assert zipf_to_cefr(0.5) == "C2"


class TestPickElelexLevel:
    def test_pick_first_acquired(self):
        # A1 频率最高 → A1（注意：ELELex_LEVEL_ORDER 使用大写等级作为键）
        assert pick_elelex_level({"A1": 100.0, "A2": 20.0, "B1": 5.0}) == "A1"

    def test_skips_rare_low_levels(self):
        # A1 几乎没有、A2 明显 → A2
        assert pick_elelex_level({"A1": 0.1, "A2": 80.0, "B1": 10.0}) == "A2"

    def test_all_below_absolute_floor(self):
        assert pick_elelex_level({"A1": 0.2, "A2": 0.2, "B1": 0.3}) is None

    def test_empty_and_nonpositive(self):
        assert pick_elelex_level({}) is None
        assert pick_elelex_level({"A1": 0.0}) is None


class TestProfiles:
    @pytest.mark.parametrize("level", list(CEFR_LEVELS))
    def test_all_defined(self, level):
        profile = get_profile(level)
        assert profile.level == level
        assert profile.band[0] == level
        assert profile.max_vocab > 0
        assert profile.notes_depth in ("basic", "standard", "nuanced")

    def test_strictness_ordering(self):
        # 等级越高，允许列出的词汇越多
        counts = [get_profile(lv).max_vocab for lv in CEFR_LEVELS]
        assert counts == sorted(counts)

    def test_unknown_level_raises(self):
        with pytest.raises(Exception):
            get_profile("Z9")

    def test_sort_key(self):
        keys = [level_sort_key(lv) for lv in CEFR_LEVELS]
        assert keys == sorted(keys)
        assert level_sort_key(LEVEL_BEYOND) > level_sort_key("C2")


class TestResolver:
    @pytest.fixture(scope="class")
    def resolver(self):
        return LevelResolver()

    def test_elelex_available(self, resolver):
        assert resolver.elelex_available
        assert resolver.elelex_entries > 1000

    def test_common_word_is_early_level(self, resolver):
        estimate = resolver.resolve("casa")
        assert estimate.level in ("A1", "A2")
        assert estimate.source == "elelex"

    def test_rare_word_falls_back_to_wordfreq(self, resolver):
        estimate = resolver.resolve("desafortunadamente")
        assert estimate.level != LEVEL_BEYOND
        assert estimate.source in ("elelex", "wordfreq")

    def test_unknown_garbage(self, resolver):
        estimate = resolver.resolve("qqqqzzzz")
        assert estimate.level == LEVEL_BEYOND
        assert estimate.source == "unknown"

    def test_covers_a1_to_c1_only(self, resolver):
        assert resolver.elelex_covers == ("A1", "C1") or resolver.elelex_covers == "A1–C1"
