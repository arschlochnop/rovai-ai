"""快照读取、严格时间解析、校验与回放顺序划分。

约定（来自快照说明）：
- 时间字段无时区时按 UTC 解释；
- 评测区间为 [since_utc, until_utc)；
- 回放顺序为解析后的 (collected, id) 升序；
- 预热段为主评测第一篇之前紧邻的最多 50 篇。

任何时间解析失败都会抛出 SnapshotError 并停止，不吞异常、不跳过记录。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field

# 只接受快照中实际出现的无时区 ISO 形式；小数秒 1~6 位，补齐到微秒是无损的。
_TS_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?$"
)

REQUIRED_TOP_KEYS = ("until_utc", "since_utc", "transaction_read_only", "group_id", "articles")
REQUIRED_ARTICLE_KEYS = (
    "id", "title", "content", "collected", "updated", "published",
    "story_id", "osint_source_id", "source_name", "link",
)

# 比较器允许读取的字段（白名单）。story_id、group_id 等不在其中。
COMPARATOR_FIELDS = (
    "id", "title", "content", "collected", "updated", "published",
    "osint_source_id", "source_name", "link",
)


class SnapshotError(RuntimeError):
    """快照结构或时间不符合约定时抛出。"""


def parse_utc(value: str, *, field_name: str, article_id: str | None = None) -> dt.datetime:
    """严格解析无时区时间并标注为 UTC。

    小数秒位数 1~6：右侧补零到 6 位（无损）。超过 6 位、带时区或其它格式一律报错。
    """
    if not isinstance(value, str):
        raise SnapshotError(f"时间字段类型错误 field={field_name} id={article_id} value={value!r}")
    m = _TS_RE.match(value)
    if not m:
        raise SnapshotError(f"时间解析失败 field={field_name} id={article_id} value={value!r}")
    y, mo, d, h, mi, s, frac = m.groups()
    micro = int((frac or "").ljust(6, "0")) if frac else 0
    try:
        return dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), micro, tzinfo=dt.timezone.utc)
    except ValueError as exc:  # 非法日期（如 2 月 30 日）
        raise SnapshotError(f"时间值非法 field={field_name} id={article_id} value={value!r}: {exc}") from exc


def fraction_digits(value: str) -> int:
    m = _TS_RE.match(value)
    if not m:
        raise SnapshotError(f"时间解析失败 value={value!r}")
    return len(m.group(7) or "")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Snapshot:
    path: str
    sha256: str
    meta: dict
    since: dt.datetime
    until: dt.datetime
    # 按 (collected, id) 升序排列后的文章（原始 dict，未修改）
    articles: list[dict]
    collected_dt: list[dt.datetime]
    published_dt: list[dt.datetime]
    main_start: int  # 主评测第一篇在排序序列中的下标
    main_end: int  # 主评测最后一篇下标 + 1
    warmup_start: int
    checks: dict = field(default_factory=dict)

    @property
    def main_count(self) -> int:
        return self.main_end - self.main_start

    def replay_indices(self) -> range:
        """回放顺序：预热段 + 主评测段。"""
        return range(self.warmup_start, self.main_end)

    def is_main(self, idx: int) -> bool:
        return self.main_start <= idx < self.main_end


def comparator_view(article: dict) -> dict:
    """只保留比较器允许读取的字段；story_id 等字段被物理剔除。"""
    return {k: article[k] for k in COMPARATOR_FIELDS}


def load_snapshot(path: str, *, warmup: int = 50) -> Snapshot:
    """读取并严格校验快照，返回排序后的回放结构。"""
    sha = sha256_file(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SnapshotError("顶层不是对象")
    missing = [k for k in REQUIRED_TOP_KEYS if k not in data]
    if missing:
        raise SnapshotError(f"顶层缺少字段: {missing}")
    arts = data["articles"]
    if not isinstance(arts, list) or not arts:
        raise SnapshotError("articles 不是非空数组")

    since = parse_utc(data["since_utc"], field_name="since_utc")
    until = parse_utc(data["until_utc"], field_name="until_utc")
    if not since < until:
        raise SnapshotError("since_utc 不早于 until_utc")

    ids = set()
    frac_hist: dict[str, dict[int, int]] = {"collected": {}, "updated": {}, "published": {}}
    parsed = []
    for i, a in enumerate(arts):
        if not isinstance(a, dict):
            raise SnapshotError(f"第 {i} 条不是对象")
        miss = [k for k in REQUIRED_ARTICLE_KEYS if k not in a]
        if miss:
            raise SnapshotError(f"第 {i} 条缺少字段 {miss}")
        aid = a["id"]
        if not isinstance(aid, str) or not aid:
            raise SnapshotError(f"第 {i} 条 id 非法: {aid!r}")
        if aid in ids:
            raise SnapshotError(f"文章 ID 重复: {aid}")
        ids.add(aid)
        for fld in ("title", "content"):
            if not isinstance(a[fld], str):
                raise SnapshotError(f"{aid} 字段 {fld} 不是字符串")
        c = parse_utc(a["collected"], field_name="collected", article_id=aid)
        parse_utc(a["updated"], field_name="updated", article_id=aid)
        p = parse_utc(a["published"], field_name="published", article_id=aid)
        for fld in frac_hist:
            n = fraction_digits(a[fld])
            frac_hist[fld][n] = frac_hist[fld].get(n, 0) + 1
        parsed.append((c, aid, p, i))

    parsed.sort(key=lambda t: (t[0], t[1]))
    keys = [(t[0], t[1]) for t in parsed]
    for k in range(len(keys) - 1):
        if not keys[k] < keys[k + 1]:
            raise SnapshotError(f"排序后不严格递增: {keys[k]} vs {keys[k + 1]}")

    articles = [arts[t[3]] for t in parsed]
    collected_dt = [t[0] for t in parsed]
    published_dt = [t[2] for t in parsed]
    main_idx = [i for i, c in enumerate(collected_dt) if since <= c < until]
    if not main_idx:
        raise SnapshotError("主评测区间内没有文章")
    main_start, main_end = main_idx[0], main_idx[-1] + 1
    if main_end - main_start != len(main_idx):
        raise SnapshotError("主评测区间在排序序列中不连续")
    warmup_start = max(0, main_start - warmup)

    file_order_sorted = [t[3] for t in parsed] == list(range(len(arts)))
    checks = {
        "article_count": len(arts),
        "unique_ids": len(ids),
        "time_parse_failures": 0,
        "strictly_increasing_after_sort": True,
        "file_order_equals_sorted_order": file_order_sorted,
        "fraction_digit_histogram": {k: {str(n): c for n, c in sorted(v.items())} for k, v in frac_hist.items()},
        "main_count": main_end - main_start,
        "before_main_count": main_start,
        "after_until_count": len(arts) - main_end,
        "warmup_count": main_start - warmup_start,
        "first_collected": articles[0]["collected"],
        "last_collected": articles[-1]["collected"],
        "main_first": {"id": articles[main_start]["id"], "collected": articles[main_start]["collected"]},
        "main_last": {"id": articles[main_end - 1]["id"], "collected": articles[main_end - 1]["collected"]},
    }
    meta = {k: data[k] for k in REQUIRED_TOP_KEYS if k != "articles"}
    return Snapshot(
        path=path, sha256=sha, meta=meta, since=since, until=until,
        articles=articles, collected_dt=collected_dt, published_dt=published_dt,
        main_start=main_start, main_end=main_end, warmup_start=warmup_start, checks=checks,
    )
