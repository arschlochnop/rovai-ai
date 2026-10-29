"""单篇文章的事实抽取（与比较无关，只看一篇文章自身）。

抽取内容：
- 数值：保留符号、货币/单位、中文与英文量级、原始精度（量子）、近似修饰词，
  并绑定所在分句的事实角色（现价、涨跌幅、转账量、融资额……）、时间口径（日内、24H……）、
  主体短语和对手方；
- 时间：具体日期、季度/年份、相对时间（7小时前）、口径窗口；
- 地址/交易哈希：完整形式与截写/前缀形式分开；
- 实体：拉丁字母实体（ticker、公司、协议等，不依赖固定词表）；
- 行情/数据来源（交易所、数据平台），用于判断报价是否同口径；
- 立场（否认/确认）、综述/复合报道结构。

抽取不到的事实一律记为 unknown，不做“未识别=相同”的推断。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR

from . import textnorm as tn

CJK = tn.CJK
MASK = "░"  # 占位符：时间表达被遮蔽，避免被当作数量

# ---------------------------------------------------------------------------
# 中文小数字（仅 1~99 + 量词/时间单位场景）
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_small_to_int(s: str) -> int | None:
    if not s:
        return None
    if s == "十":
        return 10
    if len(s) == 1 and s in _CN_DIGIT:
        return _CN_DIGIT[s]
    if len(s) == 2 and s[0] == "十" and s[1] in _CN_DIGIT:
        return 10 + _CN_DIGIT[s[1]]
    if len(s) == 2 and s[1] == "十" and s[0] in _CN_DIGIT:
        return _CN_DIGIT[s[0]] * 10
    if len(s) == 3 and s[1] == "十" and s[0] in _CN_DIGIT and s[2] in _CN_DIGIT:
        return _CN_DIGIT[s[0]] * 10 + _CN_DIGIT[s[2]]
    return None


_CN_NUM_UNIT_RE = re.compile(
    r"([一二两三四五六七八九十]{1,3})(?=(?:个)?(?:年|个月|月|周|天|日|小时|分钟|名|家|次|笔|只|支|种|项|款|位|倍|季度|起|轮))"
)


def cn_numerals_to_digits(s: str) -> str:
    """把“三年”“两名”这类小中文数字转换为阿拉伯数字；“三星”“一致”等不受影响（后面不是量词）。"""
    def rep(m: re.Match) -> str:
        v = _cn_small_to_int(m.group(1))
        return str(v) if v is not None else m.group(1)
    # “第三季度”中的三也转换；“三星电子”不会匹配（星不是量词）
    return _CN_NUM_UNIT_RE.sub(rep, s)


# ---------------------------------------------------------------------------
# 时间表达
@dataclass
class TimeExpr:
    kind: str  # date / year_month / year / quarter / clock / ago / window / named
    value: str  # 规范化取值，例如 "10-24"、"q3"、"7h_ago"、"24h"
    span: tuple[int, int]
    raw: str


_TIME_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("date", re.compile(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日")),
    ("date_slash", re.compile(r"(?<![\d.])(\d{1,2})/(\d{1,2})(?![\d/])")),
    ("quarter", re.compile(r"(?:(\d{4})(?:年|财年)?)?(?:第([1-4])季度|(?<![a-z])q([1-4])(?![a-z0-9])|前([1-4])季度?|([1-4])季度)")),
    ("fiscal", re.compile(r"(\d{4})财年")),
    ("year_month", re.compile(r"(\d{4})年(\d{1,2})月")),
    ("year", re.compile(r"(\d{4})年(?:度|底|初|内)?|(?<![\d.])(20\d{2})(?=年|财年|$|[^\d.%万亿千百])")),
    ("month", re.compile(r"(?<![\d.])(\d{1,2})月(?:份)?(?!\d{1,2}日)")),
    ("clock", re.compile(r"(?<![\d.])(\d{1,2}):(\d{2})(?::(\d{2}))?")),
    ("ago", re.compile(r"(?:约)?(\d+(?:\.\d+)?)(?:个)?(小时|分钟|天|日|周|个月|月|年)(?:前)")),
    ("window", re.compile(r"(?:过去|近|最近|未来|连续)?(\d+(?:\.\d+)?)(?:个)?(小时|h|分钟|天|日|周|个月|月|年|个交易日|交易日)(?:内|以来|后)?(?![a-z\d])")),
]

_NAMED_SCOPE = [
    ("盘前", "premarket"), ("盘后", "afterhours"), ("盘中", "intraday"), ("日内", "intraday"),
    ("盘初", "intraday"), ("早盘", "intraday"), ("午盘", "intraday"), ("尾盘", "intraday"),
    ("今日", "intraday"), ("今天", "intraday"), ("当日", "day"), ("单日", "day"), ("昨日", "day"),
    ("昨天", "day"), ("收盘", "close"), ("收涨", "close"), ("收跌", "close"), ("收报", "close"), ("收于", "close"),
    ("开盘", "open"), ("本周", "week"), ("上周", "week"), ("一周", "week"), ("周度", "week"), ("本月", "month"),
    ("单月", "month"), ("月度", "month"), ("年内", "ytd"), ("今年", "ytd"), ("年初至今", "ytd"),
    ("全年", "year"), ("同比", "yoy"), ("环比", "qoq"), ("历史累计", "cumulative"), ("累计", "cumulative"),
    ("历史总", "cumulative"),
]


def extract_time_exprs(text: str) -> list[TimeExpr]:
    found: list[TimeExpr] = []
    taken = [False] * len(text)

    def free(a: int, b: int) -> bool:
        return not any(taken[a:b])

    def mark(a: int, b: int) -> None:
        for i in range(a, b):
            taken[i] = True

    for kind, rx in _TIME_PATTERNS:
        for m in rx.finditer(text):
            a, b = m.span()
            if a == b or not free(a, b):
                continue
            g = m.groups()
            if kind == "date":
                val = f"{int(g[1]):02d}-{int(g[2]):02d}"
                if not (1 <= int(g[1]) <= 12 and 1 <= int(g[2]) <= 31):
                    continue
            elif kind == "date_slash":
                mo, d = int(g[0]), int(g[1])
                if not (1 <= mo <= 12 and 1 <= d <= 31):
                    continue
                # 只在“丨10/28”“10/28”这类明显日期语境中接受
                ctx = text[max(0, a - 1):a]
                if ctx and ctx not in "丨| (":
                    continue
                val = f"{mo:02d}-{d:02d}"
                kind = "date"
            elif kind == "quarter":
                year = g[0] or ""
                q = g[1] or g[2] or g[3] or g[4]
                pre = "ytd_" if g[3] else ""
                val = f"{year}{pre}q{q}"
            elif kind == "fiscal":
                val = f"fy{g[0]}"
            elif kind == "year_month":
                val = f"{g[0]}-{int(g[1]):02d}"
            elif kind == "year":
                y = g[0] or g[1]
                val = f"y{y}"
            elif kind == "month":
                mo = int(g[0])
                if not 1 <= mo <= 12:
                    continue
                val = f"m{mo:02d}"
            elif kind == "clock":
                hh, mm = int(g[0]), int(g[1])
                if hh > 24 or mm > 59:
                    continue
                val = f"{hh:02d}:{mm:02d}"
            elif kind == "ago":
                unit = {"小时": "h", "分钟": "min", "天": "d", "日": "d", "周": "w", "个月": "mo", "月": "mo", "年": "y"}[g[1]]
                val = f"{_dec_str(g[0])}{unit}_ago"
            elif kind == "window":
                unit = {"小时": "h", "h": "h", "分钟": "min", "天": "d", "日": "d", "周": "w", "个月": "mo",
                        "月": "mo", "年": "y", "个交易日": "td", "交易日": "td"}[g[1]]
                val = f"{_dec_str(g[0])}{unit}"
            else:  # pragma: no cover
                continue
            found.append(TimeExpr(kind=kind if kind != "window" else "window", value=val, span=(a, b), raw=m.group(0)))
            mark(a, b)
    for word, val in _NAMED_SCOPE:
        start = 0
        while True:
            i = text.find(word, start)
            if i < 0:
                break
            j = i + len(word)
            if free(i, j):
                found.append(TimeExpr(kind="named", value=val, span=(i, j), raw=word))
                mark(i, j)
            start = j
    found.sort(key=lambda t: t.span)
    return found


def _dec_str(s: str) -> str:
    d = Decimal(s).normalize()
    return format(d, "f")


def scope_of_time(te: TimeExpr) -> str | None:
    """把时间表达转换成数值口径；日期/时钟等不是口径。"""
    if te.kind == "named":
        return te.value
    if te.kind == "window":
        return te.value
    if te.kind in ("quarter", "fiscal"):
        return te.value
    return None


# ---------------------------------------------------------------------------
# 数量
_MAG = {
    "万亿": Decimal(10) ** 12, "千亿": Decimal(10) ** 11, "百亿": Decimal(10) ** 10, "十亿": Decimal(10) ** 9,
    "亿": Decimal(10) ** 8, "千万": Decimal(10) ** 7, "百万": Decimal(10) ** 6, "十万": Decimal(10) ** 5,
    "万": Decimal(10) ** 4, "千": Decimal(10) ** 3,
    "trillion": Decimal(10) ** 12, "billion": Decimal(10) ** 9, "bn": Decimal(10) ** 9, "million": Decimal(10) ** 6,
    "k": Decimal(10) ** 3, "m": Decimal(10) ** 6, "b": Decimal(10) ** 9,
}

_NUM_RE = re.compile(
    r"(?P<cur>us\$|\$|¥|€|£)?\s?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<mag>\s?(?:万亿|千亿|百亿|十亿|亿|千万|百万|十万|万|千)|\s?(?:trillion|billion|million|bn)(?![a-z])|(?<=\d)[kmb](?![a-z]))?"
)

_CURRENCY_WORDS = [
    ("美元/盎司", ("currency", "usd/oz")), ("美元/桶", ("currency", "usd/bbl")), ("美元/枚", ("currency", "usd")),
    ("美元", ("currency", "usd")), ("美金", ("currency", "usd")),
    ("韩元", ("currency", "krw")), ("日元", ("currency", "jpy")), ("港元", ("currency", "hkd")), ("港币", ("currency", "hkd")),
    ("欧元", ("currency", "eur")), ("英镑", ("currency", "gbp")), ("人民币", ("currency", "cny")),
    ("卢布", ("currency", "rub")), ("雷亚尔", ("currency", "brl")), ("泰铢", ("currency", "thb")),
    ("新台币", ("currency", "twd")), ("澳元", ("currency", "aud")), ("加元", ("currency", "cad")),
    ("新加坡元", ("currency", "sgd")), ("瑞郎", ("currency", "chf")), ("元", ("currency", "cny")),
]
_PERCENT_WORDS = [("个百分点", ("pp", "pp")), ("%", ("percent", "%")), ("bps", ("bp", "bp")), ("bp", ("bp", "bp")), ("基点", ("bp", "bp"))]
_COUNT_WORDS = "个名家次笔只支种项款位人台座条份股张篇起轮期架辆吨桶盎司颗块户所处场部集票席单"
_LATIN_UNIT_RE = re.compile(r"\s?([a-z][a-z0-9]{0,11}(?:\.[a-z]{2,4})?)(?![a-z0-9])")
_GENERIC_LATIN_UNIT_STOP = {"and", "or", "of", "to", "in", "on", "at", "by", "the", "for", "vs", "with"}


@dataclass
class Quantity:
    raw: str
    value: Decimal
    quantum: Decimal  # 原始表达的最小精度单位（含量级）
    sig_digits: int
    unit_cat: str  # currency / token / percent / pp / bp / points / count / multiple / latin / none
    unit: str  # usd / usdt / eth / % / 名 ...
    sign: int  # 显式符号：-1 / +1 / 0
    qualifier: str  # approx / over / under / ""
    role: str
    direction: str  # up / down / in / out / ""
    scope: str  # 口径（24h / intraday / q3 ...），空表示未写明
    subject: str  # 规范化主体短语（可能为空）
    subject_conf: str  # clause / sentence / title / none
    counterpart: str  # 对手方（向X/从X）
    venue: str  # 行情/数据来源
    in_title: bool
    clause_index: int
    sentence_index: int
    in_parens_valuation: bool = False

    def key_desc(self) -> str:
        return f"{self.subject or '?'}|{self.role}|{self.scope or '-'}|{self.unit_cat}:{self.unit}"

    def to_dict(self) -> dict:
        return {
            "raw": self.raw, "value": format(self.value.normalize(), "f"), "unit": f"{self.unit_cat}:{self.unit}",
            "role": self.role, "direction": self.direction, "scope": self.scope or "unknown",
            "subject": self.subject or "unknown", "subject_conf": self.subject_conf,
            "counterpart": self.counterpart or "", "venue": self.venue or "", "qualifier": self.qualifier,
            "in_title": self.in_title,
        }


# 角色词表：(词, 角色, 方向)。按长度降序匹配，选距离数字最近的一个。
_ROLE_LEXICON: list[tuple[str, str, str]] = []


def _add(words: str, role: str, direction: str = "") -> None:
    for w in words.split("|"):
        _ROLE_LEXICON.append((w, role, direction))


_add("突破|站上|升破|涨破|上破|重回|收复|站稳", "threshold", "up")
_add("跌破|失守|跌穿|下破|跌落", "threshold", "down")
_add("升至|涨至|增至|回升至|攀升至|上调至|提高至|扩大至", "level", "up")
_add("降至|跌至|减至|回落至|下调至|缩小至|降低至", "level", "down")
_add("现报|报价|收报|收于|报收|现价|价格|股价|金价|油价|报|触及", "quote", "")
_add("涨幅|上涨|大涨|暴涨|飙升|上扬|走高|拉升|反弹|涨超|涨逾|涨近|涨|增长|增加|提升|上升|升|增", "change", "up")
_add("跌幅|下跌|大跌|暴跌|下滑|走低|回落|跌超|跌逾|跌近|跌|下降|减少|降低|缩水|降|减", "change", "down")
_add("净流入|流入|净买入|净申购", "flow", "in")
_add("净流出|流出|净卖出|净赎回|撤出", "flow", "out")
_add("转入|存入|充值|充入|接收|收到|流向", "transfer", "in")
_add("转出|提取|提出|提币|发送|转移|转账|划转", "transfer", "out")
_add("买入|购入|购买|增持|抄底|买进|加仓|追加", "trade", "buy")
_add("卖出|抛售|出售|减持|清仓|套现|砸盘|卖掉", "trade", "sell")
_add("借出|借入|借款|贷款|借贷", "borrow", "")
_add("抵押|质押|保证金", "collateral", "")
_add("铸造|增发|新增铸造|发行", "mint", "")
_add("销毁", "burn", "")
_add("回购", "buyback", "")
_add("解锁|解押|解质押|赎回", "unlock", "")
_add("空投", "airdrop", "")
_add("价值|约合|合计约|折合|价值约", "valuation", "")
_add("市值|总市值|流通市值", "mcap", "")
_add("估值", "company_valuation", "")
_add("持有|持仓|持币|仓位|余额|储备|总资产|资产净值|管理规模|托管规模|资产规模", "holding", "")
_add("融资|募资|筹集|筹资|募集|领投|投资", "raise", "")
_add("营收|收入|营业收入|销售额|总收入", "revenue", "")
_add("净利润|净利|利润|经营利润|营业利润|经营溢利|归母净利|盈利", "profit", "")
_add("亏损|浮亏", "loss", "")
_add("浮盈|获利|盈利约|收益", "gain", "")
_add("损失|被盗|盗取|窃取|被黑|遭窃|盗走|转走", "incident_loss", "")
_add("爆仓|清算|强平", "liquidation", "")
_add("用户|客户|持有者|员工|人数|账户|会员", "people", "")
_add("目标价", "target_price", "")
_add("罚款|罚金", "fine", "")
_add("利率|收益率|年化|apy|apr|费率|资金费率", "rate", "")
_add("总供应量|供应量|总量|流通量|总供给", "supply", "")
_add("概率|胜率|支持率|赞成率|赞成票", "probability", "")
_add("份额|占比|比例|比率|权重", "share", "")
_add("成交额|成交量|交易量|交易额", "volume", "")
_add("赎金|勒索", "ransom", "")
_ROLE_LEXICON.sort(key=lambda t: -len(t[0]))
_ROLE_RE = re.compile("|".join(re.escape(w) for w, _, _ in _ROLE_LEXICON))
_ROLE_MAP = {w: (r, d) for w, r, d in _ROLE_LEXICON}

# 主体短语清理：出处/口径/符号在任意位置去掉；连接词、副词、单字虚词只从两端剥离，避免切坏名称
_SUBJ_DROP_ANYWHERE_RE = re.compile(
    r"(据|根据)[^\uff0c。]{0,30}?(报道|监测|数据显示|数据|显示|消息)|截至发稿前|截至发稿|发稿时|与此同时|"
    r"日内|盘中|今日|当日|单日|昨日|今天|昨天|盘前|盘后|开盘|收盘|"
    r"[\"()\[\]:·|丨\-—–_/\\\uff0c]"
)
_SUBJ_EDGE_WORDS = sorted(
    "其中 此外 同时 另外 目前 当前 此前 随后 然而 而且 并且 但是 刚刚 再次 继续 仍然 已经 短时 短线 小幅 大幅 "
    "分别 共计 总计 合计 累计 至少 最多 最高 最低 高达 超过 预计 预期 预测 计划 宣布 表示 透露 显示 消息 报道 "
    "一度 进一步 持续 首次 正式 即将 已于 将于 于 刚 又 再 仍 已 将 曾 共 约 近 超 逾 达 为 是 的 了 在 对 该 此 其 "
    "这 那 即 则 也 都 还 就 才 拟 称 并 而 且 但 正 被 遭 获 转 较 、".split(),
    key=len, reverse=True,
)
_COUNTERPART_RE = re.compile(rf"(?:向|从|(?<!来)自|(?<!截)至|到|给|朝)([a-z0-9][a-z0-9 .\-]{{0,24}}[a-z0-9]|[a-z0-9]|[{CJK}]{{1,8}})")
_ANON_SUBJECTS = {
    "某巨鲸", "巨鲸", "某地址", "地址", "某钱包", "钱包", "交易员", "某交易员", "用户", "某用户", "分析师", "机构",
    "知情人士", "数据", "报告", "市场", "该地址", "该巨鲸", "某鲸鱼", "鲸鱼", "投资者", "散户", "公司", "该公司",
    "该交易者", "交易者", "某实体", "实体", "多头", "空头", "官方", "团队", "项目", "平台", "交易所",
}

# 通用拉丁类别词：不能单独作为主体身份证据
GENERIC_LATIN = {
    "etf", "etfs", "ai", "ceo", "cfo", "cto", "coo", "cio", "api", "apis", "app", "apps", "ipo", "dex", "cex", "nft",
    "nfts", "defi", "dao", "tvl", "apy", "apr", "gdp", "cpi", "pce", "rwa", "usd", "h", "k", "m", "b", "x", "the",
    "of", "and", "in", "on", "to", "for", "a", "an", "vs", "via", "by", "at", "or", "is", "with", "from", "new",
    "q", "news", "inc", "ltd", "llc", "co", "corp", "group", "holdings", "labs", "lab", "foundation", "protocol",
    "network", "finance", "capital", "ventures", "token", "tokens", "coin", "chain", "wallet", "exchange", "market",
    "markets", "data", "spot", "perp", "pro", "plus", "mini", "global", "digital", "trust", "fund", "index",
    "com", "io", "xyz", "org", "net", "www", "https", "http", "o", "n", "l", "s", "t", "u", "v", "e", "c", "d",
    "agent", "agents", "ceo兼联合创始人", "tge", "kyc", "aml", "otc", "l1", "l2", "web3", "llm", "gpu", "cpu", "hbm",
    "dram", "nand", "ssd", "pc", "ar", "vr", "ui", "ux", "id", "ok", "usd1",
}

# 通用中文类别/修饰词：主体短语去掉这些后再比较身份
GENERIC_CJK_SUBJECT_WORDS = [
    "美国", "韩国", "中国", "日本", "香港", "欧洲", "全球", "现货", "指数", "综合", "股价", "股票", "公司", "集团",
    "平台", "交易所", "代币", "永续合约", "永续", "合约", "板块", "概念股", "概念", "市场", "官方", "关联地址",
    "关联钱包", "相关地址", "地址", "钱包", "旗下", "子公司", "母公司", "巨鲸", "鲸鱼", "交易员", "总", "单日",
    "昨日", "今日", "当日", "本周", "首日", "价格", "比特币现货", "以太坊现货",
]

# 通用中英别名：只用于避免把同一主体的中英文写法误判为“主体冲突”，
# 不参与“主体相同”的唯一证据，也不覆盖未知主体（未知主体走通用抽取）。
ALIASES: dict[str, str] = {
    "比特币": "btc", "bitcoin": "btc", "以太坊": "eth", "以太币": "eth", "ethereum": "eth", "索拉纳": "sol",
    "solana": "sol", "瑞波币": "xrp", "瑞波": "ripple", "狗狗币": "doge", "币安": "binance", "欧易": "okx",
    "火币": "htx", "huobi": "htx", "贝莱德": "blackrock", "富达": "fidelity", "灰度": "grayscale",
    "摩根大通": "jpmorgan", "小摩": "jpmorgan", "摩根士丹利": "morganstanley", "大摩": "morganstanley",
    "高盛": "goldmansachs", "花旗": "citi", "美银": "bofa", "美国银行": "bofa", "谷歌": "google",
    "alphabet": "google", "微软": "microsoft", "msft": "microsoft", "苹果": "apple", "英伟达": "nvidia",
    "甲骨文": "oracle", "亚马逊": "amazon", "特斯拉": "tesla", "三星电子": "samsung", "三星": "samsung",
    "国际货币基金组织": "imf", "美联储": "fed", "fomc": "fed", "美国证券交易委员会": "sec",
    "商品期货交易委员会": "cftc", "星展银行": "dbs", "星展": "dbs", "日经": "nikkei", "恒生": "hangseng",
    "纳斯达克": "nasdaq", "纳指": "nasdaq", "标普": "s&p", "孙宇晨": "justinsun", "赵长鹏": "cz",
}

_LATIN_TOKEN_RE = re.compile(r"(?<![a-z0-9])\$?([a-z][a-z0-9]*(?:[.\-&'][a-z0-9]+)*)")

_ADDR_FULL_RE = re.compile(r"(?<![0-9a-z])0x[0-9a-f]{40}(?![0-9a-z])")
_TX_FULL_RE = re.compile(r"(?<![0-9a-z])0x[0-9a-f]{64}(?![0-9a-z])")
_ADDR_TRUNC_RE = re.compile(r"(?<![0-9a-z])(0x[0-9a-f]{2,12})\s?(?:\.{2,}|…+|\*+)\s?([0-9a-f]{2,12})(?![0-9a-z])")
_ADDR_PREFIX_RE = re.compile(r"(?<![0-9a-z])(0x[0-9a-f]{3,12})(?![0-9a-z.…])")
_BTC_ADDR_RE = re.compile(r"(?<![0-9a-z])(bc1[0-9a-z]{20,80})(?![0-9a-z])")

_VENUE_RE = re.compile(
    rf"(?:据|根据)\s?([a-z][a-z0-9 .\-]{{1,20}}|[{CJK}]{{2,6}}[a-z]{{0,6}})\s?(?:行情数据|行情|数据|报价)|"
    rf"([a-z][a-z0-9 .\-]{{1,20}}|[{CJK}]{{2,6}}[a-z]{{0,6}})\s?(?:行情数据|行情)显示"
)
_VENUE_STOP = {"链上", "最新", "相关", "市场", "公开", "官方", "该", "上述", "行情", "交易", "统计", "历史",
               "数据", "平台", "现货", "实时", "监测", "据", "根据"}

_DENY_RE = re.compile(r"否认|不实|辟谣|并非|并未|未发生|未经授权的说法|假消息|虚假|澄清")
_CONFIRM_RE = re.compile(r"确认|证实|属实")


@dataclass
class ArticleFacts:
    article_id: str
    source_name: str
    osint_source_id: str
    published: str
    title_norm: str
    core_norm: str  # 剥离样板后的正文
    removed_boilerplate: list[str]
    full_norm: str  # 标题 + 核心正文（用于“是否出现过某词”的检查）
    quantities: list[Quantity]
    times: list[TimeExpr]
    dates: set[str]  # 具体日期（不含电头日期）
    periods: set[str]  # 季度/财年等
    agos: set[str]  # 相对时间
    addresses_full: set[str]
    tx_full: set[str]
    addresses_partial: list[tuple[str, str]]  # (前缀, 后缀) 截写/前缀
    latin_tokens: set[str]
    title_latin: set[str]
    title_subject: str  # 标题主体短语（规范化）
    title_subject_ids: set[str]  # 主体身份键（拉丁实体 / 别名 / 中文核心）
    title_fact_subjects: list[str]  # 标题中携带数值的分句主体
    venues: set[str]
    stance: str  # deny / confirm / ""
    roundup: bool
    roundup_signals: dict
    compound_title: bool
    content_len: int
    extra: dict = field(default_factory=dict)


def _mask_spans(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            chars[i] = MASK
    return "".join(chars)


def _quantum(num: str, mag: Decimal) -> tuple[Decimal, int]:
    digits = num.replace(",", "")
    if "." in digits:
        ip, fp = digits.split(".")
        q = Decimal(1).scaleb(-len(fp)) * mag
        sig = len((ip.lstrip("0") + fp).lstrip("0")) or 1
        # 3.00 这类“小数位全零”的有效位按书写计
        sig = max(sig, len(ip.lstrip("0")) + len(fp))
    else:
        stripped = digits.lstrip("0") or "0"
        tz = len(stripped) - len(stripped.rstrip("0")) if stripped != "0" else 0
        q = Decimal(10) ** tz * mag
        sig = len(stripped.rstrip("0")) or 1
    return q, sig


def _unit_after(text: str, pos: int) -> tuple[str, str, int]:
    """从 pos 开始识别单位，返回 (类别, 单位, 结束位置)。"""
    s = text[pos:pos + 16]
    lead = len(s) - len(s.lstrip(" "))
    s2 = s.lstrip(" ")
    base = pos + lead
    for w, (cat, unit) in _PERCENT_WORDS:
        if s2.startswith(w):
            return cat, unit, base + len(w)
    for w, (cat, unit) in _CURRENCY_WORDS:
        if s2.startswith(w):
            # “元”后接“宇宙/素”等不是货币；“美元”已优先匹配
            if w == "元" and s2[1:2] in ("宇", "素", "老", "旦"):
                break
            return cat, unit, base + len(w)
    if s2.startswith("枚"):
        rest = s2[1:]
        m = _LATIN_UNIT_RE.match(rest)
        if m and m.group(1) not in _GENERIC_LATIN_UNIT_STOP:
            return "token", m.group(1), base + 1 + m.end()
        return "token", "?", base + 1
    if s2.startswith("点") and not s2.startswith("点击"):
        return "points", "点", base + 1
    if s2.startswith("倍"):
        return "multiple", "倍", base + 1
    if s2[:1] and s2[0] in _COUNT_WORDS:
        return "count", s2[0], base + 1
    m = _LATIN_UNIT_RE.match(s)
    if m and m.group(1) not in _GENERIC_LATIN_UNIT_STOP:
        tok = m.group(1)
        if tok in ("usdt", "usdc", "usdg", "usde", "usds", "dai", "fdusd", "pyusd", "rlusd", "usd1"):
            return "token", tok, pos + m.end()
        if tok in ("usd", "dollars"):
            return "currency", "usd", pos + m.end()
        return "token", tok, pos + m.end()
    return "none", "", pos


def _qualifier(left: str, right: str) -> str:
    l3 = left[-4:]
    if re.search(r"(约|大约|左右|约合|上下|~)$", l3) or re.match(r"^(左右|上下)", right):
        return "approx"
    if re.search(r"(超|逾|超过|多于|以上|高于)$", l3) or re.match(r"^(多|余|以上)", right):
        return "over"
    if re.search(r"(近|接近|将近|不到|低于|不足)$", l3) or re.match(r"^(以下|以内)", right):
        return "under"
    return ""


def _find_role(left: str) -> tuple[str, str, int]:
    """在分句左侧上下文中找离数字最近的角色词，返回 (角色, 方向, 角色词起点)。"""
    best = None
    for m in _ROLE_RE.finditer(left):
        w = m.group(0)
        # “报”单字容易误伤（报告/报道）
        if w == "报" and left[m.end():m.end() + 1] in ("告", "道", "名", "价", "送"):
            continue
        if best is None or m.end() > best[2] or (m.end() == best[2] and len(w) > len(best[0])):
            best = (w, m.start(), m.end())
    if best is None:
        return "unknown", "", -1
    role, direction = _ROLE_MAP[best[0]]
    # 被修饰的数值角色：“X 涨幅/跌幅”方向由词定；“同比增”同理
    return role, direction, best[1]


_UP_RE = re.compile(r"涨|升|增|反弹|走高|上扬|拉升|飙")
_DOWN_RE = re.compile(r"跌|降|减|回落|走低|下滑|缩水|下挫")


def _change_direction(left: str) -> str:
    best, pos = "", -1
    for rx, d in ((_UP_RE, "up"), (_DOWN_RE, "down")):
        for m in rx.finditer(left):
            if m.start() > pos:
                best, pos = d, m.start()
    return best


def normalize_subject(phrase: str) -> str:
    s = _SUBJ_DROP_ANYWHERE_RE.sub(" ", phrase)
    s = re.sub(MASK + "+", " ", s)
    s = re.sub(r"\d+(?:\.\d+)?", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" ,.、\uff0c")
    changed = True
    while changed and s:
        changed = False
        for w in _SUBJ_EDGE_WORDS:
            if s.startswith(w) and len(s) > len(w):
                s = s[len(w):].lstrip(" ,.、\uff0c")
                changed = True
            if s.endswith(w) and len(s) > len(w):
                s = s[: -len(w)].rstrip(" ,.、\uff0c")
                changed = True
    if s in _SUBJ_EDGE_WORDS:
        return ""
    return s


def subject_identity(phrase: str) -> set[str]:
    """把主体短语转换成身份键集合：非通用拉丁实体 + 别名 + 去掉通用词后的中文核心。"""
    ids: set[str] = set()
    if not phrase:
        return ids
    for m in _LATIN_TOKEN_RE.finditer(phrase):
        t = m.group(1)
        if t in GENERIC_LATIN or len(t) < 2:
            continue
        ids.add(ALIASES.get(t, t))
    cjk = re.sub(r"[a-z0-9$.\-&' ]+", " ", phrase)
    for alias, key in ALIASES.items():
        if re.fullmatch(r"[a-z0-9&]+", alias):
            continue
        if alias in cjk:
            ids.add(key)
            cjk = cjk.replace(alias, " ")
    for w in GENERIC_CJK_SUBJECT_WORDS:
        cjk = cjk.replace(w, " ")
    for chunk in cjk.split():
        chunk = chunk.strip()
        if len(chunk) >= 2 and chunk not in _ANON_SUBJECTS and not chunk.startswith("某"):
            ids.add("zh:" + chunk)
    return ids


def subjects_relation(a: set[str], b: set[str]) -> str:
    """主体身份关系：same / related / different / unknown。

    - same：身份键集合相同（含中英别名归一），或双方各只有一个中文核心且互相包含（“宇树科技”/“宇树”）；
    - related：有交集或子集关系，但不完全相同（“灰度迷你ETH”与“以太坊现货ETF”都含 eth）；
      related 只能作为弱支持，不能用来判数值冲突；
    - different：无交集，或双方各自有对方没有的拉丁实体（“贝莱德ETHA”与“贝莱德IBIT”）；
    - unknown：任一方未识别出主体。
    """
    if not a or not b:
        return "unknown"
    if a == b:
        return "same"
    za = [x[3:] for x in a if x.startswith("zh:")]
    zb = [x[3:] for x in b if x.startswith("zh:")]
    if len(a) == 1 and len(b) == 1 and za and zb:
        x, y = za[0], zb[0]
        if len(x) >= 2 and len(y) >= 2 and (x in y or y in x):
            return "same"
    la = {x for x in a if not x.startswith("zh:")}
    lb = {x for x in b if not x.startswith("zh:")}
    if (la - lb) and (lb - la):
        return "different"
    if a & b:
        return "related"
    for x in za:
        for y in zb:
            if len(x) >= 2 and len(y) >= 2 and (x in y or y in x):
                return "related"
            if _common_sub(x, y) >= 3:
                return "related"
    return "different"


def _common_sub(x: str, y: str) -> int:
    best = 0
    for i in range(len(x)):
        for j in range(len(y)):
            k = 0
            while i + k < len(x) and j + k < len(y) and x[i + k] == y[j + k]:
                k += 1
            best = max(best, k)
    return best


def _venues(text: str) -> set[str]:
    out = set()
    for m in _VENUE_RE.finditer(text):
        v = (m.group(1) or m.group(2) or "").strip()
        v = re.sub(r"^(据|根据)", "", v).strip()
        if not v or v in _VENUE_STOP or len(v) < 2:
            continue
        # 去掉“链上分析平台”之类修饰
        v = re.sub(rf"^[{CJK}]*?(平台|机构|公司)", "", v) or v
        out.add(ALIASES.get(v, v))
        # “欧易okx”同时登记 okx
        for t in re.findall(r"[a-z][a-z0-9.\-]+", v):
            out.add(ALIASES.get(t, t))
        for alias, key in ALIASES.items():
            if alias in v:
                out.add(key)
    return {v for v in out if v not in _VENUE_STOP}


def _extract_quantities(segment: str, *, in_title: bool, venue: str, title_subject: str,
                        sent_offset: int) -> list[Quantity]:
    """对一段文本（标题或核心正文）逐句逐分句抽取数量。"""
    out: list[Quantity] = []
    for si, sent in enumerate(tn.sentences(segment)):
        times = extract_time_exprs(sent)
        masked = _mask_spans(sent, [t.span for t in times if t.kind != "named"])
        sent_subject = ""
        clause_start = 0
        for ci, clause in enumerate(re.split("(\uff0c)", masked)):
            if clause == "\uff0c":
                clause_start += 1
                continue
            c_from, c_to = clause_start, clause_start + len(clause)
            clause_start = c_to
            clause_times = [t for t in times if c_from <= t.span[0] < c_to]
            prev_end = 0
            clause_subject = ""
            last_token_qty: Quantity | None = None
            for m in _NUM_RE.finditer(clause):
                a = m.start("cur") if m.group("cur") else m.start("num")
                b = m.end()
                num = m.group("num")
                # 版本号、地址、字母数字混合词跳过
                prev = clause[a - 1:a] if a > 0 else ""
                if prev and (prev.isalpha() and prev.isascii() or prev in "._#" or prev == MASK):
                    continue
                if clause[b:b + 2].startswith(".") and clause[b + 1:b + 2].isdigit():
                    continue
                if re.match(r"[a-z]", clause[b:b + 1]) and not m.group("mag"):
                    # 数字后紧跟字母：可能是 4000eth 之类；交给单位识别。但 3d/4k 之类保留
                    pass
                mag_txt = (m.group("mag") or "").strip()
                if mag_txt in ("k", "m", "b") and not m.group("cur"):
                    # 单字母量级只在货币符号后接受，避免 10m（分钟/米）误判
                    b = m.start("mag")
                    mag_txt = ""
                mag = _MAG.get(mag_txt, Decimal(1))
                try:
                    base = Decimal(num.replace(",", ""))
                except Exception:  # pragma: no cover - 正则已保证
                    continue
                value = base * mag
                quantum, sig = _quantum(num, mag)
                cur = m.group("cur")
                ucat, unit, uend = _unit_after(clause, b)
                if cur and ucat in ("none", "token", "count"):
                    ucat, unit = "currency", {"$": "usd", "us$": "usd", "¥": "cny", "€": "eur", "£": "gbp"}[cur]
                    uend = b
                left = clause[prev_end:a]
                full_left = clause[:a]
                right = clause[uend:uend + 4]
                sign = 0
                pre = clause[max(0, a - 1):a]
                if pre in ("-", "−") and (a < 2 or not clause[a - 2].isalnum()):
                    sign = -1
                elif pre == "+" and (a < 2 or not clause[a - 2].isalnum()):
                    sign = 1
                role, direction, rpos = _find_role(full_left)
                parens_val = False
                if last_token_qty is not None and ucat == "currency":
                    between = clause[prev_end:a]
                    if re.fullmatch(r"\s?[(]\s?(?:价值|约合|约|合计|价值约)?\s?", between):
                        role, direction, parens_val = "valuation", "", True
                if ucat in ("percent", "pp", "bp") and role in ("unknown", "quote", "level", "threshold", "change"):
                    # 百分数的“报/升至/扩大至”语义上是涨跌幅：方向取左侧最近的涨跌词（“跌幅扩大至4.5%”为跌）
                    cd = _change_direction(full_left)
                    if cd:
                        role, direction = "change", cd
                # 主体短语：上一个数量之后、角色词之前的文本（“A涨15.5%、1小时涨11.3%”第二个数沿用 A）
                seg_from = prev_end
                subj_raw = full_left[seg_from:rpos] if rpos >= seg_from else full_left[seg_from:]
                cp = ""
                cpm = None
                for cpm in _COUNTERPART_RE.finditer(subj_raw):
                    pass
                if cpm is not None:
                    cp = cpm.group(1).strip()
                    subj_raw = subj_raw[:cpm.start()]
                subj = normalize_subject(subj_raw)
                if re.match(r"\s*较", subj_raw) or subj.startswith("较"):
                    subj = ""  # “较今开…”是比较基准，不是主体
                conf = "clause" if subj else ""
                if parens_val and last_token_qty is not None:
                    subj, conf = last_token_qty.subject, last_token_qty.subject_conf
                if not subj and clause_subject:
                    subj, conf = clause_subject, "clause"
                if not subj and sent_subject:
                    subj, conf = sent_subject, "sentence"
                if not subj and title_subject:
                    subj, conf = title_subject, "title"
                if subj and conf == "clause":
                    clause_subject = subj
                # 口径：分句内数字左侧最近的口径词；否则分句内任意口径词
                scope = ""
                best_pos = -1
                for t in clause_times:
                    sc = scope_of_time(t)
                    if sc and t.span[0] - c_from < a and t.span[0] > best_pos:
                        scope, best_pos = sc, t.span[0]
                if not scope:
                    for t in clause_times:
                        sc = scope_of_time(t)
                        if sc:
                            scope = sc
                            break
                if scope in ("1h", "24h", "intraday") and role in ("unknown",) and ucat == "percent":
                    role = "change"
                q = Quantity(
                    raw=clause[a:uend].strip(), value=value, quantum=quantum, sig_digits=sig,
                    unit_cat=ucat, unit=unit, sign=sign, qualifier=_qualifier(full_left, right),
                    role=role, direction=direction, scope=scope, subject=subj, subject_conf=conf or "none",
                    counterpart=cp, venue=venue, in_title=in_title, clause_index=ci, sentence_index=sent_offset + si,
                    in_parens_valuation=parens_val,
                )
                out.append(q)
                prev_end = uend
                if ucat in ("token", "count"):
                    last_token_qty = q
                elif not parens_val:
                    last_token_qty = None
            if clause_subject:
                sent_subject = clause_subject
    return out


_TITLE_VERB_RE = re.compile(
    "|".join(re.escape(w) for w, _, _ in _ROLE_LEXICON) +
    r"|完成|宣布|推出|上线|发布|获得|披露|遭|否认|确认|称|表示|拟|计划|将|与|向|从|对|在|于|已|正|就|成为|加入|启动|"
    r"开启|推进|支持|接入|调整|提交|批准|通过|达成|签署|公布|预计|任命|起诉|指控|调查|警告|提醒|回应|获|被|再|又|"
    r"[:]"
)


def title_subject_phrase(title_norm: str) -> str:
    sents = tn.sentences(title_norm)
    first = tn.clauses(sents[0])[0] if sents and tn.clauses(sents[0]) else ""
    times = extract_time_exprs(first)
    masked = _mask_spans(first, [t.span for t in times])
    m = _TITLE_VERB_RE.search(masked)
    cand = masked[:m.start()] if m else masked
    # 冒号前是发言主体（“Allium：……”）
    if ":" in masked[:m.end() if m else len(masked)]:
        cand = masked.split(":", 1)[0]
    return normalize_subject(cand)


def extract_facts(view: dict) -> ArticleFacts:
    """从比较器视图（白名单字段）抽取事实。"""
    title_raw = tn.strip_title(tn.normalize(view["title"]))
    title = cn_numerals_to_digits(title_raw)
    content_light = tn.normalize_light(view["content"])
    content_norm = tn.finalize(content_light)
    core, removed = tn.strip_boilerplate(content_light)
    core = cn_numerals_to_digits(core)
    full = title + "。" + core

    # 行情/数据来源只从核心正文、剥离出的出处片段和标题里找（不含媒体前缀，避免“星球日报讯okx”这类噪声）
    venues = _venues("。".join([core] + removed + [title]))
    venue_str = "|".join(sorted(venues))
    t_subject = title_subject_phrase(title)
    quantities = _extract_quantities(title, in_title=True, venue=venue_str, title_subject=t_subject, sent_offset=0)
    quantities += _extract_quantities(core, in_title=False, venue=venue_str, title_subject=t_subject, sent_offset=100)

    # 时间：电头日期（剥离掉的前缀里）不计入事件日期
    times = extract_time_exprs(full)
    dates = {t.value for t in times if t.kind == "date"}
    periods = {t.value for t in times if t.kind in ("quarter", "fiscal", "year", "year_month")}
    agos = {t.value for t in times if t.kind == "ago"}

    addr_full = set(_ADDR_FULL_RE.findall(full))
    tx_full = set(_TX_FULL_RE.findall(full))
    partial: list[tuple[str, str]] = []
    masked_full = full
    for a in addr_full | tx_full:
        masked_full = masked_full.replace(a, " ")
    for m in _ADDR_TRUNC_RE.finditer(masked_full):
        partial.append((m.group(1)[2:], m.group(2)))
    masked2 = _ADDR_TRUNC_RE.sub(" ", masked_full)
    for m in _ADDR_PREFIX_RE.finditer(masked2):
        partial.append((m.group(1)[2:], ""))
    for m in _BTC_ADDR_RE.finditer(full):
        addr_full.add(m.group(1))

    latin = {m.group(1) for m in _LATIN_TOKEN_RE.finditer(full)}
    latin = {t for t in latin if len(t) >= 2 and not re.fullmatch(r"0x[0-9a-f]+", t)}
    title_latin = {m.group(1) for m in _LATIN_TOKEN_RE.finditer(title)}
    title_latin = {t for t in title_latin if len(t) >= 2 and t not in GENERIC_LATIN}

    stance = ""
    if _DENY_RE.search(title):
        stance = "deny"
    elif _CONFIRM_RE.search(title):
        stance = "confirm"

    sig = tn.roundup_signals(title, content_norm)
    roundup = tn.is_roundup(sig, len(content_norm))

    # 复合标题：标题里至少两个分句各自带数值且主体身份不同
    title_q_subjects: list[str] = []
    for q in quantities:
        if q.in_title and q.subject_conf == "clause" and q.unit_cat not in ("none",):
            if q.subject not in title_q_subjects:
                title_q_subjects.append(q.subject)
    ids_list = [subject_identity(s) for s in title_q_subjects]
    compound = False
    for i in range(len(ids_list)):
        for j in range(i + 1, len(ids_list)):
            if subjects_relation(ids_list[i], ids_list[j]) == "different":
                compound = True

    return ArticleFacts(
        article_id=view["id"], source_name=view["source_name"], osint_source_id=view["osint_source_id"],
        published=view["published"], title_norm=title, core_norm=core, removed_boilerplate=removed,
        full_norm=full, quantities=quantities, times=times, dates=dates, periods=periods, agos=agos,
        addresses_full=addr_full, tx_full=tx_full, addresses_partial=partial, latin_tokens=latin,
        title_latin=title_latin, title_subject=t_subject, title_subject_ids=subject_identity(t_subject),
        title_fact_subjects=title_q_subjects, venues=venues, stance=stance, roundup=roundup,
        roundup_signals=sig, compound_title=compound, content_len=len(content_norm),
    )


# ---------------------------------------------------------------------------
# 数值比较
def compare_values(a: Quantity, b: Quantity) -> str:
    """equal / approx / conflict。

    - equal：Decimal 值完全相等（6,700 与 6700、126210.5 与 126210.50）；
    - approx：两者精度不同，较细的一方按较粗一方的量子四舍五入或截断后与之相等；
      （99.99 与 100.00 精度相同且不等 → conflict；不会经舍入判等）
    - conflict：其它情况。
    """
    if a.value == b.value:
        return "equal"
    if a.quantum == b.quantum:
        return "conflict"
    coarse, fine = (a, b) if a.quantum > b.quantum else (b, a)
    q = coarse.quantum
    try:
        r1 = (fine.value / q).quantize(Decimal(1), rounding=ROUND_HALF_UP) * q
        r2 = (fine.value / q).quantize(Decimal(1), rounding=ROUND_FLOOR) * q
    except Exception:  # pragma: no cover
        return "conflict"
    if r1 == coarse.value or r2 == coarse.value:
        return "approx"
    return "conflict"


def units_compatible(a: Quantity, b: Quantity) -> str:
    """same / mismatch / unknown。美元与 USDT 不视为同单位（mismatch）。"""
    if a.unit_cat == "none" or b.unit_cat == "none":
        return "unknown"
    if a.unit_cat != b.unit_cat:
        return "mismatch"
    if a.unit == b.unit:
        return "same"
    if a.unit_cat == "token" and ("?" in (a.unit, b.unit)):
        return "unknown"
    if a.unit_cat == "count" and (a.unit in "个名人位" and b.unit in "个名人位"):
        return "same"
    return "mismatch"
