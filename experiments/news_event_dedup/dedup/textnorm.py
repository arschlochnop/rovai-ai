"""文本规范化、通用样板剥离与分句。

原则：
- 只做无损或可解释的规范化（NFKC 全半角统一、小写、去除中文周边空白）；
- 不删除负号、货币符号、小数点、千分位以外的标点；
- 样板剥离使用通用句式（“X消息，”“X讯”“据X报道，”“（X）”结尾），不按来源或文章 ID 特判；
- 剥离掉的样板文本单独保留，供出处/行情来源识别使用，不参与正文相似度。
"""
from __future__ import annotations

import re
import unicodedata

CJK = "㐀-䶿一-鿿"
_CJK_SPACE_RE = re.compile(rf"(?<=[{CJK}])\s+|\s+(?=[{CJK}])")
_WS_RE = re.compile(r"\s+")

# 中文常用引号/书名号统一为英文双引号，避免同义标点造成差异。
_QUOTE_RE = re.compile("[“”「」『』‘’'\"«»]")


_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?:\D|$))")
CLAUSE_SEP = "\uff0c"  # 分句逗号统一为全角“，”，与千分位逗号区分


def normalize_light(text: str) -> str:
    """轻度规范化：NFKC、小写、统一引号、压缩空白；保留空白位置供样板识别。

    NFKC 会把中文“，”变成英文“,”，与千分位“80,000”混淆：先保护“数字,三位数字”的千分位，
    其余逗号统一还原为全角“，”作为分句符。
    """
    s = unicodedata.normalize("NFKC", text)
    s = _QUOTE_RE.sub('"', s)
    s = s.replace("\u200b", "")
    s = s.lower()
    s = _THOUSANDS_RE.sub("\u0001", s)
    s = s.replace(",", CLAUSE_SEP)
    s = s.replace("\u0001", ",")
    s = _WS_RE.sub(" ", s).strip()
    return s


def finalize(light: str) -> str:
    """去除与中文相邻的空白、数字与百分号之间的空白。"""
    s = _CJK_SPACE_RE.sub("", light)
    s = re.sub(r"(?<=\d)\s+(?=%)", "", s)
    s = re.sub(r"\s*\uff0c\s*", CLAUSE_SEP, s)
    return s


def normalize(text: str) -> str:
    return finalize(normalize_light(text))


def compact(text: str) -> str:
    """去除全部空白，用于字符 n-gram。"""
    return _WS_RE.sub("", text)


# —— 通用样板 ——
# 开头媒体前缀：“panews 10月29日消息,”“odaily星球日报讯 ”“火星财经消息,”“foresight news消息,”
_MEDIA_PREFIX_RE = re.compile(
    rf"^[a-z0-9 .&\-{CJK}]{{1,24}}?(?:\s?\d{{1,2}}\s?月\s?\d{{1,2}}\s?日)?\s?(?:消息|快讯|讯|电)(?:[，:]\s*|\s+)"
)
# 开头电头日期：“10月29日,”“2026年10月29日,”
_DATELINE_RE = re.compile(r"^(?:\d{4}\s?年\s?)?\d{1,2}\s?月\s?\d{1,2}\s?日\s?[，:]\s*")
# 开头出处：“据韩联社报道,”“据 htx 行情数据显示,”“据链上分析平台 lookonchain(@lookonchain)监测,”
_ATTRIB_RE = re.compile(
    rf"^(?:据|根据)[^，。;!?]{{0,40}}?(?:报道|消息|监测|数据显示|行情显示|数据|显示|披露|统计|发文称|称)\s?[，:]\s*"
)
# 开头“整理：xxx”之类编辑署名
_EDITOR_RE = re.compile(r"^(?:整理|编译|作者|撰文|来源)\s?[:]\s*\S{1,20}?\s")
# 结尾短出处括注：“(韩联社)”“(来源:xx)”
_TRAIL_PAREN_RE = re.compile(rf"[(]\s*(?:来源[:]?)?[^()\d]{{1,14}}[)]\s*[。.]?\s*$")
_TRAIL_LINK_RE = re.compile(r"(?:原文链接|原文地址|阅读原文)[:].*$")
# 标题通用栏目前缀（仅限不携带事实的栏目词）
_TITLE_PREFIX_RE = re.compile(r"^(?:数据|快讯|突发|重磅|最新)[:]\s*")


def strip_boilerplate(content_light: str) -> tuple[str, list[str]]:
    """在轻度规范化文本上剥离开头媒体/电头/出处样板与结尾短括注。

    返回 (核心正文（已 finalize）, 被剥离片段)。在保留空白的文本上识别，
    才能区分“odaily星球日报讯 okx…”（媒体前缀）与“腾讯内测…”（正文）。
    """
    removed: list[str] = []
    s = content_light
    for _ in range(4):
        changed = False
        for rx in (_MEDIA_PREFIX_RE, _DATELINE_RE, _ATTRIB_RE, _EDITOR_RE):
            m = rx.match(s)
            if m and m.end() < len(s):
                removed.append(m.group(0).strip())
                s = s[m.end():].lstrip()
                changed = True
        if not changed:
            break
    for rx in (_TRAIL_LINK_RE, _TRAIL_PAREN_RE):
        m = rx.search(s)
        if m and m.start() > 0:
            removed.append(m.group(0).strip())
            s = s[: m.start()].rstrip()
    return finalize(s), [finalize(r) for r in removed]


def strip_title(title_norm: str) -> str:
    m = _TITLE_PREFIX_RE.match(title_norm)
    if m and m.end() < len(title_norm):
        return title_norm[m.end():]
    return title_norm


_SENT_SPLIT_RE = re.compile(r"[。!?;\n]+")
_CLAUSE_SPLIT_RE = re.compile(r"[\uff0c]+")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]


def clauses(sentence: str) -> list[str]:
    return [c.strip() for c in _CLAUSE_SPLIT_RE.split(sentence) if c.strip()]


# —— 综述/合集识别（结构特征 + 通用栏目词）——
_ROUNDUP_TITLE_RE = re.compile(
    r"(早讯|午讯|晚讯|早报|午报|晚报|日报|周报|要闻|资讯合集|信息合集|合集|汇总|一览|盘点|精选|速览|回顾|重要资讯|"
    r"盘前要闻|今日重点|全文整理|热点|【)"
)
_ENUM_RE = re.compile(r"(?:^|[\s;。:\uff0c])([1-9]|1[0-9])[.、](?!\d)")
_KEYCAP_RE = re.compile("[0-9]️?⃣|\U0001f51f")


def roundup_signals(title_norm: str, content_norm: str) -> dict:
    """返回综述判定依据：枚举条目数、换行小标题数、标题栏目词。"""
    enum_nums = [int(m.group(1)) for m in _ENUM_RE.finditer(content_norm)]
    # 连续编号（1,2,3...）计数，避免把普通数字误当条目
    seq = 0
    expect = 1
    for n in enum_nums:
        if n == expect:
            seq += 1
            expect += 1
    keycaps = len(_KEYCAP_RE.findall(content_norm))
    title_kw = bool(_ROUNDUP_TITLE_RE.search(title_norm))
    return {"enumerated_items": seq, "keycap_items": keycaps, "title_keyword": title_kw}


def is_roundup(sig: dict, content_len: int) -> bool:
    if sig["enumerated_items"] >= 3 or sig["keycap_items"] >= 3:
        return True
    # 栏目词 + 长正文（多段）才判综述，单纯标题含“盘点”的短讯不算
    return bool(sig["title_keyword"] and content_len >= 400)
