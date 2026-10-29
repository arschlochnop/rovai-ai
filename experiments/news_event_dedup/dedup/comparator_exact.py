"""精确基线比较器。

只认“剥离通用样板后正文完全一致”（含标题一致或不一致两种情形）为 same_event；
其余一律 uncertain——精确匹配本身没有任何“不同事件”的证据，因此不输出 different_event。
"""
from __future__ import annotations

from . import textnorm as tn

VERSION = "exact-1.0.0"
DEFAULT_CONFIG = {
    "name": "exact_baseline",
    # 正文过短时（例如只有一句行情模板）也照样比较；该值只防止空正文误判
    "min_core_len": 6,
}

_SCORE_NOTE = "精确基线不计算相似度分数；same 仅表示规范化正文逐字一致"


def _core(view: dict) -> tuple[str, str]:
    title = tn.compact(tn.strip_title(tn.normalize(view["title"])))
    core, _ = tn.strip_boilerplate(tn.normalize_light(view["content"]))
    return title, tn.compact(core)


def compare_articles(left: dict, right: dict, config: dict | None = None) -> dict:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    tl, cl = _core(left)
    tr, cr = _core(right)
    same_core = cl == cr and len(cl) >= cfg["min_core_len"]
    same_title = tl == tr
    unknown_slots = {s: "unknown" for s in ("subject", "action", "quantities", "time", "address")}
    if same_core:
        label = "same_event"
        reason = "剥离通用样板后正文逐字一致" + ("，标题也一致" if same_title else "，标题不同（改写标题）")
        support = [{"type": "exact_core", "detail": f"正文一致（{len(cl)} 字）", "title_equal": same_title}]
        codes = ["exact_core_equal"]
    else:
        label = "uncertain"
        reason = "正文不逐字一致；精确基线无法判断是否同一事件，也没有不同事件的证据" + (
            "（标题逐字一致但正文不同）" if same_title else "")
        support = []
        codes = ["not_exact"] + (["title_equal_only"] if same_title else [])
    return {
        "label": label,
        "reason": reason,
        "reason_codes": codes,
        "evidence": {"support": support, "conflict": [], "unknown": [{"fact": "all", "detail": "精确基线不抽取事实"}],
                     "facts": unknown_slots},
        "diagnostics": {"exact_core_equal": same_core, "exact_title_equal": same_title, "score_note": _SCORE_NOTE},
        "comparator": {"name": cfg["name"], "version": VERSION},
    }
