"""打包回放输出以便提交：确定性 gzip（mtime=0、不含文件名），并核对解压后 sha256 与 manifest 一致。

- --full 列出的 run：pairs.jsonl → pairs.jsonl.gz（完整证据）；
- 所有 run：pairs_labels.tsv.gz（pair_id、左右 ID、距离、阶段、三态、理由码、归组结局），体积小、便于复核；
- 原始 pairs.jsonl 不入库（见 .gitignore），可用解压或按 manifest 中的提交重放得到。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def gz_write(src: Path, dst: Path) -> None:
    with open(src, "rb") as fi, open(dst, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as fo:
            for chunk in iter(lambda: fi.read(1 << 20), b""):
                fo.write(chunk)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", nargs="*", default=[])
    args = ap.parse_args()
    for d in sorted((ROOT / "runs").iterdir()):
        if not (d / "manifest.json").exists():
            continue
        man = json.load(open(d / "manifest.json", encoding="utf-8"))
        want = man["outputs_sha256"]["pairs.jsonl"]["sha256"]
        src = d / "pairs.jsonl"
        stale = d / "pairs.jsonl.gz"
        if d.name in args.full:
            gz_write(src, stale)
            got = hashlib.sha256(gzip.open(stale).read()).hexdigest()
            print(d.name, "pairs.jsonl.gz", "解压 hash 与 manifest 一致" if got == want else "不一致！")
        elif stale.exists():
            stale.unlink()
        lab = d / "pairs_labels.tsv"
        with open(src, encoding="utf-8") as f, open(lab, "w", encoding="utf-8") as fo:
            fo.write("pair_id\tleft_id\tright_id\tdistance\tphase\tlabel\treason_codes\tsame_pair_outcome\n")
            for line in f:
                r = json.loads(line)
                fo.write(f"{r['pair_id']}\t{r['left_id']}\t{r['right_id']}\t{r['distance']}\t{r['phase']}\t{r['label']}\t"
                         f"{'|'.join(r['reason_codes'])}\t{r['grouping']['same_pair_outcome']}\n")
        gz_write(lab, d / "pairs_labels.tsv.gz")
        lab.unlink()
        print(d.name, "pairs_labels.tsv.gz", (d / "pairs_labels.tsv.gz").stat().st_size, "bytes")


if __name__ == "__main__":
    main()
