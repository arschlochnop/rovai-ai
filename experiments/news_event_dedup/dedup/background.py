"""背景词频表：只用于衡量词项稀有度（IDF），不提供任何事件事实支持。

来源约定：只使用回放起点（预热段第一篇）之前采集的文章，即严格早于所有被回放文章；
不读 story_id，不含任何预热/主评测文章，因此不会把未来信息或窗外事件成员带入比较。
表在运行开始时由输入快照确定性构建，内容 hash 写入 manifest。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
from dataclasses import dataclass

from . import textnorm as tn
from .facts import GENERIC_LATIN

_LATIN_RE = re.compile(r"(?<![a-z0-9])([a-z][a-z0-9]*(?:[.\-&'][a-z0-9]+)*)")


def doc_text(view: dict) -> str:
    title = tn.strip_title(tn.normalize(view["title"]))
    core, _ = tn.strip_boilerplate(tn.normalize_light(view["content"]))
    return title + "。" + core


def char_ngrams(text: str, sizes=(2, 3)) -> list[str]:
    s = tn.compact(text)
    out = []
    for n in sizes:
        out.extend(s[i:i + n] for i in range(len(s) - n + 1))
    return out


def latin_tokens(text: str) -> set[str]:
    return {m.group(1) for m in _LATIN_RE.finditer(text) if len(m.group(1)) >= 2}


@dataclass
class Background:
    n_docs: int
    ngram_df: dict[str, int]
    latin_df: dict[str, int]
    source_desc: dict

    def idf(self, gram: str) -> float:
        return math.log((self.n_docs + 1) / (self.ngram_df.get(gram, 0) + 1)) + 1.0

    def latin_doc_freq(self, tok: str) -> int:
        return self.latin_df.get(tok, 0)

    def content_hash(self) -> str:
        h = hashlib.sha256()
        h.update(json.dumps({"n": self.n_docs, "src": self.source_desc}, sort_keys=True, ensure_ascii=False).encode())
        for k in sorted(self.ngram_df):
            h.update(f"{k}\t{self.ngram_df[k]}\n".encode())
        for k in sorted(self.latin_df):
            h.update(f"L{k}\t{self.latin_df[k]}\n".encode())
        return h.hexdigest()

    def save(self, path: str) -> None:
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump({"n_docs": self.n_docs, "source": self.source_desc, "ngram_df": self.ngram_df,
                       "latin_df": self.latin_df}, f, ensure_ascii=False, sort_keys=True)


def build_background(views: list[dict], source_desc: dict) -> Background:
    ngram_df: dict[str, int] = {}
    latin_df: dict[str, int] = {}
    for v in views:
        text = doc_text(v)
        for g in set(char_ngrams(text)):
            ngram_df[g] = ngram_df.get(g, 0) + 1
        for t in latin_tokens(text):
            latin_df[t] = latin_df.get(t, 0) + 1
    return Background(n_docs=len(views), ngram_df=ngram_df, latin_df=latin_df, source_desc=source_desc)


def empty_background() -> Background:
    return Background(n_docs=0, ngram_df={}, latin_df={}, source_desc={"note": "未提供背景表：所有词项等权"})


def is_generic_latin(tok: str) -> bool:
    return tok in GENERIC_LATIN
