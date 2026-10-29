"""无标签统计（补充标签覆盖不到的部分），全部由回放输出与原始快照程序化计算。

1. 正文逐字一致（剥离样板后 ≥20 字）的稿件对：窗口内多少最终不同组；窗口外（距离 >50）多少从未被比较；
2. 综述桥接：被识别为综述的文章所在多成员组里，是否混入非综述文章；
3. 实体/主体互斥规则判 different 的对按文本相似度分桶，导出相似度最高的若干对供核读。

  python3 tools/label_free_stats.py --run runs/fact_v4_1_1 --input <快照.json> --out reports/label_free_stats.json
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dedup import textnorm as tn  # noqa: E402
from dedup.facts import extract_facts  # noqa: E402
from dedup.snapshot import comparator_view, load_snapshot  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--window", type=int, default=50)
    args = ap.parse_args()
    snap = load_snapshot(args.input)
    group, phase = {}, {}
    for line in open(f"{args.run}/assignments.jsonl", encoding="utf-8"):
        a = json.loads(line)
        group[a["article_id"]] = a["group_id"]
        phase[a["article_id"]] = a["phase"]
    rng = range(snap.warmup_start, snap.main_end)

    # 1. 逐字一致
    by_core = collections.defaultdict(list)
    for i in rng:
        core, _ = tn.strip_boilerplate(tn.normalize_light(snap.articles[i]["content"]))
        c = tn.compact(core)
        if len(c) >= 20:
            by_core[c].append(i)
    within = within_split = beyond = 0
    beyond_examples = []
    for idx in by_core.values():
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                i, j = idx[x], idx[y]
                if j < snap.main_start:
                    continue
                a, b = snap.articles[i], snap.articles[j]
                if j - i > args.window:
                    beyond += 1
                    if len(beyond_examples) < 10:
                        beyond_examples.append({"distance": j - i, "title": a["title"][:50],
                                                "collected": [a["collected"], b["collected"]]})
                else:
                    within += 1
                    within_split += group[a["id"]] != group[b["id"]]

    # 2. 综述桥接
    members = collections.defaultdict(list)
    for aid, g in group.items():
        members[g].append(aid)
    by_id = {a["id"]: a for a in snap.articles}
    roundup = {aid: extract_facts(comparator_view(by_id[aid])).roundup for aid in group}
    roundup_articles = [aid for aid in group if roundup[aid]]
    bridged = []
    for aid in roundup_articles:
        ms = members[group[aid]]
        others = [m for m in ms if not roundup[m]]
        if len(ms) > 1 and others:
            bridged.append({"group_id": group[aid], "roundup": by_id[aid]["title"][:40],
                            "non_roundup_members": [by_id[m]["title"][:40] for m in others]})

    # 3. 实体/主体互斥判 different 的相似度分桶
    buckets = collections.Counter()
    top = []
    for line in open(f"{args.run}/pairs.jsonl", encoding="utf-8"):
        r = json.loads(line)
        if not r["main_eval"] or not r["reason_codes"]:
            continue
        code = r["reason_codes"][0]
        if code not in ("exclusive_entity_mismatch", "subject_mismatch"):
            continue
        cos = r["diagnostics"]["tfidf_cosine"]
        buckets[(code, ">=0.5" if cos >= 0.5 else ("0.3-0.5" if cos >= 0.3 else "<0.3"))] += 1
        if cos >= 0.3:
            top.append({"cos": cos, "code": code, "left": by_id[r["left_id"]]["title"][:50],
                        "right": by_id[r["right_id"]]["title"][:50],
                        "exclusive": r["diagnostics"].get("exclusive_title_entities")})
    top.sort(key=lambda x: -x["cos"])
    res = {
        "identical_core_pairs": {"within_window": within, "within_window_final_split": within_split,
                                 "beyond_window_never_compared": beyond, "beyond_examples": beyond_examples},
        "roundup": {"roundup_articles_replayed": len(roundup_articles),
                    "roundup_in_main": sum(1 for a in roundup_articles if phase[a] == "main"),
                    "groups_mixing_roundup_with_non_roundup": len(bridged), "details": bridged},
        "entity_subject_rejections_by_similarity": {f"{k[0]}|{k[1]}": v for k, v in sorted(buckets.items())},
        "entity_subject_rejections_cos_ge_0_3": top,
    }
    json.dump(res, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "entity_subject_rejections_cos_ge_0_3"}, ensure_ascii=False, indent=1))
    print("cos>=0.3 rejections:", len(top))


if __name__ == "__main__":
    main()
