"""独立执行核查：不导入回放/归组代码，直接从原始快照与输出文件重新推导并核对。

核查项：
1. 输入 hash、ID 唯一、时间解析（独立实现）、排序严格递增、回放顺序与 manifest 一致；
2. 每篇的候选恰为回放顺序中此前最多 50 篇：无未来、无越窗、无重复、无遗漏，距离正确；
3. 同源与重复内容文章照常占窗口位（统计其配对数，且第 2 项保证未被过滤）；
4. 重放 assignments：入组支持全部来自当前窗口可见成员、全部为 same_event；只在唯一组合格时加入；
   多组合格必须是歧义新建；无组合格必须新建；新组 ID 从未用过；
5. story_id 隔离：比较/归组/回放代码不引用 story_id，比较器字段白名单不含 story_id。

  python3 run_audit.py --run runs/fact_v1 --input <快照.json>
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TS = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?$")


def ts_key(s: str) -> tuple:
    m = TS.match(s)
    if not m:
        raise SystemExit(f"时间解析失败：{s!r}")
    g = m.groups()
    return tuple(int(x) for x in g[:6]) + (int((g[6] or "").ljust(6, "0") or 0),)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--out-name", default="audit")
    args = ap.parse_args()
    run = Path(args.run)
    out = run / args.out_name
    if out.exists():
        sys.exit(f"核查目录已存在：{out}")
    manifest = json.load(open(run / "manifest.json", encoding="utf-8"))
    raw = open(args.input, "rb").read()
    res: dict = {"checks": {}, "counts": {}, "violations": []}
    C, V = res["checks"], res["violations"]

    C["input_sha256_matches_manifest"] = hashlib.sha256(raw).hexdigest() == manifest["input"]["sha256"]
    data = json.loads(raw)
    arts = data["articles"]
    ids = [a["id"] for a in arts]
    C["ids_unique"] = len(set(ids)) == len(ids)
    keys = [(ts_key(a["collected"]), a["id"]) for a in arts]
    for a in arts:
        ts_key(a["updated"])
        ts_key(a["published"])
    C["time_parse_failures"] = 0
    order = sorted(range(len(arts)), key=lambda i: keys[i])
    C["sorted_strictly_increasing"] = all(keys[order[i]] < keys[order[i + 1]] for i in range(len(order) - 1))
    since, until = ts_key(data["since_utc"]), ts_key(data["until_utc"])
    main_pos = [p for p, i in enumerate(order) if since <= keys[i][0] < until]
    C["main_count"] = len(main_pos)
    wo = manifest["window_order"]
    W = wo["window"]
    start = wo["replay_start_index"]
    replay = [arts[order[p]] for p in range(start, main_pos[-1] + 1)]
    replay_ids = [a["id"] for a in replay]
    C["replay_sequence_hash_matches_manifest"] = (
        hashlib.sha256("\n".join(replay_ids).encode()).hexdigest() == wo["replay_id_sequence_sha256"])
    pos_of = {aid: k for k, aid in enumerate(replay_ids)}
    seq_of = {aid: start + k for k, aid in enumerate(replay_ids)}
    main_set = {arts[order[p]]["id"] for p in main_pos}
    src = {a["id"]: a["osint_source_id"] for a in arts}
    content = {a["id"]: a["content"] for a in arts}

    # —— 2/3. 窗口 ——
    lefts = collections.defaultdict(list)
    labels = {}
    n_pairs = collections.Counter()
    same_source_pairs = identical_content_pairs = 0
    with open(run / "pairs.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            rid, lid = r["right_id"], r["left_id"]
            if rid not in pos_of or lid not in pos_of:
                V.append({"type": "pair_outside_replay", "pair_id": r["pair_id"]})
                continue
            lefts[rid].append(lid)
            labels[(lid, rid)] = r["label"]
            n_pairs["main" if rid in main_set else "warmup"] += 1
            if pos_of[lid] >= pos_of[rid]:
                V.append({"type": "future_or_self", "pair_id": r["pair_id"]})
            if r["distance"] != pos_of[rid] - pos_of[lid] or r["distance"] != seq_of[rid] - seq_of[lid]:
                V.append({"type": "distance_mismatch", "pair_id": r["pair_id"]})
            if r["main_eval"] != (rid in main_set):
                V.append({"type": "main_flag_mismatch", "pair_id": r["pair_id"]})
            if (src[lid] == src[rid]) != r["same_source"]:
                V.append({"type": "same_source_flag_mismatch", "pair_id": r["pair_id"]})
            same_source_pairs += src[lid] == src[rid]
            identical_content_pairs += content[lid] == content[rid]
    window_ok = 0
    for k, aid in enumerate(replay_ids):
        expect = replay_ids[max(0, k - W):k]
        got = lefts.get(aid, [])
        if len(got) != len(set(got)):
            V.append({"type": "duplicate_comparison", "article_id": aid})
        if sorted(got) != sorted(expect):
            V.append({"type": "window_mismatch", "article_id": aid, "missing": sorted(set(expect) - set(got))[:5],
                      "extra": sorted(set(got) - set(expect))[:5]})
        else:
            window_ok += 1
    C["every_article_compared_exactly_previous_window"] = window_ok == len(replay_ids)
    C["main_pairs"] = n_pairs["main"]
    C["main_pairs_equals_74600"] = n_pairs["main"] == 74600
    res["counts"].update({"warmup_pairs": n_pairs["warmup"], "same_source_pairs_in_windows": same_source_pairs,
                          "identical_content_pairs_in_windows": identical_content_pairs,
                          "replayed_articles": len(replay_ids)})

    # —— 4. 归组重放 ——
    window: collections.deque = collections.deque()
    group_of: dict = {}
    used_gids: set = set()
    actions = collections.Counter()
    n_assign = 0
    with open(run / "assignments.jsonl", encoding="utf-8") as f:
        for k, line in enumerate(f):
            a = json.loads(line)
            n_assign += 1
            aid = a["article_id"]
            if k >= len(replay_ids) or replay_ids[k] != aid:
                V.append({"type": "assignment_order_mismatch", "index": k})
                break
            visible: dict = collections.defaultdict(list)
            for m in window:
                visible[group_of[m]].append(m)
            qual = [g for g, ms in visible.items() if all(labels.get((m, aid)) == "same_event" for m in ms)]
            act = a["action"]
            actions[act] += 1
            if len(qual) == 1:
                ok = act == "join" and a["group_id"] == qual[0] and sorted(a["support_member_ids"]) == sorted(visible[qual[0]])
                if not ok:
                    V.append({"type": "should_join_unique_group", "article_id": aid, "expected": qual[0], "got": a["group_id"]})
            elif len(qual) > 1:
                if act != "ambiguous_new" or a["group_id"] in used_gids or a["support_member_ids"]:
                    V.append({"type": "ambiguity_not_respected", "article_id": aid})
            else:
                if act != "new" or a["group_id"] in used_gids:
                    V.append({"type": "should_be_new", "article_id": aid})
            for m in a["support_member_ids"]:
                if m not in window:
                    V.append({"type": "support_not_in_window", "article_id": aid, "member": m})
                if labels.get((m, aid)) != "same_event":
                    V.append({"type": "support_not_same_event", "article_id": aid, "member": m})
            group_of[aid] = a["group_id"]
            used_gids.add(a["group_id"])
            window.append(aid)
            while len(window) > W:
                window.popleft()
    C["assignments_count_equals_replayed"] = n_assign == len(replay_ids)
    C["grouping_rules_respected"] = not any(v["type"] in ("should_join_unique_group", "ambiguity_not_respected",
                                                          "should_be_new", "support_not_in_window",
                                                          "support_not_same_event") for v in V)
    res["counts"]["actions_all_replayed"] = dict(actions)

    # —— 5. story_id 隔离（静态检查）——
    offenders = []
    for p in sorted((ROOT / "dedup").glob("*.py")):
        if p.name == "snapshot.py":
            continue  # 只在必填字段校验中出现
        txt = p.read_text(encoding="utf-8")
        # 只认代码层面的访问（字符串键或属性），文档字符串里说明“不读 story_id”不算
        if re.search(r"[\"']story_id[\"']|\.story_id\b|story_id\s*=", txt):
            offenders.append(p.name)
    snap_txt = (ROOT / "dedup" / "snapshot.py").read_text(encoding="utf-8")
    m = re.search(r"COMPARATOR_FIELDS = \((.*?)\)", snap_txt, re.S)
    C["story_id_not_in_comparator_fields"] = bool(m) and "story_id" not in m.group(1)
    C["story_id_not_referenced_in_compare_group_replay_code"] = not offenders
    C["comparator_config_has_no_story_id"] = "story_id" not in json.dumps(manifest["comparator"]["config"])
    res["story_id_offending_files"] = offenders

    C["no_violations"] = not V
    res["violations"] = V[:200]
    res["violation_count"] = len(V)
    out.mkdir()
    json.dump(res, open(out / "audit.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"checks": C, "counts": res["counts"], "violation_count": len(V)}, ensure_ascii=False, indent=1))
    if V:
        sys.exit(1)


if __name__ == "__main__":
    main()
