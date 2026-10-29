"""把实现者逐对阅读原文后的开发核读结论写成标注文件（v1，冻结后不再修改）。

标注方式：模型辅助开发核读（实现者阅读快照原文给出），不是独立人工金标；
这些标签不能用来声称“未见验证”的准确率，只用于开发诊断。
"""
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S, D, U = "same_event", "different_event", "uncertain"
REP, RW, AB, DST, SSD, NTD, RB, EZ = ("转载/完全重复", "普通改写", "删节/信息增量", "不同主体同模板",
                                      "同主体不同快照/不同事实", "数值/时间/方向差异", "综述桥接风险", "易负例")
L = {}
def add(ks, lab, cats, reason):
    for k in ks:
        L[k] = (lab, cats, reason)

add([0, 2, 6, 7, 10, 11, 13, 16, 17, 18, 19, 22], S, [REP], "同一报道的转载：剥离来源前缀后正文一致或仅格式差异")
add([1], S, [AB], "同一亚马逊 Alexa 新硬件发布；一篇为删节版")
add([3], S, [AB], "同一中期选举/Clarity 法案报道；后一篇多 Wintermute 观点段落")
add([4], S, [RW], "同一 SoSoValue 板块行情快照，24h 各板块数值一致，仅来源前缀不同")
add([5, 34, 45], S, [RW], "同一 Kalshi 18-20 岁月存款上限 2500 美元报道")
add([8], S, [RW], "同一 Upwind 4 亿美元融资，格式差异")
add([9], S, [REP], "同一 CBRS 24h 涨 11.4%、现报 121.01 USDT 行情快照的转载")
add([12, 20], S, [AB], "同一报道，一篇删去末段")
add([14], S, [RW], "同一笔 USDC Treasury 北京时间 04:28 在 Solana 铸造 2.5 亿 USDC")
add([15], S, [RW, AB], "同一 Crunchbase Q3 机器人融资报告")
add([21, 64], S, [RW], "同一 CryptoQuant 创始人观点报道")
add([23], S, [RW], "同一 GitHub Copilot Fusion 全面可用公告")
add([24, 59], S, [RW], "同一宇树科技 Q3 财报，数值一致")
add([25, 28, 30, 33], S, [RW], "同一 DTCC 代币化服务上线、首批 34 只证券")
add([26, 50], S, [AB], "同一 Robinhood CEO 财报电话会代币化监管表态，详略不同")
add([27, 51], S, [RW, NTD], "同一慢雾确认 Coinbase 数据泄露、超 7 万用户（7 万/70,000 格式差异）")
add([29], S, [RW, AB], "同一 Euronext 暂停 CoinShares Zcash ETP 交易")
add([31, 39], S, [RW, NTD], "同一泰国最高法院第 4929/2569 号判决（5 万美元/50,000 美元格式差异）")
add([53], S, [AB], "同一泰国最高法院判决；一篇缺导语只有后半段")
add([32, 55], S, [RW], "同一沃什“三季度 GDP 可能高于 3%”表态")
add([35, 43], S, [RW], "同一沉寂 44 天钱包花 16 万 USDG 买入 155 万 CASHCAT")
add([36], S, [RW], "同一 100 多个钱包疑遭窃约 120 万美元事件")
add([37], S, [AB], "同一 Ripple 与 Coinbase 合作扩展 RLUSD；一篇缺导语只有后半段")
add([52, 60], S, [RW], "同一 Ripple 与 Coinbase RLUSD 合作公告")
add([38], S, [RW, NTD], "同一 FT 对 Polymarket 盈利最高账户 swisstony 的分析；1225 万与 1200 万为取整差异")
add([40], S, [RW], "同一 Y Combinator 加密创业方向报告")
add([41], S, [RW, NTD], "同一三名巨鲸向 Coinbase Prime 存入 6168 BTC、4.985 亿美元（千分位差异）")
add([42], S, [RW], "同一币安上线 5 个 bStocks 交易对公告（10 月 28 日 20:00）")
add([44], S, [AB], "同一星展银行代币化货币调查；一篇缺导语")
add([46], S, [RW, AB], "同一币安新增 5 种 bStocks 作为抵押资产公告")
add([47], S, [RW], "同一币安钱包 Predict Points 空投领取方式公告")
add([48], D, [SSD], "皮查伊同场的两句不同表态（Carbon 是最强模型 vs 正与美政府测试），各为独立信息点")
add([49], S, [RW], "同一孙宇晨关联地址 PEPE 浮盈约 962 万美元报道")
add([54, 56], S, [AB], "同一韩国警方立案前调查；一篇只有后半段")
add([57], S, [RW], "同一币安 BULLA 永续合约价格保护机制公告")
add([58], S, [RW], "同一 MARA 向 BitGo 转移 1000 BTC、8066 万美元")
add([61, 88], S, [RW, NTD], "同一 HyperLabs 申请赎回 17.9706 万 HYPE、1449 万美元（约 18 万为取整）")
add([62], S, [RW, NTD], "同一 Predict.fun 代币经济学公告；两篇对生态份额/流动性比例的转述不一致（50.95%/5% vs 51.95%/4%），属转述误差而非不同事件")
add([63], S, [RW], "同一 Ripple 预计 2026 年底年化产品收入 9 亿美元")
add([65], S, [RW], "同一红杉中国与宇树成立“杉宇·创计划”")
add([66], S, [RW, AB], "同一微软第二财季业绩指引（营收 960-971 亿美元），标题侧重不同")
add([67], S, [AB], "同一 Ibis 结束隐身模式、获 FalconX 2.5 亿美元信贷额度并完成种子前轮")
add([68], D, [DST, EZ], "PEPE 巨鲸转移 vs 三巨鲸存入 BTC，共享 Lookonchain 模板，主体/资产/数量不同")
add([69, 74, 75, 76, 79, 81, 82, 85, 94, 98, 103, 171], D, [SSD], "沃什新闻发布会上不同的单句表态，各为独立信息点")
add([86], D, [SSD], "FOMC 声明中的就业表述 vs 沃什“新型货币疗法”表态，不同信息点")
add([70, 97], D, [DST, EZ], "BitGo 巨鲸买入多币种 vs 沉寂钱包买 CASHCAT，共享 Onchain Lens 模板")
add([71, 73, 80, 89, 91, 93, 96, 100], D, [EZ], "不同主题的无关报道")
add([72, 99], D, [SSD], "Alphabet 财报电话会上不同高管的不同表态")
add([77], S, [RW, NTD], "同一币安钱包 PREDICT Prime Sale（北京时间 16:00-18:00 = UTC 08:00-10:00，单人上限 6 BNB）")
add([78], S, [RW], "同一 Visa 稳定币结算年化交易量超 250 亿美元披露")
add([83, 92], D, [DST], "美元兑人民币 vs SKHYNIX，共享 Gate 行情模板，主体不同")
add([84], U, [SSD, RB], "沃什“三季度 GDP 高于 3%”单句 vs 开场发言概要（仅泛提增长加快），是否同一信息点需业务口径")
add([87], S, [RW], "同一 Spark 累计回购超 2 亿 SPK、花费 400 万 USDS")
add([90, 106, 107, 109, 114, 116, 118, 120, 122, 124, 126, 130], D, [DST], "不同资产的同模板行情快照（CVNA/LITE/SKHYNIX）")
add([95], S, [RW, NTD], "同一 Deribit 本周五期权到期（BTC 109.2 亿 + ETH 7.76 亿 ≈ 117 亿合计口径）")
add([101], S, [AB], "同一美股开盘行情快照（标普 +0.19%、纳指 +0.28%、海力士 +8.6%），一篇信息更多")
add([102], D, [SSD], "沃什 2028 年会议安排表态 vs 开场发言概要，不同信息点")
add([104], S, [AB], "同一沃什“信贷环境已收紧”表态；一篇为开场发言概要且标题即此表态")
add([105, 111, 113], D, [DST], "Robinhood 上架不同资产（$TORCH/$AI/$PRIORS），同模板")
add([108, 110, 127], D, [DST], "SOL 跌破 100 vs BTC 跌破 80000，同模板不同资产")
add([112, 119, 125, 129], D, [DST, NTD], "SKHYNIX（Gate）vs SKHY（MSX）不同标的不同快照")
add([115, 117, 121], D, [DST], "慢雾安全事件卡片的不同事件（TranchePool/Diamante/Predict.fun）")
add([123, 128], D, [DST], "ETH vs BTC 清算强度，同模板不同资产")
add([131, 132, 143, 149], D, [RB], "综述/早讯未包含另一篇的事件，无共享事实")
add([133], U, [RB], "综述《昨夜今晨重要资讯》包含 Predict.fun 约 40 万 USDT 事件条目；综述与单事件只部分重叠，不作同一")
add([134, 137, 144, 150], D, [RB], "综述未包含另一篇的单事件（已核对综述全文）")
add([135, 138], S, [REP, RB], "同一综述的转载/截取（标题一致、正文包含）")
add([136], S, [RB, RW], "同源（财联社）美股盘前要闻综述，两家标题不同但条目一致，属同一综述")
add([139], U, [RB], "A 股盘前要闻首条即美联储维持利率决议；综述与单事件只部分重叠")
add([140], D, [RB, SSD], "美股盘前要闻综述（期货）vs 美股开盘快照，不同时点")
add([141, 145, 147], U, [RB], "沃什单句表态 vs《沃什讲话全文整理》（快照正文截断仅 300 字，无法核对是否包含）")
add([142], D, [RB, EZ], "沃什讲话整理 vs Alphabet 高管表态，无关")
add([146], D, [RB], "综述提到 Binance bStocks 持有者数据，与币安新增 bStocks 交易对公告不是同一事件")
add([148], U, [RB], "综述包含 SK 海力士 Q3 财报条目；综述与单事件只部分重叠")
add([151, 152, 153, 154, 155, 156, 157, 158, 159, 160, 161, 162, 163, 164, 165, 166, 167, 169], D, [EZ],
    "仅偶然共享数字，主题无关")
add([168], D, [DST], "Alphabet Q3 财报 vs ServiceNow Q3 财报，不同公司")
add([170], S, [AB, NTD], "同一 10 月 FOMC 利率决议：利率目标下限 3.75% 数据快讯 vs 维持 3.75%-4% 不变、两票反对的报道（粒度按“利率决议”）")
add([172, 174, 175, 176, 177, 178, 179, 180, 181, 182, 183, 184, 185, 186, 187, 188, 189, 190, 191, 192, 193, 194, 195],
    D, [EZ], "随机抽到的无关报道")
add([173], D, [SSD], "FOMC 声明全文 vs 沃什“9 月取消一剂宽松”表态，不同信息点")


def main():
    cand = [json.loads(l) for l in open(ROOT / "labels/candidates_v1.jsonl", encoding="utf-8")]
    missing = [k for k in range(len(cand)) if k not in L]
    extra = [k for k in L if k >= len(cand)]
    if missing or extra:
        sys.exit(f"标签与候选不一致 missing={missing} extra={extra}")
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    out = ROOT / "labels/dev_labels_v1.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for k, c in enumerate(cand):
            lab, cats, reason = L[k]
            f.write(json.dumps({
                "label_id": f"dev-v1-{k:03d}", "left_id": c["left_id"], "right_id": c["right_id"],
                "label": lab, "reason": reason, "categories": cats,
                "annotation_method": "模型辅助开发核读（实现者逐对阅读快照原文；非独立人工金标，不能作未见验证）",
                "annotated_at_utc": now, "sampling_stratum": c["sampling_stratum"],
                "left_seq": c["left_seq"], "right_seq": c["right_seq"], "distance": c["distance"],
            }, ensure_ascii=False) + "\n")
    h = hashlib.sha256(out.read_bytes()).hexdigest()
    print(json.dumps({"written": len(cand), "sha256": h, "at": now,
                      "label_counts": {x: sum(1 for v in L.values() if v[0] == x) for x in (S, D, U)}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
