"""从真实快照的严格窗口配对中抽取待核读候选对（与比较器无关的简单特征分层抽样）。

只使用：标题字符 2-gram Jaccard、正文字符 3-gram 包含度、共享数字串、标题模板骨架、综述栏目词、随机。
不调用任何比较器，不读 story_id，避免“用算法挑标签再用标签证明算法”。
候选 ≠ 已标正负例：候选只是待人工/模型辅助核读的列表。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dedup.snapshot import load_snapshot  # noqa: E402

ROUNDUP_KW = re.compile(r"早讯|午讯|晚讯|早报|晚报|要闻|重要资讯|合集|一览|汇总|盘点|全文整理")


def simple(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s).lower())


def grams(s: str, n: int) -> set:
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def skeleton(title: str) -> str:
    t = simple(title)
    t = re.sub(r"\d[\d,.]*", "#", t)
    t = re.sub(r"[a-z][a-z0-9.\-]*", "A", t)
    return t


def numbers(s: str) -> set:
    return {x.replace(",", "") for x in re.findall(r"\d[\d,]*\.?\d*", unicodedata.normalize("NFKC", s))
            if len(x.replace(",", "").replace(".", "")) >= 3}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20261029)
    ap.add_argument("--exclude-labels", nargs="*", default=[])
    args = ap.parse_args()

    snap = load_snapshot(args.input)
    arts = snap.articles
    exclude = set()
    for p in args.exclude_labels:
        data = json.load(open(p, encoding="utf-8"))
        for x in data.get("pairs", []):
            exclude.add(frozenset((x["left_id"], x["right_id"])))

    feats = {}
    for i in range(snap.warmup_start, snap.main_end):
        a = arts[i]
        t, c = simple(a["title"]), simple(a["content"])
        feats[i] = {"t2": grams(t, 2), "c3": grams(c, 3), "num": numbers(a["title"] + " " + a["content"]),
                    "sk": skeleton(a["title"]), "roundup": bool(ROUNDUP_KW.search(a["title"]))}

    rows = []
    for j in range(snap.main_start, snap.main_end):
        for i in range(max(snap.warmup_start, j - 50), j):
            if frozenset((arts[i]["id"], arts[j]["id"])) in exclude:
                continue
            fi, fj = feats[i], feats[j]
            tj = len(fi["t2"] & fj["t2"]) / max(1, len(fi["t2"] | fj["t2"]))
            cont = len(fi["c3"] & fj["c3"]) / max(1, min(len(fi["c3"]), len(fj["c3"])))
            ns = len(fi["num"] & fj["num"])
            rows.append({"i": i, "j": j, "tj": round(tj, 3), "cont": round(cont, 3), "nshared": ns,
                         "same_skeleton": fi["sk"] == fj["sk"], "roundup": fi["roundup"] or fj["roundup"]})

    strata = {
        "S1_近乎全同": lambda r: r["tj"] >= 0.9 or r["cont"] >= 0.95,
        "S2_标题中高相似": lambda r: 0.5 <= r["tj"] < 0.9 and r["cont"] < 0.95,
        "S3_标题低相似但正文/数字重叠": lambda r: r["tj"] < 0.5 and r["cont"] < 0.95 and (r["cont"] >= 0.4 or r["nshared"] >= 2),
        "S4_同标题模板骨架": lambda r: r["same_skeleton"] and r["tj"] < 0.9 and r["cont"] < 0.95,
        "S5_综述相关": lambda r: r["roundup"] and (r["nshared"] >= 1 or r["cont"] >= 0.2),
        "S6_共享数字但文本低": lambda r: r["nshared"] >= 1 and r["cont"] < 0.3 and r["tj"] < 0.3,
        "S7_随机": lambda r: True,
    }
    quota = {"S1_近乎全同": 25, "S2_标题中高相似": 40, "S3_标题低相似但正文/数字重叠": 40, "S4_同标题模板骨架": 30,
             "S5_综述相关": 20, "S6_共享数字但文本低": 20, "S7_随机": 25}
    rng = random.Random(args.seed)
    chosen, used = [], set()
    pool_sizes = {}
    for name, pred in strata.items():
        pool = [r for r in rows if pred(r) and (r["i"], r["j"]) not in used]
        pool_sizes[name] = len(pool)
        rng.shuffle(pool)
        for r in pool[: quota[name]]:
            used.add((r["i"], r["j"]))
            chosen.append({**r, "stratum": name})
    with open(args.out, "w", encoding="utf-8") as f:
        for r in chosen:
            a, b = arts[r["i"]], arts[r["j"]]
            f.write(json.dumps({
                "left_id": a["id"], "right_id": b["id"], "left_seq": r["i"], "right_seq": r["j"],
                "distance": r["j"] - r["i"], "sampling_stratum": r["stratum"],
                "sampling_features": {k: r[k] for k in ("tj", "cont", "nshared", "same_skeleton", "roundup")},
            }, ensure_ascii=False) + "\n")
    print(json.dumps({"main_window_pairs": len(rows), "pool_sizes": pool_sizes, "chosen": len(chosen),
                      "seed": args.seed}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
