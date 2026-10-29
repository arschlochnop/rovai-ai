"""文字/事实增强比较器：判断两篇文章是否报道同一具体事件。

输入只有两篇文章的白名单字段（标题、正文、来源、时间、链接），不接收 story_id、标签或历史。
输出三态 label、可读 reason、支持/冲突/未知证据、核心事实槽位状态，以及与事实分开的基础诊断分数。

判定顺序（保守）：
1. 综述/合集：不与单事件连接；两篇综述只有近乎全文重复才算同一；
2. 明确冲突（同主体同角色同口径的数值、方向、地址、日期、主体互斥）→ different_event；
3. 复合报道与单项报道部分重叠、立场相反（否认/确认）、快照时间相距过远 → 至多 uncertain；
4. 支持：需要“事实锚点 + 文本”两类证据同时成立，单个数量或地址锚点不能独立放行；
5. 既无共享事实又文本差异大 → different_event（无共享证据）；其余 uncertain。

所有文本/锚点分数只是证据强度，不是同事件概率。
"""
from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field

from . import textnorm as tn
from .background import Background, char_ngrams, empty_background
from .contract import validate_result
from .facts import (
    ArticleFacts, Quantity, compare_values, extract_facts, subject_identity, subjects_relation,
    units_compatible, claim_units, GENERIC_LATIN, ALIASES, CJK,
)

VERSION = "fact-4.1.1"

DEFAULT_CONFIG = {
    "name": "fact_enhanced",
    # —— 文本证据门槛（来源：开发集分布核读后人工设定，见 reports；不是概率）——
    "text_high": 0.55,  # 文本强相似（字符 2/3-gram TF-IDF 余弦）
    "text_mid": 0.30,  # 文本中等相似
    "text_disjoint": 0.12,  # 低于此且无共享锚点 → 无共享证据
    "near_dup_containment": 0.88,  # 正文 3-gram 包含度：近乎全文复制/删节
    "title_near_jaccard": 0.6,  # 标题字符 2-gram Jaccard：标题近乎相同（调序/个别字差异）
    "exact_min_len": 6,
    # —— 锚点权重 ——
    "anchor_strong": 0.8,  # 单个强锚点门槛
    "fact_path_anchor_score": 1.6,  # 事实路径：锚点总分
    "fact_path_min_items": 2,  # 事实路径：至少两个不同锚点
    "text_path_anchor_score": 0.6,  # 文本强相似路径仍需的锚点分
    "near_dup_anchor_score": 0.25,
    "rare_latin_max_df": 2,
    "semirare_latin_max_df": 10,
    "rare_title_cjk_max_df": 1,
    "conflict_override_anchor": 3.0,  # 冲突与大量锚点并存时降为 uncertain 的门槛
    # —— 结构/时间保护 ——
    "snapshot_max_pub_gap_minutes": 45,
    "subject_mismatch_max_anchor": 0.8,  # 主体互斥且锚点低于此 → different
    # —— v2 新增（来源：v1 在 dev 拆分上的真实误合并：$TORCH/$AI/$PRIORS 同模板、皮查伊同场不同表态）——
    "exclusive_entity_max_anchor": 1.6,  # 双方标题各有对方全文没有的实体，且共享锚点低于此 → different
    "statement_exclusive_ratio": 0.5,  # 同一发言主体：双方表态各有 ≥50% 单元不见于对方全文 → 不同表态
    "statement_min_exclusive_units": 3,
    # v3：表态分歧只在共享事实锚点 < fact_path_anchor_score 时起作用；v4 起只阻止 same、不再断言 different；
    # 共享事实充分时不再被措辞差异否决（来源：v2 把 Iron Fish、币安 PREDICT 空投同一公告的转述拆散）
    # 语义补充：当前环境无可用模型，固定关闭（见 README）
    "semantic": {"enabled": False, "reason": "运行环境无本地 embedding 模型，huggingface.co 被网络策略拒绝"},
}

SCORE_NOTE = "tfidf_cosine/containment/anchor_score 仅为证据强度诊断，不是同事件概率"

SOFT_ROLES = {"valuation", "mcap", "holding", "gain", "loss"}
CONFLICT_ROLES = {
    "quote", "threshold", "level", "change", "flow", "transfer", "trade", "borrow", "collateral", "mint", "burn",
    "buyback", "unlock", "airdrop", "raise", "company_valuation", "revenue", "profit", "incident_loss",
    "liquidation", "target_price", "fine", "supply", "probability", "volume", "ransom", "people",
} | SOFT_ROLES
VENUE_SENSITIVE_ROLES = {"quote", "change", "level", "volume"}
SNAPSHOT_ROLES = {"quote", "threshold", "level", "change"}
OPPOSITE = {("up", "down"), ("down", "up"), ("in", "out"), ("out", "in"), ("buy", "sell"), ("sell", "buy")}

_C3 = 3


@dataclass
class Prepared:
    view_id: str
    facts: ArticleFacts
    pub: dt.datetime | None
    title_c: str
    core_c: str
    full_c: str
    vec: dict[str, float]
    norm: float
    c3: set[str]
    title_c2: set[str]
    quantities: list[Quantity]
    latin_df: dict[str, int] = field(default_factory=dict)
    title_cjk_rare: set[str] = field(default_factory=set)
    snapshot_like: bool = False


def _parse_pub(s: str) -> dt.datetime | None:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?$", s or "")
    if not m:
        return None
    y, mo, d, h, mi, sec, frac = m.groups()
    return dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(sec), int((frac or "0").ljust(6, "0")))


def _dedupe_quantities(qs: list[Quantity]) -> list[Quantity]:
    seen = {}
    for q in qs:
        k = (q.value, q.unit_cat, q.unit, q.role, q.scope, q.subject, q.direction)
        if k in seen:
            seen[k].in_title = seen[k].in_title or q.in_title
        else:
            seen[k] = q
    return list(seen.values())


def prepare(view: dict, config: dict | None = None) -> Prepared:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    bg: Background = cfg.get("background") or empty_background()
    f = extract_facts(view)
    title_c = tn.compact(f.title_norm)
    core_c = tn.compact(f.core_norm)
    full_c = title_c + "。" + core_c
    # 标题计两次：标题是编辑对事件的概括，权重高于正文
    grams = char_ngrams(f.title_norm) * 2 + char_ngrams(f.core_norm)
    tf: dict[str, int] = {}
    for g in grams:
        tf[g] = tf.get(g, 0) + 1
    vec = {g: (1.0 + math.log(c)) * bg.idf(g) for g, c in tf.items()}
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    c3 = {full_c[i:i + _C3] for i in range(len(full_c) - _C3 + 1)}
    t2 = {title_c[i:i + 2] for i in range(len(title_c) - 1)}
    latin_df = {t: bg.latin_doc_freq(t) for t in f.latin_tokens}
    # 标题中的稀有中文片段（背景频次≤阈值的 3-gram，合并成连续片段）
    rare_cjk: set[str] = set()
    tc = re.sub(rf"[^{CJK}]", " ", f.title_norm)
    for chunk in tc.split():
        run = ""
        for i in range(len(chunk) - 2):
            g = chunk[i:i + 3]
            if bg.n_docs and bg.ngram_df.get(g, 0) <= cfg["rare_title_cjk_max_df"]:
                run = run + g[-1] if run and chunk[i:i + 2] == run[-2:] else g
            else:
                if len(run) >= 3:
                    rare_cjk.add(run)
                run = ""
        if len(run) >= 3:
            rare_cjk.add(run)
    qs = _dedupe_quantities(f.quantities)
    snapshot_like = any(q.in_title and q.role in SNAPSHOT_ROLES for q in qs)
    return Prepared(view_id=view["id"], facts=f, pub=_parse_pub(view.get("published", "")), title_c=title_c,
                    core_c=core_c, full_c=full_c, vec=vec, norm=norm, c3=c3, title_c2=t2, quantities=qs,
                    latin_df=latin_df, title_cjk_rare=rare_cjk, snapshot_like=snapshot_like)


# ---------------------------------------------------------------------------
def _cosine(a: Prepared, b: Prepared) -> float:
    if len(a.vec) > len(b.vec):
        a, b = b, a
    dot = sum(w * b.vec.get(g, 0.0) for g, w in a.vec.items())
    return dot / (a.norm * b.norm)


def _containment(a: Prepared, b: Prepared) -> float:
    if not a.c3 or not b.c3:
        return 0.0
    return len(a.c3 & b.c3) / min(len(a.c3), len(b.c3))


def _jaccard(x: set, y: set) -> float:
    return len(x & y) / len(x | y) if (x or y) else 0.0


def _qty_weight(qa: Quantity, qb: Quantity, match: str) -> float:
    sig = min(qa.sig_digits, qb.sig_digits)
    if qa.unit_cat in ("percent", "pp", "bp"):
        w = {1: 0.1, 2: 0.3, 3: 0.6}.get(sig, 0.8)
    else:
        w = {1: 0.15, 2: 0.4, 3: 0.8}.get(sig, 1.0)
    if qa.role == "unknown" or qb.role == "unknown":
        w *= 0.6
    if qa.role in SOFT_ROLES or qb.role in SOFT_ROLES:
        w *= 0.7
    if match == "approx":
        w *= 0.5
    return round(w, 3)


def _qdesc(q: Quantity) -> str:
    return f"{q.raw}[{q.role}/{q.scope or '口径未写明'}/{q.subject or '主体未识别'}]"


def _venue_ok(qa: Quantity, qb: Quantity, va: set, vb: set) -> bool:
    """报价/涨跌幅类：两篇都写明来源且来源不相交 → 不同口径，不可直接比较。"""
    if qa.role not in VENUE_SENSITIVE_ROLES:
        return True
    return not (va and vb and not (va & vb))


def _role_compatible(qa: Quantity, qb: Quantity) -> bool:
    return qa.role == qb.role or "unknown" in (qa.role, qb.role)


def _scope_compatible(qa: Quantity, qb: Quantity) -> bool:
    return qa.scope == qb.scope or not qa.scope or not qb.scope


def _binding_groups(qs: list[Quantity]) -> dict:
    groups: dict = {}
    for q in qs:
        ids = frozenset(subject_identity(q.subject)) if q.subject else frozenset()
        k = (q.role, q.scope, q.unit_cat, q.unit, ids)
        groups.setdefault(k, []).append(q)
    return groups


def _distinct_values(qs: list[Quantity]) -> int:
    reps: list[Quantity] = []
    for q in qs:
        if not any(compare_values(q, r) in ("equal", "approx") for r in reps):
            reps.append(q)
    return len(reps)


def align_quantities(A: Prepared, B: Prepared) -> dict:
    va, vb = A.facts.venues, B.facts.venues
    matches, conflicts, soft_conflicts, unclear = [], [], [], []
    matched_b = set()
    ga, gb = _binding_groups(A.quantities), _binding_groups(B.quantities)
    a_single = len(A.facts.title_subject_ids) <= 1 and not A.facts.compound_title and not A.facts.roundup
    b_single = len(B.facts.title_subject_ids) <= 1 and not B.facts.compound_title and not B.facts.roundup
    for qa in A.quantities:
        if qa.unit_cat == "none" and (qa.role == "unknown" or qa.sig_digits < 3):
            continue
        best = None
        for j, qb in enumerate(B.quantities):
            if units_compatible(qa, qb) != "same":
                continue
            if not _role_compatible(qa, qb) or not _scope_compatible(qa, qb):
                continue
            # 来源不同只阻止“冲突”判断；跨来源数值完全一致仍可作为支持证据
            if (qa.direction, qb.direction) in OPPOSITE:
                continue
            cv = compare_values(qa, qb)
            if cv in ("equal", "approx"):
                w = _qty_weight(qa, qb, cv)
                if best is None or w > best[0]:
                    best = (w, cv, j, qb)
        if best is not None:
            w, cv, j, qb = best
            matched_b.add(j)
            matches.append({"type": "quantity", "match": cv, "weight": w, "left": _qdesc(qa), "right": _qdesc(qb),
                            "value": format(qa.value.normalize(), "f"), "unit": f"{qa.unit_cat}:{qa.unit}",
                            "role": qa.role if qa.role != "unknown" else qb.role, "in_title": qa.in_title or qb.in_title})
            continue
        # 冲突检查：同角色（已知）、同单位、同口径、同主体、绑定唯一
        if qa.role not in CONFLICT_ROLES:
            continue
        for qb in B.quantities:
            if qb.role != qa.role or units_compatible(qa, qb) != "same":
                continue
            if qa.scope != qb.scope:
                continue
            if not _venue_ok(qa, qb, va, vb):
                continue
            ida, idb = subject_identity(qa.subject), subject_identity(qb.subject)
            rel = subjects_relation(ida, idb)
            if rel in ("different", "related"):
                continue
            if rel == "unknown" and not (a_single and b_single):
                unclear.append({"fact": "quantity", "detail": f"{_qdesc(qa)} vs {_qdesc(qb)}：主体未绑定，不作冲突"})
                continue
            ka = (qa.role, qa.scope, qa.unit_cat, qa.unit, frozenset(ida))
            kb = (qb.role, qb.scope, qb.unit_cat, qb.unit, frozenset(idb))
            if _distinct_values(ga.get(ka, [qa])) > 1 or _distinct_values(gb.get(kb, [qb])) > 1:
                unclear.append({"fact": "quantity", "detail": f"{_qdesc(qa)}：同主体同角色有多个数值，绑定不清记 unknown"})
                continue
            if (qa.direction, qb.direction) in OPPOSITE:
                item = {"type": "direction", "left": _qdesc(qa), "right": _qdesc(qb),
                        "detail": f"同主体同角色方向相反（{qa.direction} vs {qb.direction}）"}
            elif compare_values(qa, qb) == "conflict":
                item = {"type": "quantity", "left": _qdesc(qa), "right": _qdesc(qb),
                        "detail": "同主体、同角色、同单位、同口径但数值不同"}
            else:
                continue
            item["role"] = qa.role
            item["in_title"] = qa.in_title and qb.in_title
            if qa.role in SOFT_ROLES:
                item["severity"] = "soft"
                soft_conflicts.append(item)
            else:
                item["severity"] = "hard"
                conflicts.append(item)
            break
    # 去重：同一数值只计一次锚点
    seen_vals = {}
    for m in matches:
        k = (m["value"], m["unit"])
        if k not in seen_vals or m["weight"] > seen_vals[k]["weight"]:
            seen_vals[k] = m
    uniq = list(seen_vals.values())
    # 冲突去重
    def _dedupe(items):
        out, keys = [], set()
        for it in items:
            k = (it["left"], it["right"])
            if k not in keys:
                keys.add(k)
                out.append(it)
        return out
    return {"matches": uniq, "conflicts": _dedupe(conflicts), "soft_conflicts": _dedupe(soft_conflicts),
            "unclear": unclear[:6]}


def _addresses(A: Prepared, B: Prepared) -> tuple[list, list]:
    sup, con = [], []
    fa, fb = A.facts, B.facts
    for x in sorted(fa.addresses_full & fb.addresses_full):
        sup.append({"type": "address_full", "value": x, "weight": 1.0})
    for x in sorted(fa.tx_full & fb.tx_full):
        sup.append({"type": "tx_full", "value": x, "weight": 1.0})
    if fa.addresses_full and fb.addresses_full and not (fa.addresses_full & fb.addresses_full):
        if len(fa.addresses_full) == 1 and len(fb.addresses_full) == 1:
            con.append({"type": "address", "severity": "hard", "detail": "两篇各有一个完整地址且不同",
                        "left": min(fa.addresses_full), "right": min(fb.addresses_full)})
    # 截写/前缀：只能证明“相容”，不能证明“相同”
    pa = list(fa.addresses_partial) + [(x[2:], "") for x in sorted(fa.addresses_full) if x.startswith("0x")]
    pb = list(fb.addresses_partial) + [(x[2:], "") for x in sorted(fb.addresses_full) if x.startswith("0x")]
    if fa.addresses_partial or fb.addresses_partial:
        compat = []
        for (p1, s1) in pa:
            for (p2, s2) in pb:
                if (p1.startswith(p2) or p2.startswith(p1)) and (not s1 or not s2 or s1.endswith(s2) or s2.endswith(s1)):
                    compat.append((p1, s1, p2, s2))
        if compat:
            p1, s1, p2, s2 = compat[0]
            sup.append({"type": "address_partial_compatible", "weight": 0.4,
                        "detail": f"0x{p1}{'…' + s1 if s1 else ''} 与 0x{p2}{'…' + s2 if s2 else ''} 前缀/后缀相容（不证明完整地址相同）"})
        elif pa and pb and len(pa) == 1 and len(pb) == 1:
            con.append({"type": "address", "severity": "hard", "detail": "截写地址前缀/后缀不相容",
                        "left": f"0x{pa[0][0]}…{pa[0][1]}", "right": f"0x{pb[0][0]}…{pb[0][1]}"})
    return sup, con


def _shared_latin(A: Prepared, B: Prepared, cfg: dict) -> list:
    out = []
    shared = (A.facts.latin_tokens & B.facts.latin_tokens) - GENERIC_LATIN
    for t in sorted(shared):
        df = A.latin_df.get(t, 0)
        if df <= cfg["rare_latin_max_df"]:
            out.append({"type": "rare_entity", "value": t, "background_df": df, "weight": 0.5})
        elif df <= cfg["semirare_latin_max_df"]:
            out.append({"type": "semirare_entity", "value": t, "background_df": df, "weight": 0.2})
    return out


def _present(ids: set[str], P: Prepared) -> bool:
    """主体身份键是否在另一篇全文中出现（拉丁实体/别名/中文核心任一出现即算）。"""
    full = P.facts.full_norm
    for i in ids:
        if i.startswith("zh:"):
            if i[3:] in full:
                return True
        else:
            if i in P.facts.latin_tokens:
                return True
            for alias, key in ALIASES.items():
                if key == i and alias in full:
                    return True
    return False


def _pub_gap_minutes(A: Prepared, B: Prepared) -> float | None:
    if A.pub is None or B.pub is None:
        return None
    return abs((A.pub - B.pub).total_seconds()) / 60.0


def _compound_partial(A: Prepared, B: Prepared) -> str | None:
    """部分重叠检查：一方标题里有数值事实在另一方找不到，且该事实的主体不同于双方共享事实的主体。

    缺失事实与共享事实同主体 → 信息增量/删节（允许）；
    缺失事实属于另一个主体（如 BTC ETF 之外再报 ETH ETF）→ 一篇含多个事件，另一篇只覆盖其一 → 不判同一。
    """
    for X, Y in ((A, B), (B, A)):
        shared_ids: list[set] = []
        missing: list[Quantity] = []
        for q in X.quantities:
            if not q.in_title or q.unit_cat == "none":
                continue
            found = any(units_compatible(q, r) == "same" and compare_values(q, r) in ("equal", "approx")
                        for r in Y.quantities)
            ids = subject_identity(q.subject)
            if found:
                if ids:
                    shared_ids.append(ids)
            else:
                missing.append(q)
        if not shared_ids or not missing:
            continue
        shared_named = set().union(*[{x for x in s if not x.startswith("zh:")} for s in shared_ids])
        for q in missing:
            ids = subject_identity(q.subject)
            named = {x for x in ids if not x.startswith("zh:")}
            # v2：缺失事实必须带有与共享事实不同的“具名”主体（拉丁实体/别名），
            # 只是中文短语不同（“第一季空投”“持仓”）视为同一事件的信息增量
            if named and shared_named and not (named & shared_named) and \
                    all(subjects_relation(ids, s) == "different" for s in shared_ids):
                return f"标题事实 {q.raw}（主体 {q.subject}）在另一篇中没有对应，且与共享事实主体不同"
    return None


def _exclusive_title_entities(A: Prepared, B: Prepared) -> tuple[list, list]:
    """双方标题中“对方全文（含别名）完全没有”的非通用拉丁实体/cashtag。"""
    def excl(X: Prepared, Y: Prepared) -> list:
        out = []
        for t in sorted(X.facts.title_latin):
            if len(t) < 2 or t in Y.facts.latin_tokens:
                continue
            if _present({ALIASES.get(t, t)}, Y):
                continue
            out.append(t)
        return out
    return excl(A, B), excl(B, A)


def _statement_divergence(A: Prepared, B: Prepared, cfg: dict) -> str | None:
    """同一发言主体的两条标题表态：各自有过半内容单元不见于对方全文 → 不同表态。"""
    sa, sb = A.facts.statement, B.facts.statement
    if not sa or not sb:
        return None
    ra = subjects_relation(subject_identity(sa[0]), subject_identity(sb[0]))
    if ra not in ("same", "related") and sa[0] != sb[0]:
        return None
    ua, ub = claim_units(sa[1]), claim_units(sb[1])
    fa_units, fb_units = claim_units(A.facts.full_norm), claim_units(B.facts.full_norm)
    ea, eb = ua - fb_units, ub - fa_units
    if not ua or not ub:
        return None
    ratio_a, ratio_b = len(ea) / len(ua), len(eb) / len(ub)
    if (ratio_a >= cfg["statement_exclusive_ratio"] and ratio_b >= cfg["statement_exclusive_ratio"]
            and len(ea) >= cfg["statement_min_exclusive_units"] and len(eb) >= cfg["statement_min_exclusive_units"]):
        return (f"同一发言主体（{sa[0]} / {sb[0]}）的两条不同表态：各自有 {ratio_a:.0%} / {ratio_b:.0%} 的内容不见于对方")
    return None


def _slot(status: str, detail: str = "") -> dict:
    return {"status": status, "detail": detail} if detail else {"status": status}


def compare_prepared(A: Prepared, B: Prepared, config: dict | None = None) -> dict:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    fa, fb = A.facts, B.facts
    cos = _cosine(A, B)
    cont = _containment(A, B)
    tj = _jaccard(A.title_c2, B.title_c2)
    exact = A.core_c == B.core_c and len(A.core_c) >= cfg["exact_min_len"]
    gap = _pub_gap_minutes(A, B)

    q = align_quantities(A, B)
    addr_sup, addr_con = _addresses(A, B)
    latin_sup = _shared_latin(A, B, cfg)
    title_rare = sorted(A.title_cjk_rare & B.title_cjk_rare) if A.title_cjk_rare and B.title_cjk_rare else []
    # 允许包含关系：一方的稀有片段是另一方片段的子串
    if not title_rare:
        for x in sorted(A.title_cjk_rare):
            for y in sorted(B.title_cjk_rare):
                if x in y or y in x:
                    title_rare.append(min(x, y, key=len))
    title_rare_sup = [{"type": "rare_title_term", "value": t, "weight": 0.3} for t in title_rare[:2]]

    # 日期/期间
    pub_days = set()
    for P in (A, B):
        if P.pub:
            for delta in (-1, 0, 1):
                d = P.pub + dt.timedelta(days=delta)
                pub_days.add(f"{d.month:02d}-{d.day:02d}")
    da, db = fa.dates - pub_days, fb.dates - pub_days
    date_sup, date_con = [], []
    if da & db:
        date_sup.append({"type": "date", "value": sorted(da & db), "weight": 0.3 if len(da & db) == 1 else 0.5})
    elif da and db:
        date_con.append({"type": "date", "severity": "soft", "detail": f"具体日期不相交 {sorted(da)} vs {sorted(db)}"})
    per_sup, per_con = [], []
    qa_per = {p for p in fa.periods if p.startswith(("q", "ytd", "2")) or "q" in p}
    qb_per = {p for p in fb.periods if p.startswith(("q", "ytd", "2")) or "q" in p}
    if qa_per & qb_per:
        per_sup.append({"type": "period", "value": sorted(qa_per & qb_per), "weight": 0.2})
    elif qa_per and qb_per:
        per_con.append({"type": "period", "severity": "soft", "detail": f"报告期不同 {sorted(qa_per)} vs {sorted(qb_per)}"})

    support = q["matches"] + addr_sup + latin_sup + title_rare_sup + date_sup + per_sup
    anchor_score = round(sum(s.get("weight", 0) for s in support), 3)
    anchor_items = sum(1 for s in support if s.get("weight", 0) >= 0.3)
    strong_items = sum(1 for s in support if s.get("weight", 0) >= cfg["anchor_strong"])
    hard = q["conflicts"] + [c for c in addr_con if c["severity"] == "hard"]
    soft = q["soft_conflicts"] + date_con + per_con

    # 主体
    sid_a, sid_b = fa.title_subject_ids, fb.title_subject_ids
    subj_rel = subjects_relation(sid_a, sid_b)
    title_entities = sorted((fa.title_latin & fb.title_latin) - GENERIC_LATIN)
    # 主体一致的证据：标题主体身份相同/相关，或标题共享稀有拉丁实体/稀有中文片段
    subj_ok = subj_rel in ("same", "related") or bool(title_rare) or any(
        A.latin_df.get(t, 0) <= cfg["semirare_latin_max_df"] for t in title_entities)
    mutual_absent = bool(sid_a and sid_b and subj_rel == "different" and not _present(sid_a, B) and not _present(sid_b, A))
    unknown = list(q["unclear"])
    if not sid_a or not sid_b:
        unknown.append({"fact": "subject", "detail": f"标题主体未识别（{fa.title_subject or '空'} / {fb.title_subject or '空'}），不视为相同"})

    # 动作：标题中的角色/方向
    acts_a = {(x.role, x.direction) for x in A.quantities if x.in_title and x.role != "unknown"}
    acts_b = {(x.role, x.direction) for x in B.quantities if x.in_title and x.role != "unknown"}
    if acts_a & acts_b:
        action_slot = _slot("match", ", ".join(sorted(f"{r}/{d or '-'}" for r, d in acts_a & acts_b)))
    elif any(r1 == r2 and (d1, d2) in OPPOSITE for r1, d1 in acts_a for r2, d2 in acts_b):
        action_slot = _slot("conflict", "同角色方向相反")
    else:
        action_slot = _slot("unknown", "标题动作未对齐或未识别")

    facts_slots = {
        "subject": _slot({"same": "match", "related": "partial", "different": "conflict" if mutual_absent else "unresolved",
                          "unknown": "unknown"}[subj_rel], f"{sorted(sid_a)} vs {sorted(sid_b)}"),
        "action": action_slot,
        "quantities": _slot("conflict" if q["conflicts"] else ("match" if q["matches"] else "unknown"),
                            f"匹配{len(q['matches'])} 冲突{len(q['conflicts'])} 软冲突{len(q['soft_conflicts'])}"),
        "time": _slot("conflict" if (date_con or per_con) else ("match" if (date_sup or per_sup) else "unknown"),
                      f"日期{sorted(da)}|{sorted(db)} 期间{sorted(qa_per)}|{sorted(qb_per)}"),
        "address": _slot("conflict" if addr_con else ("match" if addr_sup else
                         ("unknown" if not (fa.addresses_partial or fa.addresses_full or fb.addresses_partial or fb.addresses_full)
                          else "one_sided"))),
        "venue": _slot("match" if fa.venues & fb.venues else ("different" if fa.venues and fb.venues else "unknown"),
                       f"{sorted(fa.venues)} vs {sorted(fb.venues)}"),
    }

    near_dup = exact or (cont >= cfg["near_dup_containment"])
    excl_a, excl_b = _exclusive_title_entities(A, B)
    stmt_div = _statement_divergence(A, B, cfg)
    codes: list[str] = []
    label, reason = "uncertain", ""

    def done(lbl: str, rsn: str, *cs: str):
        nonlocal label, reason
        label, reason = lbl, rsn
        codes.extend(cs)

    if fa.roundup or fb.roundup:
        if near_dup and not hard and ((fa.roundup and fb.roundup) or tj >= 0.8):
            done("same_event", "同一综述的重复/截取转载：标题一致且正文高度包含", "roundup_duplicate")
        elif anchor_score >= 0.3 or cos >= cfg["text_disjoint"] or q["matches"]:
            done("uncertain", "一方为多事件综述/合集，与另一篇只有部分重叠；综述不用来连接单事件", "roundup_partial")
        else:
            done("different_event", "综述与另一篇没有共享事实且文本差异大", "roundup_disjoint")
    elif hard:
        top = hard[0]
        if anchor_score >= cfg["conflict_override_anchor"] and cos >= cfg["text_high"]:
            done("uncertain", f"存在冲突（{top.get('detail')}：{top.get('left', '')} vs {top.get('right', '')}），"
                              f"但同时有大量共享锚点，信息不足以判定", "conflict_with_strong_support")
        else:
            done("different_event", f"事实冲突：{top.get('detail')}（{top.get('left', '')} vs {top.get('right', '')}）",
                 "hard_conflict")
    elif excl_a and excl_b and anchor_score < cfg["exclusive_entity_max_anchor"]:
        done("different_event", f"同模板不同主体：标题实体 {excl_a} 与 {excl_b} 互不出现在对方全文中，无足够共享事实",
             "exclusive_entity_mismatch")
    elif mutual_absent and anchor_score < cfg["subject_mismatch_max_anchor"]:
        done("different_event", f"标题主体互斥且互不出现（{fa.title_subject} vs {fb.title_subject}），无足够共享事实",
             "subject_mismatch")
    elif stmt_div and anchor_score < cfg["fact_path_anchor_score"]:
        # v4：措辞分歧只说明“无法确认同一”，不是不同事件的证据（v3 把 Bitwise 同一报告、CZ 同一帖子的转述判成了不同）
        done("uncertain", stmt_div + "；措辞分歧不足以证明是不同事件，也无法确认同一", "statement_divergence")
    else:
        partial = _compound_partial(A, B)
        stance_mismatch = fa.stance != fb.stance and "deny" in (fa.stance, fb.stance)
        snapshot_far = (A.snapshot_like and B.snapshot_like and gap is not None
                        and gap > cfg["snapshot_max_pub_gap_minutes"])
        same_ok = None
        if exact:
            same_ok = ("same_event", "剥离样板后正文逐字一致", "exact_core")
        elif near_dup and (anchor_score >= cfg["near_dup_anchor_score"] or subj_ok):
            same_ok = ("same_event", f"正文高度包含（3-gram 包含度 {cont:.2f}），且有共享事实/主体，属转载、删节或增量改写",
                       "near_duplicate")
        elif (anchor_score >= cfg["fact_path_anchor_score"] and anchor_items >= cfg["fact_path_min_items"]
              and cos >= cfg["text_mid"] and not mutual_absent):
            same_ok = ("same_event", f"多个独立事实锚点一致（{anchor_items} 项，强锚点 {strong_items} 项）且文本中等以上相似，无冲突",
                       "multi_anchor")
        elif (anchor_score >= cfg["text_path_anchor_score"] and cos >= cfg["text_high"]
              and (subj_ok or latin_sup)):
            same_ok = ("same_event", f"文本强相似（{cos:.2f}）且主体/稀有实体一致、有事实锚点，无冲突", "text_and_anchor")
        elif (tj >= cfg["title_near_jaccard"] and cos >= cfg["text_mid"] and subj_ok
              and (title_rare or latin_sup or anchor_score >= cfg["text_path_anchor_score"])):
            same_ok = ("same_event", f"标题近乎相同（字符重合 {tj:.2f}）、主体一致且有稀有实体/事实锚点，无冲突",
                       "title_near_duplicate")
        if same_ok and excl_a and excl_b:
            done("uncertain", f"支持证据存在，但双方标题各有对方没有的实体 {excl_a} / {excl_b}", "exclusive_entity_with_support")
        elif same_ok and partial:
            done("uncertain", f"{partial}：复合报道与单项报道只部分重叠，不判为同一事件", "compound_partial")
        elif same_ok and stance_mismatch:
            done("uncertain", "一方为否认/澄清，另一方不是；立场不同，可能是同一事件的不同进展", "stance_mismatch")
        elif same_ok and snapshot_far:
            done("uncertain", f"行情快照类报道发布时间相距 {gap:.0f} 分钟，可能是不同时点快照", "snapshot_time_gap")
        elif same_ok and soft and not near_dup:
            done("uncertain", f"支持证据存在，但有软冲突（{soft[0].get('detail', '')}）", "soft_conflict")
        elif same_ok:
            done(*same_ok)
        elif anchor_score < 0.3 and cos < cfg["text_disjoint"] and not title_rare:
            done("different_event", f"没有共享事实锚点，文本相似度低（{cos:.2f}），无同一事件证据", "disjoint")
        else:
            missing = []
            if anchor_score < cfg["fact_path_anchor_score"]:
                missing.append(f"事实锚点不足（{anchor_score:.2f}）")
            if cos < cfg["text_high"]:
                missing.append(f"文本相似度 {cos:.2f}")
            if not subj_ok:
                missing.append("主体未确认相同")
            done("uncertain", "有部分重叠但证据不足以判定同一事件：" + "，".join(missing), "insufficient")

    res = {
        "label": label,
        "reason": reason,
        "reason_codes": codes,
        "evidence": {
            "support": support,
            "conflict": hard + soft,
            "unknown": unknown,
            "facts": facts_slots,
        },
        "diagnostics": {
            "tfidf_cosine": round(cos, 4),
            "containment_c3": round(cont, 4),
            "title_jaccard_c2": round(tj, 4),
            "exact_core_equal": exact,
            "anchor_score": anchor_score,
            "anchor_items": anchor_items,
            "pub_gap_minutes": None if gap is None else round(gap, 2),
            "roundup": [fa.roundup, fb.roundup],
            "compound_title": [fa.compound_title, fb.compound_title],
            "stance": [fa.stance, fb.stance],
            "title_subjects": [fa.title_subject, fb.title_subject],
            "subject_relation": subj_rel,
            "exclusive_title_entities": [excl_a, excl_b],
            "statement_divergence": stmt_div,
            "subject_agreement": subj_ok,
            "score_note": SCORE_NOTE,
        },
        "comparator": {"name": cfg["name"], "version": VERSION},
    }
    return res


def compare_articles(left: dict, right: dict, config: dict | None = None) -> dict:
    """可独立调用的比较入口：只读取白名单字段。"""
    from .snapshot import COMPARATOR_FIELDS
    lv = {k: left[k] for k in COMPARATOR_FIELDS if k in left}
    rv = {k: right[k] for k in COMPARATOR_FIELDS if k in right}
    for v in (lv, rv):
        for k in ("id", "title", "content"):
            if k not in v:
                raise ValueError(f"比较输入缺少必要字段 {k}")
    res = compare_prepared(prepare(lv, config), prepare(rv, config), config)
    return validate_result(res)
