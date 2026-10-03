#!/usr/bin/env python3
"""刷新 ELELex 原始数据文件（保持原样，不做任何格式转换）。

ELELex 是 UCLouvain · CENTAL 的 CEFRLex 项目成果，采用 **CC BY-NC-SA 4.0（非商业）**
许可证。本脚本只负责把**未经修改的原始文件**下载到 `data/third_party/elelex/`，
并在结束时打印署名与许可证提醒。

> 注意：因为只做原样收录（聚合），项目的 MIT 代码不会被 ShareAlike 传染；
> 但你**不得**把该数据用于商业目的，且必须保留 `NOTICE.md` 中的署名。

用法::

    python tools/update_elelex.py                # 下载并写入
    python tools/update_elelex.py --check        # 只比对当前文件的校验值
    python tools/update_elelex.py --base-url URL # 使用镜像源
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGET_DIR = PROJECT_ROOT / "data" / "third_party" / "elelex"
TARGET_FILE = TARGET_DIR / "ELELex.tsv"

DEFAULT_URL = "https://cental.uclouvain.be/cefrlex/static/resources/es/ELELex.tsv"

# 入库时的校验值（见 NOTICE.md）。上游更新后可能变化，因此仅作提醒，不强制失败。
KNOWN_SHA256 = "87a28dc6d3c5c2344883698f7bc77e259bd42212446ac2b52761a3fc8f5f26cf"

LICENSE_BANNER = """
------------------------------------------------------------------------------
ELELex —— UCLouvain · CENTAL · CEFRLex 项目
许可证：CC BY-NC-SA 4.0（署名—非商业性使用—相同方式共享）
  * 必须保留署名：CENTAL / UCLouvain，CEFRLex 项目，ELELex
  * 不得用于商业目的（含提取、复用本数据库的实质性内容）
  * 如做改编，改编成果须继续以 CC BY-NC-SA 4.0 授权并注明修改
许可证全文：https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode
资源页面：https://cental.uclouvain.be/cefrlex/elelex/
------------------------------------------------------------------------------
"""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "es-yt-learner/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def check() -> int:
    if not TARGET_FILE.exists():
        print(f"[缺失] {TARGET_FILE} 不存在。")
        return 1
    digest = sha256_of(TARGET_FILE)
    size = TARGET_FILE.stat().st_size
    print(f"文件：{TARGET_FILE}")
    print(f"大小：{size:,} 字节")
    print(f"sha256：{digest}")
    if digest == KNOWN_SHA256:
        print("[一致] 与入库时的校验值相同（文件未被修改）。")
    else:
        print("[注意] 与入库时的校验值不同：可能上游已更新，或本地文件被改动。")
        print(f"       入库时的值：{KNOWN_SHA256}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="刷新 ELELex 原始数据文件")
    parser.add_argument("--url", default=DEFAULT_URL, help="下载地址")
    parser.add_argument("--base-url", help="用镜像源替换默认域名（拼接同路径）")
    parser.add_argument("--check", action="store_true", help="只校验当前文件，不下载")
    parser.add_argument("--force", action="store_true", help="即使校验值相同也重写")
    args = parser.parse_args(argv)

    if args.check:
        return check()

    url = args.url
    if args.base_url:
        url = args.base_url.rstrip("/") + "/cefrlex/static/resources/es/ELELex.tsv"

    print(f"正在下载：{url}")
    try:
        payload = download(url)
    except urllib.error.HTTPError as exc:
        print(f"[失败] HTTP {exc.code}：{exc.reason}")
        print("       请检查网络，或用 --base-url 指定可访问的镜像。")
        return 2
    except Exception as exc:  # noqa: BLE001 - 面向使用者的统一错误提示
        print(f"[失败] 下载出错：{exc}")
        return 2

    if not payload.strip():
        print("[失败] 下载到的内容为空。")
        return 2

    digest = hashlib.sha256(payload).hexdigest()
    if TARGET_FILE.exists() and not args.force:
        current = sha256_of(TARGET_FILE)
        if current == digest:
            print("[跳过] 本地文件已是新版，无需更新。")
            print(LICENSE_BANNER)
            return 0
        print("[更新] 上游内容与本地不同，将覆盖本地文件。")

    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    TARGET_FILE.write_bytes(payload)

    lines = payload.count(b"\n") + (0 if payload.endswith(b"\n") else 1)
    print(f"[完成] 已写入 {TARGET_FILE}")
    print(f"       大小 {len(payload):,} 字节，{lines:,} 行")
    print(f"       sha256 {digest}")
    if digest != KNOWN_SHA256:
        print("       [注意] 与入库时的校验值不同，请同步更新 NOTICE.md 中的校验值。")
    print(LICENSE_BANNER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
