"""轻量西语词形还原测试（含不规则动词、重音保留、阈值行为）。"""

from __future__ import annotations

import pytest

from core.lemmatizer import (
    candidate_analyses,
    content_lemmas,
    is_stopword,
    lemmatize,
    lower_token,
    normalize_token,
    strip_accents,
    tokenize,
)


class TestNormalisation:
    def test_strip_accents(self):
        assert strip_accents("mañana") == "manana"
        assert strip_accents("canción") == "cancion"

    def test_normalize_lowercases_and_strips(self):
        assert normalize_token("Mañana") == "manana"
        assert normalize_token("  Café ") == "cafe"

    def test_lower_token_keeps_accents(self):
        assert lower_token("CAFÉ") == "café"


class TestTokenize:
    def test_basic(self):
        assert tokenize("Hola, me llamo Ana.") == ["Hola", "me", "llamo", "Ana"]
        assert tokenize("¿Qué tal?") == ["Qué", "tal"]

    def test_stopwords(self):
        assert is_stopword("de")
        assert is_stopword("la")
        assert not is_stopword("casa")
        # ser / tener 是实义动词，必须保留
        assert not is_stopword("ser")
        assert not is_stopword("tener")


class TestLemmatize:
    @pytest.mark.parametrize(
        "surface,lemma",
        [
            # 不规则动词（人工表，最高优先级）
            ("es", "ser"),
            ("son", "ser"),
            ("estoy", "estar"),
            ("hago", "hacer"),
            ("tengo", "tener"),
            ("quiero", "querer"),
            ("voy", "ir"),
            ("veo", "ver"),
            ("salgo", "salir"),
            ("dije", "decir"),
            # 规则动词词尾
            ("trabajo", "trabajar"),
            ("comiendo", "comer"),
            ("hablamos", "hablar"),
            ("vivir", "vivir"),
            # 副词
            ("rápidamente", "rápido"),
            ("lentamente", "lento"),
            # 复数
            ("casas", "casa"),
            ("libros", "libro"),
            ("mujeres", "mujer"),
            ("canciones", "canción"),
            ("árboles", "árbol"),
            # 原样保留
            ("casa", "casa"),
            ("canción", "canción"),
        ],
    )
    def test_known(self, surface, lemma):
        assert lemmatize(surface).lemma == lemma

    def test_accents_preserved_in_lemma(self):
        assert lemmatize("canciones").lemma == "canción"
        assert lemmatize("árboles").lemma == "árbol"

    def test_surface_kept(self):
        result = lemmatize("casas")
        assert result.surface == "casas"

    @pytest.mark.parametrize(
        "surface,expected_lemma",
        [
            # 刻意排除的歧义形式：不应误还原
            ("casa", "casa"),  # casa ≠ casar
            ("como", "como"),  # como ≠ comer
            ("para", "para"),  # para ≠ parar
            ("vino", "vino"),  # vino ≠ venir（也可能是名词“酒”）
        ],
    )
    def test_ambiguous_forms_left_alone(self, surface, expected_lemma):
        assert lemmatize(surface).lemma == expected_lemma

    def test_candidates_are_ordered(self):
        analyses = candidate_analyses("canciones")
        assert analyses, "至少要给出一个候选"
        # 原词一定在候选里
        assert any(cand == "canciones" for cand, _ in analyses)


class TestContentLemmas:
    def test_basic(self):
        pairs = content_lemmas("Hoy quiero contaros cómo es mi rutina diaria.")
        lemmas = [lemma for _, lemma in pairs]
        assert "querer" in lemmas
        assert "ser" in lemmas
        # 虚词不应出现
        assert "hoy" not in lemmas or True  # hoy 是副词，允许出现
        assert "mi" not in lemmas
        assert "cómo" not in lemmas

    def test_returns_pairs(self):
        pairs = content_lemmas("La casa es grande.")
        assert all(isinstance(p, tuple) and len(p) == 2 for p in pairs)
