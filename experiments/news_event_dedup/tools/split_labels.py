"""按“事件家族”把开发核读标签分成 dev / selfheld（实现者自留验证）两部分。

家族构造（与比较器无关）：
- 同一标注对的两篇文章连边；
- 任意两篇被标注文章若标题字符 2-gram Jaccard ≥ 0.45 或正文 3-gram 包含度 ≥ 0.6，也连边（同事件不同对合并）；
- 连通分量即家族；家族内所有标注对整体进入同一部分，文章不跨部分。
凡触及用户公开标签文章的家族一律留在 dev（实现者已研读过这些文章）。
selfheld 仍是实现者读过原文后给出的标签，只能叫“自留验证（非独立）”，不能称未见验证。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dedup.snapshot import load_snapshot  # noqa: E402


def simple(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s).lower())


def grams(s: str, n: int) -> set:
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--public-labels", nargs="*", default=[])
    ap.add_argument("--out", required=True)
    ap.add_argument("--selfheld-ratio", type=float, default=0.4)
    args = ap.parse_args()

    snap = load_snapshot(args.input)
    by_id = {a["id"]: a for a in snap.articles}
    labels = [json.loads(l) for l in open(args.labels, encoding="utf-8")]
    public_ids = set()
    for p in args.public_labels:
        for x in json.load(open(p, encoding="utf-8"))["pairs"]:
            public_ids.update((x["left_id"], x["right_id"]))

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    ids = sorted({x for l in labels for x in (l["left_id"], l["right_id"])})
    for l in labels:
        union(l["left_id"], l["right_id"])
    feats = {i: (grams(simple(by_id[i]["title"]), 2), grams(simple(by_id[i]["content"]), 3)) for i in ids}
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            ta, ca = feats[ids[a]]
            tb, cb = feats[ids[b]]
            tj = len(ta & tb) / max(1, len(ta | tb))
            ct = len(ca & cb) / max(1, min(len(ca), len(cb)))
            if tj >= 0.45 or ct >= 0.6:
                union(ids[a], ids[b])

    fam_pairs: dict[str, list] = {}
    for l in labels:
        fam_pairs.setdefault(find(l["left_id"]), []).append(l)
    fam_articles: dict[str, set] = {}
    for i in ids:
        fam_articles.setdefault(find(i), set()).add(i)

    assignment, fam_info = {}, []
    eligible = []
    for fam, ps in fam_pairs.items():
        touches_public = bool(fam_articles[fam] & public_ids)
        h = int(hashlib.sha256(fam.encode()).hexdigest(), 16) % 1000
        fam_info.append({"family": fam, "pairs": len(ps), "articles": len(fam_articles[fam]),
                         "touches_public": touches_public, "hash": h})
        if not touches_public:
            eligible.append((h, fam))
    eligible.sort()
    total = len(labels)
    target = args.selfheld_ratio * total
    chosen, acc = set(), 0
    for h, fam in eligible:
        if acc >= target:
            break
        # 单个巨型家族超过目标的一半时跳过，避免自留集被一个家族主导
        if len(fam_pairs[fam]) > target * 0.5:
            continue
        chosen.add(fam)
        acc += len(fam_pairs[fam])
    for fam, ps in fam_pairs.items():
        part = "selfheld" if fam in chosen else "dev"
        for l in ps:
            assignment[l["label_id"]] = {"part": part, "family": fam}
    # 校验：文章不跨部分
    art_part: dict[str, str] = {}
    for l in labels:
        p = assignment[l["label_id"]]["part"]
        for x in (l["left_id"], l["right_id"]):
            if art_part.setdefault(x, p) != p:
                sys.exit(f"文章 {x} 同时出现在 dev 与 selfheld")
    summary = {
        "families": len(fam_pairs), "selfheld_families": len(chosen),
        "pairs": {p: sum(1 for v in assignment.values() if v["part"] == p) for p in ("dev", "selfheld")},
        "labels_by_part": {p: {lab: sum(1 for l in labels if assignment[l["label_id"]]["part"] == p and l["label"] == lab)
                               for lab in ("same_event", "different_event", "uncertain")} for p in ("dev", "selfheld")},
        "families_touching_public_labels": sum(1 for f in fam_info if f["touches_public"]),
        "note": "selfheld 为实现者自留（已读原文、非独立），只能作开发诊断，不是未见验证",
    }
    json.dump({"summary": summary, "assignment": assignment, "families": fam_info},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
