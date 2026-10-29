"""从最终回放的评测行导出全部错误/弃权案例（含原文摘录与出处链接），以及历史污染组。

  python3 tools/error_cases.py --run runs/fact_v4_1_1 --input <快照.json> --history runs/fact_v1 --out reports/error_cases_fact_v4_1_1.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dedup.snapshot import load_snapshot  # noqa: E402

SET_TITLES = {"public_115": "公开参考 115 对", "model_read_dev": "模型辅助开发核读·dev",
              "model_read_selfheld": "模型辅助开发核读·selfheld（非独立）"}


def excerpt(a: dict, n: int = 260) -> str:
    c = a["content"].replace("\n", " ")
    return c[:n] + (f"…（全文 {len(c)} 字）" if len(c) > n else "")


def art_block(a: dict, seq: int) -> list[str]:
    return [f"  - [{seq}] **{a['title']}**（{a['source_name']}，发布 {a['published']}，[原文]({a['link']})，id `{a['id']}`）",
            f"    > {excerpt(a)}"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--history", nargs="*", default=[])
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    snap = load_snapshot(args.input)
    by_id = {a["id"]: a for a in snap.articles}
    seq = {a["id"]: i for i, a in enumerate(snap.articles)}
    rows = [json.loads(l) for l in open(f"{args.run}/eval/eval_rows.jsonl", encoding="utf-8")]
    pairs = {}
    want = {frozenset((r["left_id"], r["right_id"])) for r in rows if not r.get("group_level") and r.get("compared")}
    with open(f"{args.run}/pairs.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            k = frozenset((r["left_id"], r["right_id"]))
            if k in want:
                pairs[k] = r
    assign = {}
    with open(f"{args.run}/assignments.jsonl", encoding="utf-8") as f:
        for line in f:
            a = json.loads(line)
            assign[a["article_id"]] = a

    L = [f"# 错误与弃权案例（{args.run}，由 tools/error_cases.py 生成）", "",
         "说明：列出所有“预测 ≠ 标签”的配对（含弃权），以及正例最终分裂的原因；原文为快照内容摘录，链接为快照 link 字段。",
         "标签来源：公开 115 对为用户提供的已公开开发参考；model_read_* 为实现者阅读原文的开发核读，均非盲测。", ""]
    for setname, title in SET_TITLES.items():
        prs = [r for r in rows if r["set"] == setname and not r.get("group_level")]
        errs = [r for r in prs if r.get("compared") and r["gold"] != r["pred"]]
        L.append(f"## {title}：配对层不一致 {len(errs)} 对（共 {len(prs)} 对）")
        for r in sorted(errs, key=lambda x: (x["gold"], x["pred"], x["label_id"])):
            p = pairs[frozenset((r["left_id"], r["right_id"]))]
            kind = {("same_event", "uncertain"): "正例弃权", ("same_event", "different_event"): "正例明确误拒",
                    ("different_event", "same_event"): "负例误判same（误合并风险）",
                    ("different_event", "uncertain"): "负例弃权"}.get((r["gold"], r["pred"]), f"金标{r['gold']}→预测{r['pred']}")
            L.append(f"### {r['label_id']} · {kind} · 距离 {p['distance']} · 类别 {r['categories']}")
            L.append(f"- 标签理由：{r['gold_reason']}")
            L.append(f"- 比较器：{p['label']}｜{p['reason']}（{p['reason_codes']}）")
            sup = [f"{s.get('type')}:{s.get('value', s.get('left', ''))}" for s in p["final_evidence"]["support"]][:6]
            con = [f"{c.get('type')}:{c.get('detail', '')}" for c in p["final_evidence"]["conflict"]][:3]
            L.append(f"- 支持证据（前 6）：{sup}；冲突：{con}")
            L.append(f"- 诊断（非概率）：cos={p['diagnostics']['tfidf_cosine']} 包含度={p['diagnostics']['containment_c3']} "
                     f"锚点分={p['diagnostics']['anchor_score']}")
            for x in (r["left_id"], r["right_id"]):
                L.extend(art_block(by_id[x], seq[x]))
            L.append("")
        nc = [r for r in prs if not r.get("compared")]
        if nc:
            L.append(f"### 回放中未直接比较的 {len(nc)} 对（{', '.join(sorted({r['not_compared_reason'] for r in nc}))}）：直接调用同一比较器的结果")
            for r in nc:
                mark = "" if r.get("direct_pred") == r["gold"] else " ← 不一致"
                L.append(f"- {r['label_id']} 金标 {r['gold']} / 直接比较 {r.get('direct_pred')}{mark}："
                         f"{by_id[r['left_id']]['title'][:36]} ‖ {by_id[r['right_id']]['title'][:36]}")
            L.append("")
        grs = [r for r in rows if r["set"] == setname and r.get("group_level") and r["gold"] == "same_event"
               and r["status"] == "split"]
        L.append(f"### 正例最终分裂 {len(grs)} 对（原因见括号）")
        for r in grs:
            pr = next(x for x in prs if x["label_id"] == r["label_id"])
            a, b = by_id[pr["left_id"]], by_id[pr["right_id"]]
            later = max((pr["left_id"], pr["right_id"]), key=lambda x: seq[x])
            extra = ""
            if r["split_cause"] == "split_ambiguous_multiple_groups":
                q = assign[later]["qualifying_groups"]
                extra = f"；合格组 {[g['group_id'] for g in q]}"
            elif r["split_cause"] == "split_other_members_insufficient":
                bl = [b_ for b_ in assign[later]["blocked_groups"]]
                extra = f"；受阻组 {[(g['group_id'], [n['label'] for n in g['non_same']]) for g in bl][:2]}"
            L.append(f"- {r['label_id']}（{r['split_cause']}{extra}）：[{seq[a['id']]}] {a['title'][:40]} ‖ [{seq[b['id']]}] {b['title'][:40]}")
        L.append("")

    for h in args.history:
        ev = json.load(open(f"{h}/eval/eval_summary.json", encoding="utf-8"))
        L.append(f"## 历史版本 {h} 的污染组（负例标签被分进同组）")
        for g in ev["contaminated_groups"]:
            L.append(f"### {g['group_id']}（标签 {g['negative_label_ids']}）")
            for m in g["members"]:
                L.extend(art_block(by_id[m], seq[m]))
        L.append("")
    Path(args.out).write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"写出 {args.out}，{len(L)} 行")


if __name__ == "__main__":
    main()
