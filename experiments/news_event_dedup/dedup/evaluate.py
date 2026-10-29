"""标签评测：配对层与最终归组层分开统计，所有计数由同一份记录程序化生成并做守恒校验。

约定：
- 标签含快照中不存在的文章 ID → 报错；同一无向对在同一标签集中标签冲突 → 报错；
- 金标 uncertain 不进入确定分母，单独报告；预测 uncertain 对正例记“正例弃权”，对负例记“负例弃权”，
  不算正确负例；
- 配对层：只评测回放中直接比较过的对（距离 ≤ 50 且双方都在回放内）；
- 归组层：双方都被回放过即可评价是否同组（距离 > 50 的正例分裂记为“窗口限制”）；
  有一方未回放的对不计 split，记为“未完整回放”。
"""
from __future__ import annotations

import collections
import json

GOLD = ("same_event", "different_event", "uncertain")
PRED = ("same_event", "different_event", "uncertain")


class LabelError(RuntimeError):
    pass


def load_public_pairs(path: str, source_tag: str) -> list[dict]:
    data = json.load(open(path, encoding="utf-8"))
    out = []
    for k, p in enumerate(data["pairs"]):
        cats = p.get("strata") or ([p["topic"]] if p.get("topic") else [p.get("original_label", "未分层")])
        out.append({"label_id": f"{source_tag}-{k:03d}", "left_id": p["left_id"], "right_id": p["right_id"],
                    "label": p["label"], "reason": p.get("reason", ""), "categories": cats,
                    "source": p.get("source", source_tag)})
    return out


def load_jsonl_labels(path: str) -> list[dict]:
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def check_labels(labels: list[dict], known_ids: set[str], name: str) -> None:
    seen: dict = {}
    for l in labels:
        for x in (l["left_id"], l["right_id"]):
            if x not in known_ids:
                raise LabelError(f"[{name}] 标签含快照中不存在的文章 ID：{x}（{l.get('label_id')}）")
        if l["left_id"] == l["right_id"]:
            raise LabelError(f"[{name}] 标签自配对：{l}")
        if l["label"] not in GOLD:
            raise LabelError(f"[{name}] 非法标签 {l['label']!r}")
        key = frozenset((l["left_id"], l["right_id"]))
        if key in seen and seen[key] != l["label"]:
            raise LabelError(f"[{name}] 同一无向对标签冲突：{sorted(key)}")
        seen[key] = l["label"]


class RunIndex:
    """把一次回放的输出读成可查询索引。"""

    def __init__(self, run_dir: str):
        self.pairs: dict = {}
        with open(f"{run_dir}/pairs.jsonl", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                self.pairs[frozenset((r["left_id"], r["right_id"]))] = {
                    "label": r["label"], "reason": r["reason"], "distance": r["distance"],
                    "phase": r["phase"], "outcome": r["grouping"]["same_pair_outcome"],
                    "reason_codes": r["reason_codes"], "pair_id": r["pair_id"],
                }
        self.group_of, self.seq_of, self.phase_of = {}, {}, {}
        self.assign = {}
        with open(f"{run_dir}/assignments.jsonl", encoding="utf-8") as f:
            for line in f:
                a = json.loads(line)
                self.group_of[a["article_id"]] = a["group_id"]
                self.seq_of[a["article_id"]] = a["seq"]
                self.phase_of[a["article_id"]] = a["phase"]
                self.assign[a["article_id"]] = a
        self.members = collections.defaultdict(list)
        for aid, g in self.group_of.items():
            self.members[g].append(aid)


def _rate(n: int, d: int):
    return None if d == 0 else round(n / d, 4)


def evaluate_set(labels: list[dict], run: RunIndex, name: str, direct=None) -> dict:
    """direct: 可选函数 (left_id, right_id) -> 预测标签，用于回放中没有直接比较的对（单列，不并入回放结果）。"""
    pair_rows, group_rows = [], []
    conf = collections.Counter()
    conf_direct = collections.Counter()
    not_compared = collections.Counter()
    by_cat = collections.defaultdict(collections.Counter)
    for l in labels:
        key = frozenset((l["left_id"], l["right_id"]))
        g = l["label"]
        rec = run.pairs.get(key)
        row = {"label_id": l["label_id"], "left_id": l["left_id"], "right_id": l["right_id"], "gold": g,
               "categories": l.get("categories", []), "gold_reason": l.get("reason", "")}
        if rec is not None:
            row.update({"pred": rec["label"], "pred_reason": rec["reason"], "distance": rec["distance"],
                        "phase": rec["phase"], "compared": True, "pair_id": rec["pair_id"],
                        "reason_codes": rec["reason_codes"]})
            conf[(g, rec["label"])] += 1
            for c in row["categories"]:
                by_cat[c][(g, rec["label"])] += 1
        else:
            in_l, in_r = l["left_id"] in run.seq_of, l["right_id"] in run.seq_of
            why = "distance_gt_window" if (in_l and in_r) else "not_replayed"
            row.update({"compared": False, "not_compared_reason": why})
            not_compared[why] += 1
            if direct is not None:
                p = direct(l["left_id"], l["right_id"])
                row["direct_pred"] = p["label"]
                row["direct_reason"] = p["reason"]
                conf_direct[(g, p["label"])] += 1
        pair_rows.append(row)

        # 归组层
        grow = {"label_id": l["label_id"], "gold": g, "categories": row["categories"]}
        if l["left_id"] in run.group_of and l["right_id"] in run.group_of:
            same = run.group_of[l["left_id"]] == run.group_of[l["right_id"]]
            grow["status"] = "same_group" if same else "split"
            if not same and g == "same_event":
                if rec is None:
                    grow["split_cause"] = "window_limit_not_compared"
                elif rec["label"] != "same_event":
                    grow["split_cause"] = f"comparator_{rec['label']}"
                else:
                    grow["split_cause"] = rec["outcome"]
            grow["phases"] = [run.phase_of[l["left_id"]], run.phase_of[l["right_id"]]]
        else:
            grow["status"] = "not_fully_replayed"
        group_rows.append(grow)

    def block(c: collections.Counter) -> dict:
        pos = {p: c[("same_event", p)] for p in PRED}
        neg = {p: c[("different_event", p)] for p in PRED}
        unc = {p: c[("uncertain", p)] for p in PRED}
        npos, nneg = sum(pos.values()), sum(neg.values())
        return {
            "positives": {"n": npos, "hit_same": pos["same_event"], "explicit_reject_different": pos["different_event"],
                          "abstain_uncertain": pos["uncertain"],
                          "hit_rate": _rate(pos["same_event"], npos), "abstain_rate": _rate(pos["uncertain"], npos),
                          "explicit_reject_rate": _rate(pos["different_event"], npos)},
            "negatives": {"n": nneg, "false_same": neg["same_event"], "explicit_different": neg["different_event"],
                          "abstain_uncertain": neg["uncertain"],
                          "false_same_rate": _rate(neg["same_event"], nneg), "abstain_rate": _rate(neg["uncertain"], nneg)},
            "gold_uncertain": {"n": sum(unc.values()), **{f"pred_{p}": unc[p] for p in PRED}},
            "precision_of_same_on_determinate": _rate(pos["same_event"], pos["same_event"] + neg["same_event"]),
        }

    pair_block = block(conf)
    grp = collections.Counter()
    split_causes = collections.Counter()
    for r in group_rows:
        grp[(r["gold"], r["status"])] += 1
        if r.get("split_cause"):
            split_causes[r["split_cause"]] += 1
    group_block = {
        "positives": {"same_group": grp[("same_event", "same_group")], "split": grp[("same_event", "split")],
                      "not_fully_replayed": grp[("same_event", "not_fully_replayed")]},
        "negatives": {"together_contamination": grp[("different_event", "same_group")],
                      "apart": grp[("different_event", "split")],
                      "not_fully_replayed": grp[("different_event", "not_fully_replayed")]},
        "gold_uncertain": {"same_group": grp[("uncertain", "same_group")], "split": grp[("uncertain", "split")],
                           "not_fully_replayed": grp[("uncertain", "not_fully_replayed")]},
        "positive_split_causes": dict(split_causes),
    }
    n = len(labels)
    checks = {
        "pair_rows_total": len(pair_rows) == n,
        "compared_plus_not_compared": sum(conf.values()) + sum(not_compared.values()) == n,
        "group_rows_total": sum(grp.values()) == n,
        "positive_split_causes_conserve": sum(split_causes.values()) == grp[("same_event", "split")],
        "category_counts_are_overlapping": "一对可属多个类别，类别计数不可相加",
    }
    if not all(v for k, v in checks.items() if isinstance(v, bool)):
        raise LabelError(f"[{name}] 守恒校验失败：{checks}")
    return {
        "name": name, "n_labels": n,
        "gold_counts": dict(collections.Counter(l["label"] for l in labels)),
        "pair_level_replay": {"compared": sum(conf.values()), "not_compared": dict(not_compared), **pair_block},
        "pair_level_direct_call_for_not_compared": block(conf_direct) if conf_direct else None,
        "group_level": group_block,
        "by_category_pair_level": {c: block(v) for c, v in sorted(by_cat.items())},
        "conservation_checks": checks,
        "rows": pair_rows,
        "group_rows": group_rows,
    }


def topic_group_eval(topics_path: str, run: RunIndex) -> list[dict]:
    """公开 4 类同事件主题：组内文章最终被分到几个临时组。"""
    out = []
    for t in json.load(open(topics_path, encoding="utf-8")):
        ids = [a["id"] for a in t["原始文章"]]
        groups = collections.Counter(run.group_of.get(i, "未回放") for i in ids)
        out.append({"topic": t["主题"], "articles": len(ids), "predicted_groups": len(groups),
                    "group_membership": {i: run.group_of.get(i, "未回放") for i in ids},
                    "user_reported_status_quo_events": t.get("事件数")})
    return out


def contaminated_groups(run: RunIndex, label_sets: list[list[dict]]) -> list[dict]:
    """负例标签被分进同一组的组 → 疑似错误组（需核读全部成员）。"""
    bad = {}
    for labels in label_sets:
        for l in labels:
            if l["label"] != "different_event":
                continue
            a, b = l["left_id"], l["right_id"]
            if a in run.group_of and b in run.group_of and run.group_of[a] == run.group_of[b]:
                bad.setdefault(run.group_of[a], []).append(l["label_id"])
    return [{"group_id": g, "negative_label_ids": ids, "members": run.members[g]} for g, ids in sorted(bad.items())]
