"""把一次回放中触及主评测的多成员临时组导出为可核读文本（每组全部成员：序号、来源、发布时间、标题）。"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dedup.snapshot import load_snapshot  # noqa: E402


def main():
    run, inp, out = sys.argv[1], sys.argv[2], sys.argv[3]
    snap = load_snapshot(inp)
    by_id = {a["id"]: a for a in snap.articles}
    groups = collections.defaultdict(list)
    for line in open(f"{run}/assignments.jsonl", encoding="utf-8"):
        a = json.loads(line)
        groups[a["group_id"]].append(a)
    with open(out, "w", encoding="utf-8") as f:
        n = 0
        for g, ms in groups.items():
            if len(ms) < 2 or not any(m["phase"] == "main" for m in ms):
                continue
            n += 1
            f.write(f"## {g} ({len(ms)})\n")
            for m in ms:
                a = by_id[m["article_id"]]
                f.write(f"  {m['seq']} {a['source_name'][:3]} {a['published'][11:16]} {m['action'][:4]} {a['title'][:70]}\n")
        print(n)


if __name__ == "__main__":
    main()
