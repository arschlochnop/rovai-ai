"""生成人工复核模板（盲审版 + 答案键 + 组纯度复核表）。

盲审版不含模型核读标签与算法输出，顺序打乱，避免锚定；复核完成后用答案键计算一致性。
包含三部分：
  A. 196 对模型辅助开发核读标签的独立人工复核；
  B. 未标注候选（仅候选，不是已标正负例）：最终版判 same / uncertain / 高相似 different 的分层随机样本，
     用于估计标签集之外的误合并与漏聚合；
  C. 最终版全部触及主评测的多成员临时组（组纯度复核）。

  python3 tools/review_template.py --run runs/fact_v4_1_1 --input <快照.json> --labels labels/dev_labels_v1.jsonl \
      --public-pairs <公开115对.json> --out-dir labels/human_review
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dedup.snapshot import load_snapshot  # noqa: E402

HUMAN_COLS = ["human_label(same_event/different_event/uncertain)", "human_reason", "reviewer", "reviewed_at_utc"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--public-pairs", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--seed", type=int, default=20261030)
    args = ap.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=False)
    snap = load_snapshot(args.input)
    by_id = {a["id"]: a for a in snap.articles}
    rng = random.Random(args.seed)

    labeled = set()
    dev = [json.loads(l) for l in open(args.labels, encoding="utf-8")]
    for l in dev:
        labeled.add(frozenset((l["left_id"], l["right_id"])))
    for p in json.load(open(args.public_pairs, encoding="utf-8"))["pairs"]:
        labeled.add(frozenset((p["left_id"], p["right_id"])))

    pairs = {}
    pools = collections.defaultdict(list)
    with open(f"{args.run}/pairs.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            k = frozenset((r["left_id"], r["right_id"]))
            pairs[k] = {"label": r["label"], "reason": r["reason"], "codes": r["reason_codes"]}
            if not r["main_eval"] or k in labeled:
                continue
            if r["label"] == "same_event":
                pools["B1_算法判same"].append(r)
            elif r["label"] == "uncertain":
                pools["B2_算法判uncertain"].append(r)
            elif r["diagnostics"].get("tfidf_cosine", 0) >= 0.3:
                pools["B3_算法判different但文本较相似"].append(r)
    quota = {"B1_算法判same": 80, "B2_算法判uncertain": 40, "B3_算法判different但文本较相似": 30}

    items = []
    for l in dev:
        items.append({"section": "A_开发核读标签复核", "left_id": l["left_id"], "right_id": l["right_id"],
                      "key": {"model_read_label": l["label"], "model_read_reason": l["reason"],
                              "categories": "|".join(l["categories"]), "label_id": l["label_id"]}})
    for name, pool in pools.items():
        rng.shuffle(pool)
        for r in pool[: quota[name]]:
            items.append({"section": f"B_未标注候选·{name}", "left_id": r["left_id"], "right_id": r["right_id"],
                          "key": {"note": "仅候选，不是已标正负例"}})
    rng.shuffle(items)

    with open(out / "pair_review_blind.csv", "w", newline="", encoding="utf-8-sig") as fb, \
            open(out / "pair_review_key.csv", "w", newline="", encoding="utf-8-sig") as fk:
        wb, wk = csv.writer(fb), csv.writer(fk)
        wb.writerow(["review_id", "left_id", "left_source", "left_published", "left_title", "left_content", "left_link",
                     "right_id", "right_source", "right_published", "right_title", "right_content", "right_link"] + HUMAN_COLS)
        wk.writerow(["review_id", "section", "left_id", "right_id", "model_read_label", "model_read_reason",
                     "categories", "dev_label_id", "algorithm_label_fact_v4_1_1", "algorithm_reason", "algorithm_codes", "note"])
        for n, it in enumerate(items, 1):
            rid = f"R{n:04d}"
            a, b = sorted((by_id[it["left_id"]], by_id[it["right_id"]]), key=lambda x: (x["collected"], x["id"]))
            wb.writerow([rid, a["id"], a["source_name"], a["published"], a["title"], a["content"], a["link"],
                         b["id"], b["source_name"], b["published"], b["title"], b["content"], b["link"]] + [""] * 4)
            p = pairs.get(frozenset((a["id"], b["id"])), {})
            k = it["key"]
            wk.writerow([rid, it["section"], a["id"], b["id"], k.get("model_read_label", ""), k.get("model_read_reason", ""),
                         k.get("categories", ""), k.get("label_id", ""), p.get("label", "未直接比较"), p.get("reason", ""),
                         "|".join(p.get("codes", [])), k.get("note", "")])

    groups = collections.defaultdict(list)
    phase = {}
    with open(f"{args.run}/assignments.jsonl", encoding="utf-8") as f:
        for line in f:
            a = json.loads(line)
            groups[a["group_id"]].append(a["article_id"])
            phase[a["article_id"]] = a["phase"]
    with open(out / "group_review.csv", "w", newline="", encoding="utf-8-sig") as fg:
        w = csv.writer(fg)
        w.writerow(["group_id", "size", "members(序号|来源|发布时间|标题)", "member_ids",
                    "组是否纯净(是/否/不确定)", "混入成员ID", "应同组但被拆到别组的线索", "reviewer", "reviewed_at_utc"])
        seq = {a["id"]: i for i, a in enumerate(snap.articles)}
        for gid, ms in groups.items():
            if len(ms) < 2 or not any(phase[m] == "main" for m in ms):
                continue
            desc = "\n".join(f"{seq[m]}|{by_id[m]['source_name']}|{by_id[m]['published']}|{by_id[m]['title']}" for m in ms)
            w.writerow([gid, len(ms), desc, "|".join(ms), "", "", "", "", ""])
    summary = {
        "seed": args.seed,
        "pair_items": collections.Counter(it["section"] for it in items),
        "candidate_pool_sizes": {k: len(v) for k, v in pools.items()},
        "files": ["pair_review_blind.csv（交给复核人，不含任何模型/算法标签）",
                  "pair_review_key.csv（复核完成后再打开，计算一致性）",
                  "group_review.csv（最终版多成员组纯度复核）"],
        "note": "B 部分是从算法输出分层随机抽取的候选，在人工给出标签前不是正负例；A 部分的模型核读标签不是独立金标。",
    }
    json.dump(summary, open(out / "README.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
