# 第三方数据声明 —— ELELex

本目录（`data/third_party/elelex/`）收录的是 **ELELex** 数据文件。
请仔细阅读本声明与同目录的 [`LICENSE`](./LICENSE)（CC BY-NC-SA 4.0 许可证全文）。

> **本目录内容与本项目其余部分采用不同的许可证。本项目代码是 MIT，但本目录不是。**

---

## 1. 署名（Attribution）

| 项目 | 内容 |
| --- | --- |
| 资源名称 | **ELELex** —— 面向西班牙语二语学习者的 CEFR 分级词表（receptive lexicon） |
| 出品方 | **CENTAL — Centre for English Corpus Linguistics / 语言资源中心，比利时鲁汶天主教大学（UCLouvain）** |
| 所属项目 | **CEFRLex** —— 面向二语学习的 CEFR 分级词汇资源项目 |
| 项目主页 | https://cental.uclouvain.be/cefrlex/ |
| 资源页面 | https://cental.uclouvain.be/cefrlex/elelex/ |
| 下载页面 | https://cental.uclouvain.be/cefrlex/elelex/download |
| 下载地址 | `https://cental.uclouvain.be/cefrlex/static/resources/es/ELELex.tsv` |
| 获取日期 | 2026-10-03 |
| 覆盖等级 | A1 · A2 · B1 · B2 · C1（**不含 C2**） |
| 词条数量 | 14,290 条 |
| 词性标注体系 | [FreeLing tagset](https://freeling-user-manual.readthedocs.io/en/v4.0/tagsets/tagset-es/)（简化映射见 `freeling_to_elelex.yaml`） |

**引用要求**：下载页面注明 ELELex 的参考论文"Reference article forthcoming"（尚未正式发表）。
在该论文正式发布前，若你在研究中使用了 ELELex，请引用 CEFRLex 项目页面
<https://cental.uclouvain.be/cefrlex/> 及上表中的资源页面，并注明数据获取日期。

## 2. 许可证

本目录内容采用 **Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International**
（署名—非商业性使用—相同方式共享 4.0 国际）许可协议授权。

- 许可证摘要：https://creativecommons.org/licenses/by-nc-sa/4.0/
- 许可证全文：见同目录 [`LICENSE`](./LICENSE)
- 完整法律文本：https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode

**核心限制（请务必遵守）**

1. **署名（BY）**：必须保留上表中的署名信息与许可证链接。
2. **非商业（NC）**：**不得将本内容用于商业目的。**
   许可证原文定义为"并非主要为商业优势或金钱报酬而使用"。提取、复用本数据库的
   实质性内容同样仅限非商业目的（参见许可证 Section 4 关于数据库权利的规定）。
3. **相同方式共享（SA）**：如果你对本内容进行了**改编**（例如裁剪字段、转换格式、
   合并进自己的词表），则改编后的材料**必须**继续以 CC BY-NC-SA 4.0
   （或 Creative Commons 认定的兼容许可证）授权，并注明做过修改。
4. 不得额外施加限制条款，也不得使用技术措施妨碍他人行使许可证授予的权利。

## 3. 本项目对该文件的处理方式：未修改（聚合）

**本目录下的 `ELELex.tsv` 是原样收录的原始文件，未经任何修改。**
仅供本项目在运行时读取、在内存中解析为查询索引，**不落盘生成派生数据文件**。

依据 Creative Commons 官方对 ShareAlike 的说明：

> "The ShareAlike condition applies only for works considered adaptations under copyright law,
> not simply in collections with other works (also referred to as mere aggregations).
> Simply including an SA work unmodified alongside unrelated materials does not produce an adaptation."
> —— <https://wiki.creativecommons.org/wiki/ShareAlike_interpretation>

因此本文件与项目其余部分构成**聚合（mere aggregation）**而非改编，
ShareAlike **不会**传染到本项目的 MIT 代码。项目代码仍为 MIT。

**校验值**（用于证明本文件与上游一致、未被修改）：

```
sha256  ELELex.tsv
87a28dc6d3c5c2344883698f7bc77e259bd42212446ac2b52761a3fc8f5f26cf
```

文件大小：1,442,791 字节；行数：14,291（1 行表头 + 14,290 条词条）。

如需刷新原始文件，请运行 `tools/update_elelex.py`（会重新从上游下载并校验）。

## 4. 如果你需要商用

请**直接删除本目录**（`data/third_party/elelex/`）。
删除后，本项目其余部分即恢复为纯 MIT，可自由用于商业目的——
此时等级判定会自动退化为仅使用 `wordfreq`（MIT 代码）的词频近似，
功能不受影响，只是 C1 附近的等级精度略降。
详见项目根目录 `README.md` 的「许可证说明」一节。

若确实需要在商业场景中使用 ELELex，请自行联系 UCLouvain / CENTAL 获取授权。

## 5. 数据文件格式（供参考，非上游文档）

`ELELex.tsv` 为制表符分隔、字段用双引号包裹，表头如下：

```
"word"  "tag"  "level_freq@a1"  "level_freq@a2"  "level_freq@b1"  "level_freq@b2"
"level_freq@c1"  "total_freq@total"  "nb_doc@a1" … "nb_doc@total"
```

- `word`：词元（lemma）；多词条用下划线连接，例如 `a_el_aire_libre`。
- `tag`：FreeLing 词性标注。
- `level_freq@<level>`：该词在对应 CEFR 等级语料中的归一化频率。
- `total_freq@total` / `nb_doc@*`：总体频率与文档数。

本项目由这五档频率推导出单一等级：从 A1 往上扫，取**第一个**满足
`level_freq@level ≥ 0.6 × 各档最大值` 且 `≥ 0.5` 的等级
（即「最早被习得」的等级；实现见 `core/level.py` 的 `pick_elelex_level`）。
未被 ELELex 收录的词回退到 wordfreq 词频近似。
