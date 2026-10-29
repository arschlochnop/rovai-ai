"""严格窗口回放：逐篇比较前序窗口全部文章（不做 top-k），再交给顺序归组。

输出：
- pairs.jsonl：每次比较一行（文章 ID、前序距离、主评测标记、三态、理由、最终事实证据、基础诊断、归组结局）；
- assignments.jsonl：每篇文章的入组决定与全部窗口支持成员；
- 计时：预处理（单篇特征抽取，相当于“编码”成本）、配对比较、归组分开计。
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict

from .contract import validate_result
from .grouping import SequentialGrouper
from .snapshot import Snapshot, comparator_view


def _same_pair_outcome(left_gid: str, dec, labels_left: str) -> str:
    if labels_left != "same_event":
        return "n/a"
    if dec.action == "join" and dec.group_id == left_gid:
        return "joined_same_group"
    if dec.action == "ambiguous_new" and any(q["group_id"] == left_gid for q in dec.qualifying_groups):
        return "split_ambiguous_multiple_groups"
    if any(b["group_id"] == left_gid for b in dec.blocked_groups):
        return "split_other_members_insufficient"
    return "split_other"


def run_replay(snap: Snapshot, comparator, config: dict, out_dir: str, *, window: int = 50,
               start: int | None = None, log=print) -> dict:
    """start 为回放起点下标（默认预热段起点）。返回计时与计数。"""
    start = snap.warmup_start if start is None else start
    indices = list(range(start, snap.main_end))
    has_prepare = hasattr(comparator, "prepare") and hasattr(comparator, "compare_prepared")
    grouper = SequentialGrouper(window_size=window)
    prepared: dict[str, object] = {}
    t = {"prepare_s": {"warmup": 0.0, "main": 0.0, "pre": 0.0}, "compare_s": {"warmup": 0.0, "main": 0.0, "pre": 0.0},
         "grouping_s": {"warmup": 0.0, "main": 0.0, "pre": 0.0}, "write_s": 0.0}
    counts = {"pairs": {"warmup": 0, "main": 0, "pre": 0}, "articles": {"warmup": 0, "main": 0, "pre": 0},
              "labels_main": {"same_event": 0, "different_event": 0, "uncertain": 0},
              "labels_warmup": {"same_event": 0, "different_event": 0, "uncertain": 0},
              "actions_main": {"join": 0, "new": 0, "ambiguous_new": 0},
              "actions_warmup": {"join": 0, "new": 0, "ambiguous_new": 0}}
    seq_of = {}
    pair_id = 0
    pf = open(f"{out_dir}/pairs.jsonl", "w", encoding="utf-8")
    af = open(f"{out_dir}/assignments.jsonl", "w", encoding="utf-8")
    try:
        for pos, idx in enumerate(indices):
            art = snap.articles[idx]
            aid = art["id"]
            phase = "main" if snap.is_main(idx) else ("warmup" if idx >= snap.warmup_start else "pre")
            counts["articles"][phase] += 1
            view = comparator_view(art)
            t0 = time.perf_counter()
            if has_prepare:
                prepared[aid] = comparator.prepare(view, config)
            t["prepare_s"][phase] += time.perf_counter() - t0

            win = list(grouper.window)
            results = []
            t0 = time.perf_counter()
            for lseq, lid in win:
                if has_prepare:
                    res = comparator.compare_prepared(prepared[lid], prepared[aid], config)
                else:
                    res = comparator.compare_articles(comparator_view(snap.articles[lseq]), view, config)
                validate_result(res)
                results.append((lseq, lid, res))
            t["compare_s"][phase] += time.perf_counter() - t0

            t0 = time.perf_counter()
            labels = {lid: res["label"] for _, lid, res in results}
            left_groups = {lid: grouper.group_of[lid] for _, lid, _ in results}
            dec = grouper.decide(aid, labels)
            grouper.admit(idx, aid)
            seq_of[aid] = idx
            t["grouping_s"][phase] += time.perf_counter() - t0
            counts[f"actions_{phase}" if phase != "pre" else "actions_warmup"][dec.action] += 1

            t0 = time.perf_counter()
            for lseq, lid, res in results:
                pair_id += 1
                counts["pairs"][phase] += 1
                if phase in ("main", "warmup"):
                    counts[f"labels_{phase}"][res["label"]] += 1
                left = snap.articles[lseq]
                rec = {
                    "pair_id": pair_id,
                    "right_id": aid, "left_id": lid,
                    "right_seq": idx, "left_seq": lseq, "distance": idx - lseq,
                    "phase": phase, "main_eval": phase == "main",
                    "same_source": left["osint_source_id"] == art["osint_source_id"],
                    "label": res["label"], "reason": res["reason"], "reason_codes": res.get("reason_codes", []),
                    "final_evidence": res["evidence"],
                    "diagnostics": res["diagnostics"],
                    "grouping": {
                        "left_group": left_groups[lid], "right_group": dec.group_id,
                        "same_final_group": left_groups[lid] == dec.group_id,
                        "same_pair_outcome": _same_pair_outcome(left_groups[lid], dec, res["label"]),
                    },
                    "comparator": res["comparator"],
                }
                pf.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            arec = {
                "article_id": aid, "seq": idx, "replay_pos": pos, "phase": phase,
                "action": dec.action, "group_id": dec.group_id,
                "support_member_ids": dec.support_member_ids,
                "qualifying_groups": dec.qualifying_groups,
                "blocked_groups": dec.blocked_groups,
                "window_size": len(dec.window_ids),
                "window_first_seq": win[0][0] if win else None, "window_last_seq": win[-1][0] if win else None,
                "same_left_ids": [lid for _, lid, res in results if res["label"] == "same_event"],
            }
            af.write(json.dumps(arec, ensure_ascii=False) + "\n")
            t["write_s"] += time.perf_counter() - t0
            if (pos + 1) % 250 == 0:
                log(f"  回放 {pos + 1}/{len(indices)}")
    finally:
        pf.close()
        af.close()
    return {"timings": t, "counts": counts, "replay_start": start, "replay_end": snap.main_end,
            "window": window, "group_count": grouper.next_gid - 1}
