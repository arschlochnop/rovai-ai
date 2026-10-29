"""汇总各次回放的结果（全部从 runs/*/ 的文件程序化读取），并核对每个 run 记录的代码 hash 与其 git 提交一致。

  python3 tools/compare_runs.py --out reports/run_comparison
输出 run_comparison.json 与 run_comparison.md。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ["exact_v1", "fact_v1", "fact_v2", "fact_v3", "fact_v4", "fact_v4_1", "fact_v4_1_1"]
REPO = ROOT.parents[1]


def run_commit(run_dir: Path, manifest: dict) -> str | None:
    if manifest.get("git", {}).get("commit"):
        return manifest["git"]["commit"]
    note = run_dir / "CODE_VERSION.txt"
    if note.exists():
        m = re.search(r"git commit ([0-9a-f]{7,40})", note.read_text(encoding="utf-8"))
        return m.group(1) if m else None
    return None


def verify_code(commit: str, code: dict) -> dict:
    """逐文件比对 manifest 记录的 sha256 与该 commit 中文件内容的 sha256。"""
    ok, bad, missing = 0, [], []
    rel_base = ROOT.relative_to(REPO)
    for rel, sha in code.items():
        r = subprocess.run(["git", "show", f"{commit}:{rel_base / rel}"], cwd=REPO, capture_output=True)
        if r.returncode != 0:
            missing.append(rel)
            continue
        if hashlib.sha256(r.stdout).hexdigest() == sha:
            ok += 1
        else:
            bad.append(rel)
    # 比较器真正依赖的文件必须全部一致
    core = [k for k in code if k.startswith("dedup/") or k == "run_replay.py"]
    core_ok = not any(k in bad or k in missing for k in core)
    return {"commit": commit, "files_match": ok, "files_mismatch": bad, "files_missing_in_commit": missing,
            "core_code_matches_commit": core_ok}


def block(p: dict) -> dict:
    return {"pos_n": p["positives"]["n"], "pos_hit": p["positives"]["hit_same"],
            "pos_reject": p["positives"]["explicit_reject_different"], "pos_abstain": p["positives"]["abstain_uncertain"],
            "neg_n": p["negatives"]["n"], "neg_false_same": p["negatives"]["false_same"],
            "neg_different": p["negatives"]["explicit_different"], "neg_abstain": p["negatives"]["abstain_uncertain"],
            "gold_unc": p["gold_uncertain"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rows = []
    for name in RUNS:
        d = ROOT / "runs" / name
        if not d.exists():
            continue
        man = json.load(open(d / "manifest.json", encoding="utf-8"))
        summ = json.load(open(d / "summary.json", encoding="utf-8"))
        audit = json.load(open(d / "audit" / "audit.json", encoding="utf-8")) if (d / "audit").exists() else None
        ev = json.load(open(d / "eval" / "eval_summary.json", encoding="utf-8")) if (d / "eval").exists() else None
        commit = run_commit(d, man)
        row = {
            "run": name, "comparator_version": man["comparator"]["version"],
            "code_check": verify_code(commit, man["code_sha256"]) if commit else None,
            "main_label_counts": summ["main_label_counts"],
            "main_same_pair_grouping_outcome": summ["main_same_pair_grouping_outcome"],
            "main_actions": summ["main_actions"],
            "multi_member_groups_touching_main": summ["multi_member_groups_touching_main"],
            "articles_in_multi_member_groups": summ["articles_in_multi_member_groups"],
            "group_size_histogram": summ["group_size_histogram"],
            "conservation_checks": summ["conservation_checks"],
            "timings_seconds": man["timings_seconds"],
            "audit_no_violations": audit["checks"]["no_violations"] if audit else None,
            "audit_checks": audit["checks"] if audit else None,
            "eval": {}, "contaminated_groups": None, "topic_groups": None,
        }
        if ev:
            for k, v in ev["results"].items():
                row["eval"][k] = {"pair": block(v["pair_level_replay"]), "group": v["group_level"],
                                  "direct": block(v["pair_level_direct_call_for_not_compared"])
                                  if v.get("pair_level_direct_call_for_not_compared") else None}
            row["contaminated_groups"] = [{"group_id": g["group_id"], "labels": g["negative_label_ids"],
                                           "titles": [m["title"] for m in g["member_titles"]]}
                                          for g in ev["contaminated_groups"]]
            row["topic_groups"] = [{k: t[k] for k in ("topic", "articles", "predicted_groups",
                                                     "user_reported_status_quo_events")} for t in ev["topic_groups"]]
        rows.append(row)
    out = Path(args.out)
    json.dump(rows, open(out.with_suffix(".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    L = ["# 回放对照（由 tools/compare_runs.py 从 runs/*/ 文件自动生成，请勿手改）", ""]
    L.append("## 主评测 74,600 对的标签分布与归组")
    L.append("| run | 比较器版本 | 代码与提交一致 | 审计 | same | different | uncertain | 加入 | 新建 | 歧义新建 | 多成员组 | 其中文章数 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        c = r["main_label_counts"]
        a = r["main_actions"]
        cc = r["code_check"]
        L.append(f"| {r['run']} | {r['comparator_version']} | {'是' if cc and cc['core_code_matches_commit'] else '否'}"
                 f"（{cc['commit'][:7] if cc else '-'}） | {'通过' if r['audit_no_violations'] else '未通过'} | "
                 f"{c.get('same_event', 0)} | {c.get('different_event', 0)} | {c.get('uncertain', 0)} | "
                 f"{a.get('join', 0)} | {a.get('new', 0)} | {a.get('ambiguous_new', 0)} | "
                 f"{r['multi_member_groups_touching_main']} | {r['articles_in_multi_member_groups']} |")
    L.append("")
    L.append("## 配对层（只含回放中直接比较过的对）")
    L.append("正例：命中 same / 明确误拒 different / 弃权 uncertain；负例：误判 same / 明确 different / 弃权 uncertain；金标 uncertain 单列。")
    for setname, title in (("public_115", "公开参考 115 对（用户提供、已披露、非盲测）"),
                           ("model_read_dev", "模型辅助开发核读·dev（用于修订）"),
                           ("model_read_selfheld", "模型辅助开发核读·selfheld（实现者自留，非独立）")):
        L.append(f"### {title}")
        L.append("| run | 正例n | 命中 | 误拒 | 弃权 | 负例n | 误判same | 明确different | 弃权 | 金标uncertain→(same/diff/unc) |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            e = r["eval"].get(setname)
            if not e:
                continue
            p = e["pair"]
            g = p["gold_unc"]
            L.append(f"| {r['run']} | {p['pos_n']} | {p['pos_hit']} | {p['pos_reject']} | {p['pos_abstain']} | {p['neg_n']} | "
                     f"{p['neg_false_same']} | {p['neg_different']} | {p['neg_abstain']} | "
                     f"{g['n']}→{g['pred_same_event']}/{g['pred_different_event']}/{g['pred_uncertain']} |")
        L.append("")
    L.append("## 最终归组层（双方都回放过即评价，含距离>50无法直接比较的对）")
    L.append("| run | 标签集 | 正例同组 | 正例分裂 | 正例未完整回放 | 分裂原因 | 负例同组（污染） | 负例分开 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        for setname in ("public_115", "model_read_dev", "model_read_selfheld"):
            e = r["eval"].get(setname)
            if not e:
                continue
            g = e["group"]
            L.append(f"| {r['run']} | {setname} | {g['positives']['same_group']} | {g['positives']['split']} | "
                     f"{g['positives']['not_fully_replayed']} | {json.dumps(g['positive_split_causes'], ensure_ascii=False)} | "
                     f"{g['negatives']['together_contamination']} | {g['negatives']['apart']} |")
    L.append("")
    L.append("## 标签负例被分进同组（疑似污染组）")
    for r in rows:
        L.append(f"- {r['run']}: " + ("无" if not r["contaminated_groups"] else "; ".join(
            f"{g['group_id']}（{len(g['titles'])} 篇：{' / '.join(t[:30] for t in g['titles'])}）" for g in r["contaminated_groups"])))
    L.append("")
    L.append("## 公开 4 类同事件主题的最终组数")
    L.append("| run | " + " | ".join(t["topic"] for t in rows[-1]["topic_groups"]) + " |")
    L.append("|---|" + "---|" * len(rows[-1]["topic_groups"]))
    for r in rows:
        if r["topic_groups"]:
            L.append(f"| {r['run']} | " + " | ".join(f"{t['articles']}篇→{t['predicted_groups']}组" for t in r["topic_groups"]) + " |")
    L.append("")
    L.append("## 耗时（秒；主评测分母只用 main 段，预热段单列）")
    L.append("| run | 解析快照 | 背景表 | 单篇预处理 main / warmup | 配对比较 main / warmup | 归组 main / warmup |")
    L.append("|---|---|---|---|---|---|")
    for r in rows:
        t = r["timings_seconds"]
        L.append(f"| {r['run']} | {t['snapshot_load_and_parse']} | {t['background_build']} | "
                 f"{t['article_prepare_by_phase']['main']} / {t['article_prepare_by_phase']['warmup']} | "
                 f"{t['pair_compare_by_phase']['main']} / {t['pair_compare_by_phase']['warmup']} | "
                 f"{t['grouping_by_phase']['main']} / {t['grouping_by_phase']['warmup']} |")
    out.with_suffix(".md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
