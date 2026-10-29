"""对一次回放输出做标签评测（回放本身不读标签）。

  python3 run_eval.py --run runs/fact_v1 --input <快照.json> \
      --public-pairs <公开115对.json> --topics <4类主题.json> \
      --dev-labels labels/dev_labels_v1.jsonl --dev-split labels/dev_split_v1.json
输出写入 <run>/eval/（目录必须不存在，评分后修订须另开回放目录）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dedup import comparator_exact, comparator_fact  # noqa: E402
from dedup.background import Background  # noqa: E402
from dedup.evaluate import (RunIndex, check_labels, contaminated_groups, evaluate_set,  # noqa: E402
                            load_jsonl_labels, load_public_pairs, topic_group_eval)
from dedup.snapshot import comparator_view, load_snapshot, sha256_file  # noqa: E402


def strip_rows(res: dict) -> dict:
    return {k: v for k, v in res.items() if k not in ("rows", "group_rows")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--public-pairs")
    ap.add_argument("--topics")
    ap.add_argument("--dev-labels")
    ap.add_argument("--dev-split")
    ap.add_argument("--out-name", default="eval")
    args = ap.parse_args()

    run_dir = Path(args.run)
    out = run_dir / args.out_name
    if out.exists():
        sys.exit(f"评测目录已存在，拒绝覆盖：{out}")
    out.mkdir()
    manifest = json.load(open(run_dir / "manifest.json", encoding="utf-8"))
    snap = load_snapshot(args.input)
    if snap.sha256 != manifest["input"]["sha256"]:
        sys.exit("输入快照 hash 与回放 manifest 不一致")
    by_id = {a["id"]: a for a in snap.articles}
    known = set(by_id)
    run = RunIndex(str(run_dir))

    # 回放中未直接比较的对：可选地直接调用同一比较器（同配置、同背景表）单列报告
    comp_name = manifest["comparator"]["config"]["name"]
    comp = comparator_fact if comp_name == "fact_enhanced" else comparator_exact
    cfg = dict(manifest["comparator"]["config"])
    if manifest.get("background"):
        with gzip.open(run_dir / manifest["background"]["file"], "rt", encoding="utf-8") as f:
            b = json.load(f)
        cfg["background"] = Background(n_docs=b["n_docs"], ngram_df=b["ngram_df"], latin_df=b["latin_df"],
                                       source_desc=b["source"])

    def direct(lid: str, rid: str) -> dict:
        a, b = sorted((by_id[lid], by_id[rid]), key=lambda x: (x["collected"], x["id"]))
        return comp.compare_articles(comparator_view(a), comparator_view(b), cfg)

    results, label_hashes, all_sets = {}, {}, []
    if args.public_pairs:
        pub = load_public_pairs(args.public_pairs, "public")
        check_labels(pub, known, "public")
        label_hashes["public_pairs"] = {"path": args.public_pairs, "sha256": sha256_file(args.public_pairs)}
        results["public_115"] = evaluate_set(pub, run, "公开参考标签（用户提供，已披露，非盲测）", direct=direct)
        all_sets.append(pub)
    if args.dev_labels:
        dev = load_jsonl_labels(args.dev_labels)
        check_labels(dev, known, "dev")
        label_hashes["dev_labels"] = {"path": args.dev_labels, "sha256": sha256_file(args.dev_labels)}
        all_sets.append(dev)
        if args.dev_split:
            split = json.load(open(args.dev_split, encoding="utf-8"))["assignment"]
            label_hashes["dev_split"] = {"path": args.dev_split, "sha256": sha256_file(args.dev_split)}
            for part in ("dev", "selfheld"):
                sub = [l for l in dev if split[l["label_id"]]["part"] == part]
                results[f"model_read_{part}"] = evaluate_set(
                    sub, run, f"模型辅助开发核读标签·{part}（实现者已读原文，非独立人工金标）")
        results["model_read_all"] = evaluate_set(dev, run, "模型辅助开发核读标签·全部")
    topics = topic_group_eval(args.topics, run) if args.topics else None
    if args.topics:
        label_hashes["topics"] = {"path": args.topics, "sha256": sha256_file(args.topics)}
    bad = contaminated_groups(run, all_sets)
    for g in bad:
        g["member_titles"] = [{"id": m, "seq": run.seq_of[m], "source": by_id[m]["source_name"],
                               "title": by_id[m]["title"]} for m in g["members"]]

    report = {
        "run": str(run_dir), "evaluated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "comparator": manifest["comparator"]["version"], "label_files": label_hashes,
        "honesty_notes": [
            "公开 115 对与 4 类主题是用户提供的已公开开发参考，不是盲测；",
            "model_read_* 由实现者阅读原文给出（模型辅助开发核读），selfheld 只是按事件家族自留、实现者同样读过原文，不是未见验证；",
            "小样本分层结果不能外推全天准确率；本批未出现误合并不代表全量为零。",
        ],
        "results": {k: strip_rows(v) for k, v in results.items()},
        "topic_groups": topics,
        "contaminated_groups": bad,
    }
    json.dump(report, open(out / "eval_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    with open(out / "eval_rows.jsonl", "w", encoding="utf-8") as f:
        for k, v in results.items():
            for r in v["rows"]:
                f.write(json.dumps({"set": k, **r}, ensure_ascii=False) + "\n")
            for r in v["group_rows"]:
                f.write(json.dumps({"set": k, "group_level": True, **r}, ensure_ascii=False) + "\n")
    for k, v in results.items():
        p = v["pair_level_replay"]
        g = v["group_level"]
        print(f"{k}: n={v['n_labels']} compared={p['compared']} | 正例 命中{p['positives']['hit_same']}"
              f"/误拒{p['positives']['explicit_reject_different']}/弃权{p['positives']['abstain_uncertain']}"
              f" | 负例 误判same{p['negatives']['false_same']}/明确different{p['negatives']['explicit_different']}"
              f"/弃权{p['negatives']['abstain_uncertain']} | 金标uncertain {p['gold_uncertain']}"
              f" || 组：正例同组{g['positives']['same_group']}/分裂{g['positives']['split']}"
              f" 负例污染{g['negatives']['together_contamination']}")
    print("污染组：", [(g["group_id"], g["negative_label_ids"]) for g in bad])


if __name__ == "__main__":
    main()
