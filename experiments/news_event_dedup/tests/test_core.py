"""核心单元测试（标准库 unittest）：数值等价与冲突、单位、时间解析、比较器契约、归组规则。

  cd experiments/news_event_dedup && python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dedup import comparator_exact, comparator_fact  # noqa: E402
from dedup.contract import ContractError, validate_module, validate_result  # noqa: E402
from dedup.facts import compare_values, extract_facts, units_compatible  # noqa: E402
from dedup.grouping import SequentialGrouper  # noqa: E402
from dedup.snapshot import SnapshotError, parse_utc  # noqa: E402


def art(i, title, content, pub="2026-10-29T01:00:00", src="s1"):
    return {"id": f"a{i}", "title": title, "content": content, "collected": pub, "updated": pub,
            "published": pub, "osint_source_id": src, "source_name": src, "link": ""}


def qty(text, raw_contains):
    f = extract_facts(art(0, text, text))
    for q in f.quantities:
        if raw_contains in q.raw:
            return q
    raise AssertionError(f"未抽到 {raw_contains}: {[q.raw for q in f.quantities]}")


class TestNumbers(unittest.TestCase):
    def test_thousands_separator_equal(self):
        a, b = qty("转入6,700枚ETH", "6,700"), qty("转入6700枚ETH", "6700")
        self.assertEqual(a.value, Decimal(6700))
        self.assertEqual(compare_values(a, b), "equal")

    def test_trailing_zero_decimal_equal(self):
        a, b = qty("目标价126210.5美元", "126210.5"), qty("目标价126,210.50美元", "126,210.50")
        self.assertEqual(compare_values(a, b), "equal")

    def test_no_rounding_to_equal(self):
        a, b = qty("现报99.99美元", "99.99"), qty("现报100.00美元", "100.00")
        self.assertEqual(compare_values(a, b), "conflict")

    def test_coarser_precision_is_only_approx(self):
        a, b = qty("净流出6020万美元", "6020"), qty("净流出6021.78万美元", "6021.78")
        self.assertEqual(compare_values(a, b), "approx")

    def test_magnitude(self):
        self.assertEqual(qty("价值4.985亿美元", "4.985").value, Decimal("498500000"))
        self.assertEqual(qty("持有2.250452万枚ETH", "2.250452").value, Decimal("22504.52"))

    def test_negative_sign_kept(self):
        q = qty("自由现金流为 -90.7亿美元", "90.7")
        self.assertEqual(q.sign, -1)
        self.assertLess(q.value, 0)
        self.assertEqual(compare_values(q, qty("自由现金流为90.7亿美元", "90.7")), "conflict")

    def test_range_hyphen_is_not_minus(self):
        q = qty("利率维持在3.75%-4.00%区间", "4.00")
        self.assertEqual(q.sign, 0)
        self.assertGreater(q.value, 0)

    def test_usd_vs_usdt_not_same_unit(self):
        a, b = qty("跌破80000美元", "80000"), qty("跌破80000 USDT", "80000")
        self.assertEqual(units_compatible(a, b), "mismatch")

    def test_cjk_asset_after_mei(self):
        q = qty("存入6,168枚比特币", "6,168")
        self.assertEqual((q.unit_cat, q.unit), ("token", "btc"))

    def test_time_window_not_quantity(self):
        f = extract_facts(art(0, "预测未来12个月", "比特币将在未来 12 个月内重新测试 57,000 美元"))
        self.assertFalse(any(q.raw.startswith("12") for q in f.quantities))

    def test_scope_bound_to_percent(self):
        self.assertEqual(qty("BTC跌破80000美元，日内下跌0.70%", "0.70").scope, "intraday")
        self.assertEqual(qty("BTC跌破80000 USDT，24H跌幅1.17%", "1.17").scope, "24h")


class TestTime(unittest.TestCase):
    def test_fraction_padding_lossless(self):
        self.assertEqual(parse_utc("2026-10-29T01:59:06.632", field_name="t").microsecond, 632000)
        self.assertEqual(parse_utc("2026-10-29T01:59:06.6320", field_name="t").microsecond, 632000)
        self.assertEqual(parse_utc("2026-10-29T01:59:06.632075", field_name="t").microsecond, 632075)

    def test_bad_time_raises(self):
        for bad in ("2026-10-29 01:59:06", "2026-10-29T01:59:06.1234567", "2026-10-29T01:59:06Z", "2026-02-30T00:00:00"):
            with self.assertRaises(SnapshotError):
                parse_utc(bad, field_name="t")


class TestContract(unittest.TestCase):
    def test_modules_declare_version_and_config(self):
        validate_module(comparator_exact)
        validate_module(comparator_fact)

    def test_invalid_label_or_empty_reason(self):
        good = comparator_exact.compare_articles(art(1, "a", "同一正文"), art(2, "b", "同一正文"))
        validate_result(good)
        for patch in ({"label": "maybe"}, {"reason": "  "}):
            with self.assertRaises(ContractError):
                validate_result({**good, **patch})

    def test_story_id_is_never_read(self):
        a = art(1, "SKHYNIX 24小时上涨10.1%", "据 Gate 数据，SKHYNIX 24 小时上涨 10.1%，现报 1136.11 USDT。")
        b = art(2, "LITE 24小时上涨15.5%", "据 MSX.COM 数据，LITE 24 小时上涨 15.5%，现报 993.75 USDT。")
        a["story_id"] = b["story_id"] = "same-story"
        r = comparator_fact.compare_articles(a, b)
        self.assertEqual(r["label"], "different_event")


class TestComparatorCases(unittest.TestCase):
    def test_same_template_different_asset(self):
        a = art(1, "Robinhood Assets Listing: $TORCH added to Robinhood assets", "Followin News, $TORCH added to Robinhood assets")
        b = art(2, "Robinhood Assets Listing: $AI added to Robinhood assets", "Followin News, $AI added to Robinhood assets")
        self.assertNotEqual(comparator_fact.compare_articles(a, b)["label"], "same_event")

    def test_snapshot_conflict(self):
        a = art(1, "韩国 KOSPI 综合盘中跌 1.8%，报 5908.53 点", "韩国 KOSPI 综合盘中报 5908.53 点，较今开 6017.22 点下跌 1.8%。")
        b = art(2, "韩股日内跌 3%，三星电子跌近 6%", "韩国 KOSPI 指数日内大跌 3.00%，现报 5831.67 点。三星电子跌近 6%，SK 海力士跌 0.4%。")
        self.assertEqual(comparator_fact.compare_articles(a, b)["label"], "different_event")

    def test_unknown_fact_slots_present(self):
        r = comparator_fact.compare_articles(art(1, "某公司宣布合作", "某公司宣布合作。"), art(2, "另一消息", "无关内容。"))
        for slot in ("subject", "action", "quantities", "time", "address"):
            self.assertIn(slot, r["evidence"]["facts"])


class TestGrouping(unittest.TestCase):
    def test_join_requires_all_visible_members(self):
        g = SequentialGrouper(window_size=50)
        g.decide("a", {}); g.admit(0, "a")
        g.decide("b", {"a": "same_event"}); g.admit(1, "b")
        d = g.decide("c", {"a": "same_event", "b": "uncertain"})
        self.assertEqual(d.action, "new")
        self.assertEqual(d.blocked_groups[0]["reason"], "other_members_insufficient")

    def test_ambiguity_creates_new_group(self):
        g = SequentialGrouper(window_size=50)
        g.decide("a", {}); g.admit(0, "a")
        g.decide("b", {"a": "different_event"}); g.admit(1, "b")
        d = g.decide("c", {"a": "same_event", "b": "same_event"})
        self.assertEqual(d.action, "ambiguous_new")
        self.assertEqual(len(d.qualifying_groups), 2)

    def test_out_of_window_member_gives_no_support(self):
        g = SequentialGrouper(window_size=1)
        g.decide("a", {}); g.admit(0, "a")
        g.decide("b", {"a": "different_event"}); g.admit(1, "b")
        d = g.decide("c", {"b": "different_event"})  # a 已出窗，不能再为其组提供支持
        self.assertEqual(d.action, "new")

    def test_window_mismatch_raises(self):
        g = SequentialGrouper(window_size=50)
        g.decide("a", {}); g.admit(0, "a")
        with self.assertRaises(RuntimeError):
            g.decide("b", {})


if __name__ == "__main__":
    unittest.main()
