# 第三方组件与数据声明（THIRD_PARTY_NOTICES）

本项目（西语 YouTube 学习助手）的**源代码**采用 MIT 许可，见 [`LICENSE`](./LICENSE)。
但项目**引用或打包**了下列第三方组件与数据，它们各自适用自己的许可证。
本文件如实说明「哪些是我们写的、哪些是别人的」，请一并阅读。

---

## 1. 打包进仓库的第三方数据

### ELELex —— 西班牙语 CEFR 分级词表

| 项目 | 内容 |
| --- | --- |
| 位置 | `data/third_party/elelex/ELELex.tsv`（**原样收录，未做任何修改**） |
| 出品方 | **CENTAL — Centre for English Corpus Linguistics**，比利时鲁汶天主教大学（**UCLouvain**）；隶属 **CEFRLex** 项目 |
| 主页 | <https://cental.uclouvain.be/cefrlex/> |
| 下载地址 | <https://cental.uclouvain.be/cefrlex/static/resources/es/ELELex.tsv> |
| 规模 | 14,290 条 lemma，覆盖 A1–C1（不含 C2），带 FreeLing 词性标注 |
| 许可证 | **CC BY-NC-SA 4.0**（署名—非商业性使用—相同方式共享） |
| 许可证全文 | [`data/third_party/elelex/LICENSE`](./data/third_party/elelex/LICENSE) |
| 署名与使用声明 | [`data/third_party/elelex/NOTICE.md`](./data/third_party/elelex/NOTICE.md)（含 sha256 校验值） |
| 用途 | 词汇的权威 CEFR 等级判定（A1–C1） |

> **⚠️ 重要：非商业限制**
>
> ELELex 是 **NonCommercial（非商业）** 许可数据：
>
> * 如果你想把本项目**用于商业用途**（例如做成收费产品、在公司内部作为营利服务部署），
>   **必须先删除 `data/third_party/elelex/` 整个目录**。
>   删除后工具仍可运行，但词汇等级会完全依赖 wordfreq 词频近似（精度下降）。
> * CC 的 NC 条款并非禁止一切金钱相关使用，其定义是
>   「并非主要为了商业优势或金钱报酬」。本项目作为个人学习/求职作品属于
>   **非商业用途**；如果拿不准你的场景是否合规，请咨询专业人士。
> * 另需注意：本目录以「**单纯聚合**（mere aggregation）」方式收录原始文件、
>   未做任何修改，因此 CC BY-NC-SA 的 ShareAlike 条款**不传染**到本项目的 MIT 代码。

---

## 2. 运行时依赖的开源库（pip 安装，不打包在仓库里）

| 库 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| [youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api) | ~=1.2.4 | MIT | 获取 YouTube 字幕 |
| [Flask](https://github.com/pallets/flask) | ~=3.1 | BSD-3-Clause | Web 框架与模板 |
| [wordfreq](https://github.com/rspeer/wordfreq) | ~=3.1 | **代码 MIT / 词汇数据 CC BY-SA 4.0** | 西语词频（Zipf）→ CEFR 近似档位 |
| [requests](https://github.com/psf/requests) | （随 youtube-transcript-api 安装） | Apache-2.0 | HTTP 请求 |

> 注：`wordfreq` 的词汇数据以 CC BY-SA 4.0 提供。本项目只在运行时调用它做
> 词频查询，**不把它的数据 redistribute 进仓库**。

---

## 3. 可选生成的数据（默认**不在**仓库里）

| 数据 | 生成方式 | 许可证 | 是否入库 |
| --- | --- | --- | --- |
| `data/es_zh_glossary.jsonl.gz` | `python tools/build_glossary.py --from-kaikki`（从 kaikki.org 英文词典 dump 按义项对齐） | **CC BY-SA 3.0**（源自 Wiktionary 文本） | ❌ 不入库 |
| `data/llm_glossary.jsonl.gz` | `python tools/build_glossary.py --from-llm`（用你自己配置的大模型生成） | 由使用者决定 | ❌ 不入库 |

`--from-kaikki` 产物来源于 [Wiktionary](https://en.wiktionary.org/)（经由
[kaikki.org](https://kaikki.org/) 的机器可读抽取）。Wiktionary 文本采用
**CC BY-SA 3.0**，使用该词表请署名并以相同方式共享衍生数据；
脚本会自动生成 `data/es_zh_glossary.SOURCE.txt` 记录来源与日期。
这两类文件都已在 `.gitignore` 中排除。

---

## 4. 本项目自撰、MIT 许可的内容

* `core/`、`tools/`、`tests/`、`app.py`、`config.py` —— 全部为本项目作者撰写；
* `data/core_glossary.json` —— 自撰精编西汉词表（约 251 条，MIT）；
* `data/demo/demo_es.srt`、`demo_translations.json`、`demo_glossary.json` ——
  自撰教学示例（字幕、人工译文、示例词表），**不包含任何第三方字幕内容**，
  示例中提到的时间点仅用于演示「点击跳转」功能。

---

## 5. 灵感与参考（致谢）

以下同类项目给过我们思路参考，各项目实现相互独立：

* [Tongkai-Z/PodCastVocabExtractor](https://github.com/Tongkai-Z/PodCastVocabExtractor) —— spaCy + wordfreq 做 CEFR 分级词表
* [RizhongLin/PolyglotWhisperer](https://github.com/RizhongLin/PolyglotWhisperer) —— 多语言字幕 → 词汇学习材料
* [ObsidianRelay/lingua-study](https://github.com/ObsidianRelay/lingua-study) —— 字幕导入与词汇整理
* [kaikki.org](https://kaikki.org/) / [wiktextract](https://github.com/tatuylonen/wiktextract) —— 机器可读词典数据基础设施
