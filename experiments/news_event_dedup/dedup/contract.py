"""比较器契约：模块必须声明 VERSION 与 DEFAULT_CONFIG；输出必须是合法三态并带非空理由。

违反契约直接抛出 ContractError，回放立即停止，不做静默修正。
"""
from __future__ import annotations

LABELS = ("same_event", "different_event", "uncertain")
REQUIRED_EVIDENCE_KEYS = ("support", "conflict", "unknown", "facts")
REQUIRED_FACT_SLOTS = ("subject", "action", "quantities", "time", "address")


class ContractError(RuntimeError):
    pass


def validate_module(mod) -> None:
    version = getattr(mod, "VERSION", None)
    if not isinstance(version, str) or not version.strip():
        raise ContractError(f"{mod.__name__} 缺少非空 VERSION 声明")
    cfg = getattr(mod, "DEFAULT_CONFIG", None)
    if not isinstance(cfg, dict) or not cfg.get("name"):
        raise ContractError(f"{mod.__name__} 缺少 DEFAULT_CONFIG 或其中没有 name")
    if not callable(getattr(mod, "compare_articles", None)):
        raise ContractError(f"{mod.__name__} 没有可调用的 compare_articles")


def validate_result(res: dict) -> dict:
    if not isinstance(res, dict):
        raise ContractError("比较结果不是对象")
    label = res.get("label")
    if label not in LABELS:
        raise ContractError(f"非法 label: {label!r}")
    reason = res.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ContractError("reason 为空")
    ev = res.get("evidence")
    if not isinstance(ev, dict) or any(k not in ev for k in REQUIRED_EVIDENCE_KEYS):
        raise ContractError(f"evidence 缺少字段，需要 {REQUIRED_EVIDENCE_KEYS}")
    facts = ev["facts"]
    for slot in REQUIRED_FACT_SLOTS:
        if slot not in facts:
            raise ContractError(f"evidence.facts 缺少槽位 {slot}（未识别也必须显式标 unknown）")
    diag = res.get("diagnostics")
    if not isinstance(diag, dict) or "score_note" not in diag:
        raise ContractError("diagnostics 缺少 score_note（分数只是证据，不是同事件概率）")
    comp = res.get("comparator")
    if not isinstance(comp, dict) or not comp.get("version") or not comp.get("name"):
        raise ContractError("结果缺少 comparator 名称/版本")
    return res
