"""轻量西语词形还原（lemmatization）+ 停用词表。

**这是一个刻意的轻量方案**：不引入 spaCy / stanza 等重型依赖（Windows 上体积大、
模型需额外下载）。代价是只能处理规则变化，不规则形式（如 `fui`、`tengo`）无法还原。

## 选择算法

单纯「取词频最高者」并不够：在西语语料里，**复数形式常常比单数更常见**
（`flores` 4.83 > `flor` 4.47、`niños` 5.39 > `niño` 5.10），所以高频优先会把
`flores` 错当成原形。改成按「候选类型 + 词频比值」判定：

1. **动词 / 副词还原**（`-ando`/`-iendo`/`-ado`/`-ído`/`-mente`）：词尾语义明确，
   只要得分 ≥ 最高分的 0.80 就采用；
2. **复数 → 单数**（含 `-ciones→-ción` 这类需要补回重音的规则）：
   要求得分 ≥ 最高分的 0.85，且绝对分 ≥ 2.5；
3. 否则取词频最高者（通常就是原词本身）。

这些阈值是用真实词频校准过的：正确还原的比值落在 0.93–1.17，
而错误还原（`adiós→adió` 0.32、`inglés→inglé` 0.27、`país→paí` 0.40）都远低于阈值。

> 刻意**不做**阴阳性互换（`casa ↔ caso`）：两者比值高达 0.99，无法用词频区分，
> 强行互换会把 `casa` 错还原成 `caso`。宁可保留 `chica` 也不冒这个险。

**重音处理**：候选保留重音（`rápidamente` → `rápido`），因为重音在西语里有意义，
且 wordfreq 词表条目本身带重音；而查词表（glossary / ELELex）时统一用去重音的小写
形式做键。两者各司其职。

本模块在 README「已知限制」中被明确标注为**近似**，不作为权威词法分析。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# 西语单词（含重音字母与 ñ/ü）
_WORD_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+")
_VOWELS = "aeiouáéíóúü"

# 候选类型
KIND_IRREGULAR = "irregular"
KIND_VERB = "verb"
KIND_ADVERB = "adverb"
KIND_PLURAL = "plural"
KIND_SURFACE = "surface"

# 人工整理的**高频不规则动词形式** → 不定式。
#
# 规则词尾推导对不规则动词无能为力（`quiero` / `hago` / `tengo` / `voy` 都推不出原形），
# 而这些恰是初学者最常遇到的词，因此用一张人工表兜住。表是按「最高频不规则动词」整理的，
# 覆盖 ser / estar / haber / tener / hacer / ir / venir / poder / querer / saber / decir /
# ver / dar / poner / salir / volver / suponer / parecer / conocer / seguir / pedir /
# sentir / dormir / jugar / empezar / pensar / entender / perder / encontrar / recordar /
# contar / traer / oír / creer / caer / valer / doler / soler / andar / gustar / encantar 等。
#
# **刻意排除**有歧义的形式（既可能是动词也可能是名词/限定词）：
#   esta / este / estas / estos（也可能是指示词）、para（也可能是介词）、
#   como（也可能是「像」）、vino（也可能是「葡萄酒」）、tenia（也可能是「绦虫」）、
#   ve / di / da / es / son 等过短且易冲突的形式。
IRREGULAR_FORMS: Dict[str, str] = {}


def _register(infinitive: str, forms: str) -> None:
    for form in forms.split():
        IRREGULAR_FORMS[form] = infinitive


_register("ser", "soy es eres somos sois son era eras éramos eran fui fuiste fuimos fueron sea seas sean sido siendo")
_register("estar", "estoy estás estamos estáis están estaba estabas estábamos estaban estuve estuviste estuvo estuvieron esté estén estado estando")
_register("haber", "he has ha hemos habéis han había habías habían hubo haya hayan habido habiendo hay")
_register("tener", "tengo tienes tiene tenemos tenéis tienen tenía tenías teníamos tenían tuve tuviste tuvo tuvieron tenga tengan tenido teniendo")
_register("hacer", "hago haces hace hacemos hacéis hacen hacía hacías hacíamos hacían hice hiciste hizo hicieron haga hagan hecho haciendo")
_register("ir", "voy vas vamos vais iba ibas íbamos iban fui fuiste fueron vaya vayan ido yendo")
_register("venir", "vengo vienes viene venimos venís vienen venía venías venían vine viniste vinieron venga vengan venido viniendo")
_register("poder", "puedo puedes puede podemos podéis pueden podía podías podían pude pudiste pudo pudieron pueda puedan podido pudiendo")
_register("querer", "quiero quieres quiere queremos queréis quieren quería querías querían quise quisiste quiso quisieron quiera quieran querido queriendo")
_register("saber", "sabes sabe sabemos sabéis saben sabía sabías sabían supe supiste supo supieron sepa sepan sabido sabiendo")
_register("decir", "digo dices dice decimos decís dicen decía decías decían dije dijiste dijo dijeron diga digan dicho diciendo")
_register("ver", "veo ves vemos veis veía veías veían vi vio vieron vea vean visto viendo")
_register("dar", "doy damos dais daba dabas daban di dio dieron dé den dado dando")
_register("poner", "pongo pones pone ponemos ponéis ponen ponía ponías ponían puse pusiste puso pusieron ponga pongan puesto poniendo")
_register("salir", "salgo sales sale salimos salís salen salía salías salían salí saliste salió salieron salga salgan salido saliendo")
_register("volver", "vuelvo vuelves vuelve volvemos volvéis vuelven volvía volvías volví volviste volvió volvieron vuelva vuelvan vuelto volviendo")
_register("suponer", "supongo supones supone suponemos suponéis suponen suponía supuse supuso supongan supuesto suponiendo")
_register("parecer", "parezco pareces parece parecemos parecen parecía parecían pareció parezca parecido pareciendo")
_register("conocer", "conozco conoces conoce conocemos conocéis conocen conocía conocían conocí conoció conozca conocido conociendo")
_register("seguir", "sigo sigues sigue seguimos seguís siguen seguía seguían seguí siguió siga sigan seguido siguiendo")
_register("conseguir", "consigo consigues consigue conseguimos consiguen conseguí consiguió consigan conseguido")
_register("pedir", "pido pides pide pedimos piden pedía pedían pedí pidió pidieron pida pidan pedido pidiendo")
_register("sentir", "siento sientes siente sentimos sienten sentía sentían sentí sintió sintieron sienta sentido sintiendo")
_register("dormir", "duermo duermes duerme dormimos duermen dormía dormían dormí durmió durmieron duerma dormido durmiendo")
_register("morir", "muero mueres muere morimos mueren murió murieron muerto muriendo")
_register("jugar", "juego juegas juega jugamos juegan jugaba jugué jugó jugaron juegue jugado jugando")
_register("empezar", "empiezo empiezas empieza empezamos empiezan empezaba empecé empezó empezaron empiece empezado empezando")
_register("comenzar", "comienzo comienzas comienza comenzamos comienzan comenzó comenzaron comience comenzado comenzando")
_register("pensar", "pienso piensas piensa pensamos piensan pensaba pensé pensó pensaron piense pensado pensando")
_register("entender", "entiendo entiendes entiende entendemos entienden entendía entendí entendió entendieron entienda entendido entendiendo")
_register("perder", "pierdo pierdes pierde perdemos pierden perdía perdí perdió perdieron pierda perdido perdiendo")
_register("encontrar", "encuentro encuentras encuentra encontramos encuentran encontraba encontré encontró encontraron encuentre encontrado encontrando")
_register("recordar", "recuerdo recuerdas recuerda recordamos recuerdan recordaba recordé recordó recordaron recuerde recordado recordando")
_register("contar", "cuento cuentas cuenta contamos cuentan contaba conté contó contaron cuente contado contando")
_register("mostrar", "muestro muestras muestra mostramos muestran mostró mostraron muestre mostrado mostrando")
_register("costar", "cuesto cuesta cuestan costaba costó costaron cueste costado")
_register("traer", "traigo traes trae traemos traéis traen traía trajo trajeron traiga traído trayendo")
_register("oír", "oigo oyes oye oímos oís oyen oía oyó oyeron oiga oído oyendo")
_register("creer", "creo crees cree creemos creéis creen creía creía creyó creyeron crea creído creyendo")
_register("caer", "caigo caes cae caemos caen caía cayó cayeron caiga caído cayendo")
_register("valer", "valgo vales vale valemos valen valía valió valga valido valiendo")
_register("doler", "duele duelen dolía dolió dolido")
_register("soler", "suelo sueles suele solemos suelen solía solían solido")
_register("andar", "ando andas anda andamos andan andaba anduve anduvo anduvieron ande andado andando")
_register("gustar", "gusta gustan gustaba gustó gustaron guste gustado")
_register("encantar", "encanta encantan encantaba encantó encantaron encante encantado")
_register("faltar", "falta faltan faltaba faltó faltaron falte faltado")
_register("sobrar", "sobra sobran sobraba sobró sobrado")
_register("importar", "importa importan importaba importó importe importado")
_register("apetecer", "apetece apetecen apetecía apeteció")
_register("interesar", "interesa interesan interesaba interesó")
_register("llover", "llueve llovía llovió llovido lloviendo")

# 选择阈值（用真实词频校准，见模块 docstring）
VERB_MIN_RATIO = 0.80
PLURAL_MIN_RATIO = 0.85
PLURAL_MIN_SCORE = 2.5

# 高频功能词：冠词、代词、介词、连词、限定词等。
# 这些词在词表里列出来对学习者没有价值，因此统一过滤。
# 注意：ser / estar / haber / tener 等实义动词**不在此列**，它们是 A1 学习内容。
STOPWORDS: frozenset = frozenset(
    """
    el la los las un una unos unas lo al del
    yo tu tú él ella ello nosotros nosotras vosotros vosotras ellos ellas
    usted ustedes me te se nos os le les
    mí ti sí conmigo contigo
    mi mis tu tus su sus nuestro nuestra nuestros nuestras
    mío mía míos mías tuyo tuya suyo suya
    este esta esto estos estas ese esa eso esos esas
    aquel aquella aquello aquellos aquellas
    algo alguien nadie nada alguno alguna ninguno ninguna
    quien quién quienes quiénes que qué cual cuál cuales cuáles
    cuanto cuánto cuanta cuánta cuantos cuántos
    donde dónde cuando cuándo como cómo
    a ante bajo con contra de desde durante en entre hacia hasta
    para por según sin so sobre tras mediante
    y e o u pero mas sino aunque porque pues si ni
    no sí ya muy más menos tan tanto también tampoco
    siempre nunca jamás aquí allí allá ahí
    ahora luego después antes hoy ayer mañana
    bien mal así casi solo sólo aún todavía entonces además
    otro otra otros otras mismo misma todo toda todos todas
    cada varios varias cualquier cualesquiera
    """.split()
)


def strip_accents(text: str) -> str:
    """去掉重音符号：`canción` → `cancion`（ñ → n）。"""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def normalize_token(token: str) -> str:
    """面向查表的规范形式：小写 + 去重音 + 去首尾空白。

    用于 glossary / ELELex 的索引键（两边的键都按这个规则生成，保证一致）。
    """
    return strip_accents((token or "").strip().lower())


def lower_token(token: str) -> str:
    """保留重音的小写形式（候选原形用这个）。"""
    return (token or "").strip().lower()


def tokenize(text: str) -> List[str]:
    """从字幕文本中切出西语单词（保留原始重音，便于展示）。"""
    return _WORD_RE.findall(text or "")


def is_stopword(token: str) -> bool:
    """判断是否为被过滤的功能词（按去重音后的小写形式比较）。"""
    return normalize_token(token) in STOPWORDS


# --------------------------------------------------------------------------
# 候选原形生成
# --------------------------------------------------------------------------

# -mente 副词 → 形容词（rápidamente → rápido）
_MENTE_SUFFIX = "mente"

# 副动词 / 过去分词：词尾长且语义明确，命中后不再套用短词尾规则（避免生成噪声）。
# 同时收录带重音的形式（leído / caído 这类 -er/-ir 分词会带重音）。
_LONG_VERB_RULES: Sequence[Tuple[str, Tuple[str, ...]]] = (
    ("ándose", ("ar",)),
    ("iéndose", ("er", "ir")),
    ("yéndose", ("er", "ir")),
    ("ando", ("ar",)),
    ("iendo", ("er", "ir")),
    ("yendo", ("er", "ir")),
    ("ados", ("ar",)),
    ("adas", ("ar",)),
    ("ado", ("ar",)),
    ("ada", ("ar",)),
    ("ídos", ("er", "ir")),
    ("ídas", ("er", "ir")),
    ("ído", ("er", "ir")),
    ("ída", ("er", "ir")),
    ("idos", ("er", "ir")),
    ("idas", ("er", "ir")),
    ("ido", ("er", "ir")),
    ("ida", ("er", "ir")),
)

# 时态 / 人称词尾。按长度降序，保证长的先匹配。
_SHORT_VERB_RULES: Sequence[Tuple[str, Tuple[str, ...]]] = (
    ("ábamos", ("ar",)),
    ("ábais", ("ar",)),
    ("aban", ("ar",)),
    ("abas", ("ar",)),
    ("aba", ("ar",)),
    ("íamos", ("er", "ir")),
    ("íais", ("er", "ir")),
    ("ían", ("er", "ir")),
    ("ías", ("er", "ir")),
    ("ía", ("er", "ir")),
    ("asteis", ("ar",)),
    ("aron", ("ar",)),
    ("aste", ("ar",)),
    ("isteis", ("er", "ir")),
    ("ieron", ("er", "ir")),
    ("iste", ("er", "ir")),
    ("amos", ("ar", "er", "ir")),
    ("emos", ("ar", "er", "ir")),
    ("imos", ("er", "ir")),
    ("éis", ("er",)),
    ("áis", ("ar",)),
    ("ís", ("ir",)),
    ("rán", ("ar", "er", "ir")),
    ("rás", ("ar", "er", "ir")),
    ("réis", ("ar", "er", "ir")),
    ("remos", ("ar", "er", "ir")),
    ("ría", ("ar", "er", "ir")),
    ("ré", ("ar", "er", "ir")),
    ("rá", ("ar", "er", "ir")),
    ("an", ("ar",)),
    ("en", ("er", "ir")),
    ("es", ("er", "ir")),
    ("as", ("ar",)),
    ("ió", ("er", "ir")),
    ("ó", ("ar",)),
    ("í", ("er", "ir")),
    ("é", ("ar",)),
    ("o", ("ar", "er", "ir")),
    ("a", ("ar",)),
    ("e", ("er", "ir")),
)

# 复数还原中需要「补回重音」的规则（canción / camión / ciudad …）
_ACCENT_RESTORE_RULES: Sequence[Tuple[str, Tuple[str, ...]]] = (
    ("logías", ("logía",)),
    ("grafías", ("grafía",)),
    ("idades", ("idad",)),
    ("ciones", ("ción",)),
    ("tudes", ("tud",)),
    ("dades", ("dad",)),
    ("iones", ("ión",)),
    ("ones", ("ón",)),
    ("ices", ("iz",)),
    ("uces", ("uz",)),
)

# 常规复数还原
_PLURAL_RULES: Sequence[Tuple[str, Tuple[str, ...]]] = (
    ("ces", ("z",)),
    ("es", ("",)),
    ("s", ("",)),
)


def _dedupe_pairs(pairs: Iterable[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """按候选去重，保留首次出现的类型（优先级由调用顺序决定）。"""
    seen: Dict[str, str] = {}
    order: List[Tuple[str, str]] = []
    for candidate, kind in pairs:
        candidate = (candidate or "").strip()
        if not candidate or candidate in seen:
            continue
        seen[candidate] = kind
        order.append((candidate, kind))
    return order


def candidate_analyses(token: str) -> List[Tuple[str, str]]:
    """生成 ``(候选原形, 候选类型)`` 列表（保留重音）。"""
    word = lower_token(token)
    if not word:
        return []
    if len(word) <= 1:
        return [(word, KIND_SURFACE)]

    pairs: List[Tuple[str, str]] = []

    # 0) 高频不规则动词形式（人工表，命中则优先级最高）
    irregular = IRREGULAR_FORMS.get(word)
    if irregular:
        pairs.append((irregular, KIND_IRREGULAR))

    if len(word) <= 2:
        pairs.append((word, KIND_SURFACE))
        return _dedupe_pairs(pairs)

    # 1) -mente 副词
    if word.endswith(_MENTE_SUFFIX) and len(word) > len(_MENTE_SUFFIX) + 2:
        base = word[: -len(_MENTE_SUFFIX)]
        if base:
            stem = base[:-1] if base[-1] in _VOWELS else base
            pairs.append((stem + "o", KIND_ADVERB))
            pairs.append((stem + "a", KIND_ADVERB))
            pairs.append((base, KIND_ADVERB))

    # 2) 副动词 / 过去分词（命中就不再套用短词尾规则）
    matched_long = False
    for suffix, replacements in _LONG_VERB_RULES:
        if len(word) > len(suffix) + 1 and word.endswith(suffix):
            stem = word[: -len(suffix)]
            pairs.extend((stem + r, KIND_VERB) for r in replacements)
            matched_long = True
            break

    # 3) 时态 / 人称词尾
    if not matched_long:
        for suffix, replacements in _SHORT_VERB_RULES:
            if len(word) > len(suffix) + 1 and word.endswith(suffix):
                stem = word[: -len(suffix)]
                pairs.extend((stem + r, KIND_VERB) for r in replacements)

    # 4) 复数还原（先补重音规则，再常规规则）
    for suffix, replacements in _ACCENT_RESTORE_RULES + _PLURAL_RULES:
        if len(word) > len(suffix) and word.endswith(suffix):
            stem = word[: -len(suffix)]
            pairs.extend((stem + r, KIND_PLURAL) for r in replacements)

    # 5) 原词本身放最后（优先级最低）
    pairs.append((word, KIND_SURFACE))

    return _dedupe_pairs(pairs)


def candidate_lemmas(token: str) -> List[str]:
    """给出一个词所有可能的原形候选（保留重音；顺序即优先级）。"""
    return [candidate for candidate, _ in candidate_analyses(token)]


@dataclass
class LemmaResult:
    """还原结果。"""

    surface: str
    lemma: str
    candidates: List[str]
    score: float = 0.0
    kind: str = KIND_SURFACE

    @property
    def changed(self) -> bool:
        return lower_token(self.surface) != lower_token(self.lemma)


def lemmatize(
    token: str,
    *,
    scorer: Optional[Callable[[str], float]] = None,
) -> LemmaResult:
    """把词还原为原形。

    :param scorer: 可注入自定义打分函数（测试时用来摆脱 wordfreq 依赖）。
    """
    analyses = candidate_analyses(token)
    if not analyses:
        return LemmaResult(surface=token, lemma=normalize_token(token), candidates=[])

    if scorer is None:
        scorer = _default_scorer()

    scored: List[Tuple[float, str, str]] = []
    for candidate, kind in analyses:
        try:
            score = float(scorer(candidate))
        except Exception:  # pragma: no cover - 打分器异常时按未知处理
            score = 0.0
        scored.append((score, candidate, kind))

    top_score = max(score for score, _, _ in scored)

    def best_of(kinds: Tuple[str, ...]) -> Optional[Tuple[float, str, str]]:
        pool = [item for item in scored if item[2] in kinds and item[0] > 0]
        return max(pool, key=lambda item: item[0]) if pool else None

    # 0) 人工表中命中的高频不规则形式：直接采用（它就是确定的答案）
    irregular_best = best_of((KIND_IRREGULAR,))
    if irregular_best:
        return LemmaResult(
            surface=token,
            lemma=irregular_best[1],
            candidates=[c for c, _ in analyses],
            score=irregular_best[0],
            kind=irregular_best[2],
        )

    # 1) 动词 / 副词还原：词尾语义明确，阈值最宽
    verb_best = best_of((KIND_VERB, KIND_ADVERB))
    if verb_best and verb_best[0] >= VERB_MIN_RATIO * top_score and top_score > 0:
        return LemmaResult(
            surface=token,
            lemma=verb_best[1],
            candidates=[c for c, _ in analyses],
            score=verb_best[0],
            kind=verb_best[2],
        )

    # 2) 复数 → 单数：需要比值 + 绝对分双重要求
    plural_best = best_of((KIND_PLURAL,))
    if (
        plural_best
        and top_score > 0
        and plural_best[0] >= PLURAL_MIN_RATIO * top_score
        and plural_best[0] >= PLURAL_MIN_SCORE
    ):
        return LemmaResult(
            surface=token,
            lemma=plural_best[1],
            candidates=[c for c, _ in analyses],
            score=plural_best[0],
            kind=plural_best[2],
        )

    # 3) 否则取词频最高者（通常就是原词本身）
    score, candidate, kind = max(scored, key=lambda item: item[0])
    if score <= 0.0:
        candidate, kind = lower_token(token), KIND_SURFACE
    return LemmaResult(
        surface=token,
        lemma=candidate,
        candidates=[c for c, _ in analyses],
        score=score,
        kind=kind,
    )


def _default_scorer() -> Callable[[str], float]:
    """默认打分器：wordfreq 西语 Zipf（0–8）；带重音形式优先，失败则试去重音形式。"""
    try:
        from wordfreq import zipf_frequency
    except Exception:  # pragma: no cover - 未安装 wordfreq 时退化为空打分

        def _zero(_: str) -> float:
            return 0.0

        return _zero

    cache: Dict[str, float] = {}

    def _score(word: str) -> float:
        if word in cache:
            return cache[word]
        value = float(zipf_frequency(word, "es"))
        if value <= 0:
            plain = strip_accents(word)
            if plain != word:
                value = float(zipf_frequency(plain, "es"))
        cache[word] = value
        return value

    return _score


def content_lemmas(text: str) -> List[Tuple[str, str]]:
    """从文本中取出「值得学习」的 (原词小写, 原形) 对，已过滤停用词。

    保留出现顺序，去重与计数交给调用方。
    """
    pairs: List[Tuple[str, str]] = []
    for token in tokenize(text):
        if len(token) < 2:
            continue
        if is_stopword(token):
            continue
        result = lemmatize(token)
        pairs.append((token.lower(), result.lemma))
    return pairs
