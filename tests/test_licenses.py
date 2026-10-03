"""许可与仓库卫生守护测试。

这些测试的作用是「防止将来不小心弄坏合规性」：

1. ELELex 第三方数据必须原样保留，且 NOTICE 的校验值与文件一致；
2. 生成的词表（wiktionary / llm）不得提交进仓库；
3. 源码里不得出现密钥或 ``.env`` 文件。
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ELELEX_DIR = PROJECT_ROOT / "data" / "third_party" / "elelex"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class TestElelex:
    def test_data_file_present(self):
        tsv = ELELEX_DIR / "ELELex.tsv"
        assert tsv.exists(), "ELELex.tsv 不见了，等级判定会退化成纯词频近似"
        assert tsv.stat().st_size > 1_000_000

    def test_sha_matches_notice(self):
        tsv = ELELEX_DIR / "ELELex.tsv"
        notice = (ELELEX_DIR / "NOTICE.md").read_text(encoding="utf-8")
        actual = _sha256(tsv)
        assert actual in notice, "NOTICE.md 里的校验值与实际文件不一致，数据可能被改动过"

    def test_sha_matches_upstream_record(self):
        from tools.update_elelex import KNOWN_SHA256

        assert _sha256(ELELEX_DIR / "ELELex.tsv") == KNOWN_SHA256

    def test_license_text_present(self):
        license_text = (ELELEX_DIR / "LICENSE").read_text(encoding="utf-8")
        assert "Attribution-NonCommercial-ShareAlike" in license_text
        assert "4.0" in license_text

    def test_notice_has_attribution_and_no_modification(self):
        notice = (ELELEX_DIR / "NOTICE.md").read_text(encoding="utf-8")
        for needle in ("CENTAL", "UCLouvain", "CEFRLex", "ELELex", "cental.uclouvain.be"):
            assert needle in notice, f"NOTICE.md 缺少署名要素：{needle}"
        assert "未修改" in notice or "unmodified" in notice.lower(), "必须声明未做任何修改（聚合）"
        assert "非商业" in notice or "NonCommercial" in notice

    def test_data_untouched_in_place(self):
        """聚合豁免的前提是「原样保留」，这里检查表头没有被改写。"""
        header = (ELELEX_DIR / "ELELex.tsv").read_text(encoding="utf-8", errors="strict")[:400]
        assert "word" in header and "level_freq@a1" in header


class TestGeneratedGlossariesNotCommitted:
    def test_no_wiktionary_glossary(self):
        assert not (PROJECT_ROOT / "data" / "es_zh_glossary.jsonl.gz").exists(), (
            "es_zh_glossary.jsonl.gz 是 CC BY-SA 3.0 数据，请勿提交进仓库"
        )

    def test_no_llm_glossary(self):
        assert not (PROJECT_ROOT / "data" / "llm_glossary.jsonl.gz").exists(), (
            "llm_glossary.jsonl.gz 是用户本地生成的产物，请勿提交进仓库"
        )

    def test_gitignore_covers_generated_files(self):
        gitignore = PROJECT_ROOT / ".gitignore"
        if not gitignore.exists():  # 尚未初始化 git 时跳过
            pytest.skip(".gitignore 尚未创建")
        text = gitignore.read_text(encoding="utf-8")
        for needle in ("es_zh_glossary", "llm_glossary", ".venv", ".env"):
            assert needle in text, f".gitignore 缺少 {needle}"


class TestNoSecrets:
    def test_no_env_file(self):
        assert not (PROJECT_ROOT / ".env").exists(), (
            "检测到 .env 文件，请确认它已被 .gitignore 排除，且里面只有本地测试配置"
        )

    def test_source_has_no_hardcoded_key(self):
        patterns = ("LLM_API_KEY=", "api_key=", "Authorization: Bearer ")
        suspicious = []
        for path in PROJECT_ROOT.rglob("*.py"):
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            # 测试文件里的断言字符串本身会命中模式，跳过；虚拟环境与缓存不在仓库里
            if rel.startswith((".venv", "tools/_cache", "tests")):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                stripped = line.strip()
                if not any(p in stripped for p in patterns):
                    continue
                # 允许的是：读环境变量 / 传 None / 写文档说明
                after = stripped.split("=", 1)[-1].strip()
                if after in ('""', "''", "None", ""):
                    continue
                if "os.environ" in stripped or "getenv" in stripped:
                    continue
                if "LLM_API_KEY" in stripped and ("env" in stripped or "覆盖" in stripped):
                    continue
                if stripped.startswith("#") or "环境变量" in stripped:
                    continue
                suspicious.append(f"{rel}: {stripped[:90]}")
        assert not suspicious, "源码里疑似硬编码了密钥：\n" + "\n".join(suspicious)

    def test_source_has_no_real_api_key_pattern(self):
        key_re = re.compile(r"sk-[A-Za-z0-9]{20,}")
        offenders = []
        for pattern in ("*.py", "*.js", "*.html", "*.css", "*.md", "*.txt"):
            for path in PROJECT_ROOT.rglob(pattern):
                rel = path.relative_to(PROJECT_ROOT).as_posix()
                if rel.startswith((".venv", "tools/_cache", "data/third_party", "tests")):
                    continue
                if key_re.search(path.read_text(encoding="utf-8", errors="replace")):
                    offenders.append(rel)
        assert not offenders, "源码里发现疑似真实密钥：" + ", ".join(offenders)
