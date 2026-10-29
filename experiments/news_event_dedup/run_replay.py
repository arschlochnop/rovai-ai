"""严格前 50 篇窗口回放命令行入口。

示例：
  python3 run_replay.py --input <快照.json> --comparator fact --out runs/fact_v1
输出目录必须不存在（不覆盖旧实验）。story_id 只在全部预测完成后用于“现状对照”统计，
不进入比较器、候选选择或归组。
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dedup import comparator_exact, comparator_fact  # noqa: E402
from dedup.background import build_background  # noqa: E402
from dedup.contract import validate_module  # noqa: E402
from dedup.replay import run_replay  # noqa: E402
from dedup.snapshot import comparator_view, load_snapshot, sha256_file  # noqa: E402

COMPARATORS = {"exact": comparator_exact, "fact": comparator_fact}


def git_state() -> dict:
    import subprocess
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=ROOT, capture_output=True,
                               text=True).stdout.strip()
        return {"commit": head, "experiment_dir_dirty": bool(dirty)}
    except OSError:
        return {"commit": None, "experiment_dir_dirty": None}


def code_hashes() -> dict:
    out = {}
    for p in sorted(list((ROOT / "dedup").glob("*.py")) + list(ROOT.glob("*.py")) + list((ROOT / "tools").glob("*.py"))):
        out[str(p.relative_to(ROOT))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def status_quo_comparison(snap, out_dir: str) -> dict:
    """现状对照：预测完成后才读取 story_id；只描述与现有归组的一致程度，不是质量真值。"""
    story = {a["id"]: a["story_id"] for a in snap.articles}
    tab = collections.Counter()
    grp = collections.Counter()
    with open(f"{out_dir}/pairs.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if not r["main_eval"]:
                continue
            same_story = story[r["left_id"]] == story[r["right_id"]]
            tab[(r["label"], "同story" if same_story else "异story")] += 1
            grp[("预测同组" if r["grouping"]["same_final_group"] else "预测不同组", "同story" if same_story else "异story")] += 1
    return {
        "note": "story_id 是现状归组字段：仅在预测完成后统计一致程度；“不同 story”不等于不同事件，“相同 story”也不等于正确，不能当质量真值",
        "main_pairs_label_vs_story": {f"{k[0]}|{k[1]}": v for k, v in sorted(tab.items())},
        "main_pairs_final_group_vs_story": {f"{k[0]}|{k[1]}": v for k, v in sorted(grp.items())},
    }


def summarize(snap, out_dir: str, stats: dict) -> dict:
    pairs_main = collections.Counter()
    label_main = collections.Counter()
    reason_main = collections.Counter()
    outcome_main = collections.Counter()
    same_source_main = collections.Counter()
    dist_hist = collections.Counter()
    with open(f"{out_dir}/pairs.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            ph = r["phase"]
            pairs_main[ph] += 1
            if ph != "main":
                continue
            label_main[r["label"]] += 1
            for c in r["reason_codes"]:
                reason_main[c] += 1
            outcome_main[r["grouping"]["same_pair_outcome"]] += 1
            same_source_main[(r["same_source"], r["label"])] += 1
            dist_hist[r["distance"]] += 1
    groups = collections.defaultdict(list)
    actions = collections.Counter()
    with open(f"{out_dir}/assignments.jsonl", encoding="utf-8") as f:
        for line in f:
            a = json.loads(line)
            groups[a["group_id"]].append(a)
            if a["phase"] == "main":
                actions[a["action"]] += 1
    main_groups = {g: m for g, m in groups.items() if any(x["phase"] == "main" for x in m)}
    sizes = collections.Counter(sum(1 for x in m) for m in main_groups.values())
    multi = {g: m for g, m in main_groups.items() if len(m) > 1}
    checks = {
        "main_pairs_equals_expected_74600": pairs_main["main"] == 74600,
        "main_pairs_equals_sum_min50": pairs_main["main"] == sum(min(stats["window"], i - stats["replay_start"])
                                                                for i in range(snap.main_start, snap.main_end)),
        "label_counts_conserve": sum(label_main.values()) == pairs_main["main"],
        "same_outcomes_conserve": sum(v for k, v in outcome_main.items() if k != "n/a") == label_main["same_event"],
        "actions_conserve": sum(actions.values()) == snap.main_count,
        "distance_max": max(dist_hist) if dist_hist else 0,
    }
    return {
        "articles": stats["counts"]["articles"],
        "pairs": dict(pairs_main),
        "main_label_counts": dict(label_main),
        "main_reason_codes": dict(reason_main.most_common()),
        "main_same_pair_grouping_outcome": dict(outcome_main),
        "main_same_source_pairs": {f"{'同源' if k[0] else '异源'}|{k[1]}": v for k, v in sorted(same_source_main.items())},
        "main_actions": dict(actions),
        "warmup_actions": stats["counts"]["actions_warmup"],
        "groups_touching_main": len(main_groups),
        "multi_member_groups_touching_main": len(multi),
        "articles_in_multi_member_groups": sum(len(m) for m in multi.values()),
        "group_size_histogram": {str(k): v for k, v in sorted(sizes.items())},
        "conservation_checks": checks,
        "notes": [
            "joined/multi-member 数量只描述聚合程度，不是正确率",
            "预热段从空窗口开始，预热文章的成本与标签不计入主评测分母",
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="资讯同事件去重：严格窗口回放")
    ap.add_argument("--input", required=True, help="上传的快照 JSON 路径（只读）")
    ap.add_argument("--comparator", choices=sorted(COMPARATORS), required=True)
    ap.add_argument("--out", required=True, help="新的输出目录（必须不存在）")
    ap.add_argument("--window", type=int, default=50)
    ap.add_argument("--warmup", type=int, default=50)
    ap.add_argument("--replay-from", choices=["warmup", "snapshot_start"], default="warmup",
                    help="warmup=主评测前紧邻预热段起回放（默认）；snapshot_start=从快照第一篇起完整回放（诊断用）")
    ap.add_argument("--config-json", default=None, help="覆盖比较器 DEFAULT_CONFIG 的 JSON 文件（可选）")
    args = ap.parse_args()

    out = Path(args.out)
    if out.exists():
        sys.exit(f"输出目录已存在，拒绝覆盖：{out}")
    if Path(args.input).resolve().is_relative_to(out.resolve()):
        sys.exit("输入文件不能位于输出目录内")
    out.mkdir(parents=True)
    log_f = open(out / "run.log", "w", encoding="utf-8")

    def log(msg: str) -> None:
        line = f"[{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}] {msg}"
        print(line)
        log_f.write(line + "\n")
        log_f.flush()

    started = dt.datetime.now(dt.timezone.utc)
    comp = COMPARATORS[args.comparator]
    validate_module(comp)
    t0 = time.perf_counter()
    snap = load_snapshot(args.input, warmup=args.warmup)
    load_s = time.perf_counter() - t0
    log(f"快照：{snap.checks['article_count']} 篇，主评测 {snap.main_count} 篇，预热 {snap.checks['warmup_count']} 篇")

    config = dict(comp.DEFAULT_CONFIG)
    if args.config_json:
        config.update(json.load(open(args.config_json, encoding="utf-8")))
    start = snap.warmup_start if args.replay_from == "warmup" else 0
    bg_info = None
    bg_s = 0.0
    if args.comparator == "fact":
        t0 = time.perf_counter()
        bg_views = [comparator_view(a) for a in snap.articles[:start]]
        bg = build_background(bg_views, {"snapshot_sha256": snap.sha256, "sorted_index_range": [0, start],
                                         "rule": "回放起点之前采集的全部文章"})
        bg.save(str(out / "background_df.json.gz"))
        bg_s = time.perf_counter() - t0
        bg_info = {"file": "background_df.json.gz", "n_docs": bg.n_docs, "content_hash": bg.content_hash(),
                   "source": bg.source_desc}
        config["background"] = bg
        if bg.n_docs == 0:
            log("提示：从快照第一篇起回放时没有更早的文章，背景频次表为空（所有词项等权）")
    log(f"比较器 {config['name']} {comp.VERSION}，回放起点 {start}")

    stats = run_replay(snap, comp, config, str(out), window=args.window, start=start, log=log)
    t0 = time.perf_counter()
    summary = summarize(snap, str(out), stats)
    summary["status_quo_story_comparison"] = status_quo_comparison(snap, str(out))
    summary["comparator"] = {"name": config["name"], "version": comp.VERSION}
    summary["semantic"] = {"ran": False, "reason": "运行环境无本地 embedding 模型；huggingface.co 被环境网络策略拒绝（403）"}
    summary_s = time.perf_counter() - t0
    json.dump(summary, open(out / "summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    serial_cfg = {k: v for k, v in config.items() if k != "background"}
    replay_ids = [snap.articles[i]["id"] for i in range(start, snap.main_end)]
    outputs = {}
    for name in sorted(os.listdir(out)):
        if name in ("manifest.json", "run.log"):
            continue  # manifest 不含自身；run.log 在 manifest 写出后仍会追加，单独保存不计 hash
        p = out / name
        outputs[name] = {"sha256": sha256_file(str(p)), "bytes": p.stat().st_size}
    manifest = {
        "run_dir": str(out),
        "argv": sys.argv,
        "started_utc": started.isoformat(),
        "finished_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "dependencies": "仅 Python 标准库（未安装/未使用第三方包）",
        "input": {"path": str(Path(args.input).resolve()), "sha256": snap.sha256, "meta": snap.meta,
                  "checks": snap.checks},
        "code_sha256": code_hashes(),
        "git": git_state(),
        "comparator": {"module": comp.__name__, "version": comp.VERSION, "config": serial_cfg},
        "background": bg_info,
        "semantic_model": None,
        "labels": "回放不读取任何标签；评测由 run_eval.py 单独进行",
        "window_order": {
            "order_key": "(collected 解析为 UTC, id) 升序",
            "window": args.window, "replay_from": args.replay_from, "replay_start_index": start,
            "warmup_start_index": snap.warmup_start, "main_start_index": snap.main_start,
            "main_end_index_exclusive": snap.main_end,
            "first_replayed_id": replay_ids[0], "last_replayed_id": replay_ids[-1],
            "replay_id_sequence_sha256": hashlib.sha256("\n".join(replay_ids).encode()).hexdigest(),
        },
        "timings_seconds": {
            "snapshot_load_and_parse": round(load_s, 4),
            "background_build": round(bg_s, 4),
            "article_prepare_by_phase": {k: round(v, 4) for k, v in stats["timings"]["prepare_s"].items()},
            "pair_compare_by_phase": {k: round(v, 4) for k, v in stats["timings"]["compare_s"].items()},
            "grouping_by_phase": {k: round(v, 4) for k, v in stats["timings"]["grouping_s"].items()},
            "output_write": round(stats["timings"]["write_s"], 4),
            "summary": round(summary_s, 4),
            "note": "主评测耗时分母只用 main 段；预热段（warmup）成本单列；无模型初始化/向量编码成本（语义未运行）",
        },
        "counts": stats["counts"],
        "outputs_sha256": outputs,
        "manifest_note": "manifest 不包含自身 hash；summary.json 单独保存",
    }
    json.dump(manifest, open(out / "manifest.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"完成：主评测配对 {summary['pairs'].get('main', 0)}，标签 {summary['main_label_counts']}，"
        f"守恒检查 {summary['conservation_checks']}")
    log_f.close()


if __name__ == "__main__":
    main()
