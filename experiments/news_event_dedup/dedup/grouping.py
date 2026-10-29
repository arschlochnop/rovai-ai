"""严格前 50 篇窗口的保守顺序归组。

规则（首版保守策略）：
- 新文章只与窗口内此前最多 50 篇比较；判定完成后才进入窗口（同源、重复、已归组的文章都占位）；
- 某临时组合格 ⇔ 该组当前窗口内可见的全部成员与新文章都是 same_event；
  任一成员 different_event 或 uncertain 都不合格；没有可见成员的旧组不可加入（不用出窗代表）；
- 恰好一个组合格才加入；多个组合格记为歧义并新建独立组，不合并旧组、不选最高分/最近组；
- 不做连通分量、不做多数票、不传递证据。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class Decision:
    action: str  # join / new / ambiguous_new
    group_id: str
    support_member_ids: list[str]
    qualifying_groups: list[dict]
    blocked_groups: list[dict]
    window_ids: list[str]


@dataclass
class SequentialGrouper:
    window_size: int = 50
    window: deque = field(default_factory=deque)  # (seq, article_id)
    group_of: dict = field(default_factory=dict)
    next_gid: int = 1

    def window_ids(self) -> list[str]:
        return [aid for _, aid in self.window]

    def visible_groups(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for _, aid in self.window:
            groups.setdefault(self.group_of[aid], []).append(aid)
        return groups

    def _new_gid(self) -> str:
        gid = f"G{self.next_gid:06d}"
        self.next_gid += 1
        return gid

    def decide(self, article_id: str, labels: dict[str, str]) -> Decision:
        """labels: 窗口内每篇前序文章 id → 与新文章比较得到的三态标签（必须恰好覆盖窗口）。"""
        win = self.window_ids()
        if set(labels) != set(win) or len(labels) != len(win):
            raise RuntimeError(f"{article_id} 的比较结果与当前窗口不一致")
        qualifying, blocked = [], []
        for gid, members in self.visible_groups().items():
            same = [m for m in members if labels[m] == "same_event"]
            if len(same) == len(members):
                qualifying.append({"group_id": gid, "visible_member_ids": members})
            elif same:
                blocked.append({
                    "group_id": gid, "visible_member_ids": members, "same_member_ids": same,
                    "non_same": [{"id": m, "label": labels[m]} for m in members if labels[m] != "same_event"],
                    "reason": "other_members_insufficient",
                })
        if len(qualifying) == 1:
            g = qualifying[0]
            dec = Decision("join", g["group_id"], list(g["visible_member_ids"]), qualifying, blocked, win)
        elif len(qualifying) > 1:
            dec = Decision("ambiguous_new", self._new_gid(), [], qualifying, blocked, win)
        else:
            dec = Decision("new", self._new_gid(), [], qualifying, blocked, win)
        self.group_of[article_id] = dec.group_id
        return dec

    def admit(self, seq: int, article_id: str) -> None:
        """判定完成后入窗口；超过窗口大小时最早的文章出窗（其组 ID 保留但不再提供支持）。"""
        self.window.append((seq, article_id))
        while len(self.window) > self.window_size:
            self.window.popleft()
