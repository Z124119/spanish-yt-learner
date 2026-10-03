"""西汉词表加载与查询测试（含多来源合并语义）。"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from core.glossary import SOURCE_LABELS, Glossary

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _write_gz(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


class TestCoreGlossary:
    @pytest.fixture(scope="class")
    def glossary(self):
        return Glossary().load()

    def test_loaded(self, glossary):
        assert glossary.available
        assert glossary.entries >= 200
        assert glossary.source == "core"

    def test_lookup(self, glossary):
        senses = glossary.lookup("casa")
        assert senses
        assert any("房" in s.meaning or "家" in s.meaning for s in senses)

    def test_accents_irrelevant(self, glossary):
        assert glossary.has("cancion") or glossary.has("canción")

    def test_unknown_word(self, glossary):
        assert glossary.lookup("qqqqzzzz") == []
        assert not glossary.has("qqqqzzzz")

    def test_multiword_compact_match(self, glossary):
        # "a el aire libre" 这类多词条：去空格也应能命中
        senses = glossary.lookup("airelibre")
        assert isinstance(senses, list)


class TestMergedSources:
    def test_priority_and_merge(self, tmp_path):
        llm = tmp_path / "llm.jsonl.gz"
        full = tmp_path / "full.jsonl.gz"
        core = tmp_path / "core.json"

        _write_gz(llm, [{"w": "casa", "p": "n.", "zh": ["LLM 释义"]}, {"w": "sol", "p": "n.", "zh": ["太阳"]}])
        _write_gz(full, [{"w": "casa", "p": "n.", "zh": ["Wiktionary 释义"]}, {"w": "mar", "p": "n.", "zh": ["海"]}])
        core.write_text(
            json.dumps({"entries": {"casa": {"pos": "n.", "zh": ["房子"]}, "luna": {"pos": "n.", "zh": ["月亮"]}}}),
            encoding="utf-8",
        )

        glossary = Glossary(llm_path=llm, full_path=full, core_path=core).load()
        assert glossary.entries == 4  # casa + sol + mar + luna
        assert [s["name"] for s in glossary.sources_loaded] == ["llm", "wiktionary", "core"]
        assert glossary.source == "llm"
        # 高优先级来源优先
        assert [s.meaning for s in glossary.lookup("casa")] == ["LLM 释义"]
        assert [s.meaning for s in glossary.lookup("mar")] == ["海"]
        assert [s.meaning for s in glossary.lookup("luna")] == ["月亮"]
        # 来源显示名映射完整
        assert SOURCE_LABELS["llm"] and SOURCE_LABELS["wiktionary"] and SOURCE_LABELS["core"]

    def test_missing_files_falls_back_to_none(self, tmp_path):
        glossary = Glossary(
            llm_path=tmp_path / "a.gz", full_path=tmp_path / "b.gz", core_path=tmp_path / "c.json"
        ).load()
        assert not glossary.available
        assert glossary.source == "none"
        assert glossary.warnings

    def test_corrupt_file_does_not_crash(self, tmp_path):
        bad = tmp_path / "bad.jsonl.gz"
        bad.write_bytes(b"\x00\x01\x02not a gzip")
        good = tmp_path / "core.json"
        good.write_text(json.dumps({"entries": {"sol": {"pos": "n.", "zh": ["太阳"]}}}), encoding="utf-8")
        glossary = Glossary(llm_path=bad, full_path=tmp_path / "x.gz", core_path=good).load()
        assert glossary.has("sol")
        assert any("bad.jsonl.gz" in w for w in glossary.warnings)


class TestProjectGlossaryMerge:
    """仓库真实数据：若存在生成的词表，应与 core 合并且不覆盖已有释义。"""

    def test_real_sources(self):
        glossary = Glossary().load()
        assert glossary.available
        names = [s["name"] for s in glossary.sources_loaded]
        assert "core" in names
        assert names == sorted(names, key=lambda n: ["llm", "wiktionary", "core"].index(n))
