# 回放对照（由 tools/compare_runs.py 从 runs/*/ 文件自动生成，请勿手改）

## 主评测 74,600 对的标签分布与归组
| run | 比较器版本 | 代码与提交一致 | 审计 | same | different | uncertain | 加入 | 新建 | 歧义新建 | 多成员组 | 其中文章数 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| exact_v1 | exact-1.0.0 | 是（a904537） | 通过 | 475 | 0 | 74125 | 412 | 1080 | 0 | 361 | 773 |
| fact_v1 | fact-1.0.0 | 是（a904537） | 通过 | 1250 | 71766 | 1584 | 668 | 797 | 27 | 398 | 1066 |
| fact_v2 | fact-2.0.0 | 是（17d5110） | 通过 | 1261 | 72319 | 1020 | 665 | 798 | 29 | 395 | 1060 |
| fact_v3 | fact-3.0.0 | 是（75ef95f） | 通过 | 1266 | 72319 | 1015 | 669 | 796 | 27 | 393 | 1062 |
| fact_v4 | fact-4.0.0 | 是（70954e5） | 通过 | 1266 | 71422 | 1912 | 669 | 796 | 27 | 393 | 1062 |
| fact_v4_1 | fact-4.1.0 | 是（e90aa1c） | 通过 | 1266 | 71425 | 1909 | 669 | 796 | 27 | 393 | 1062 |
| fact_v4_1_1 | fact-4.1.1 | 是（6c22ca6） | 通过 | 1266 | 71425 | 1909 | 669 | 796 | 27 | 393 | 1062 |

## 配对层（只含回放中直接比较过的对）
正例：命中 same / 明确误拒 different / 弃权 uncertain；负例：误判 same / 明确 different / 弃权 uncertain；金标 uncertain 单列。
### 公开参考 115 对（用户提供、已披露、非盲测）
| run | 正例n | 命中 | 误拒 | 弃权 | 负例n | 误判same | 明确different | 弃权 | 金标uncertain→(same/diff/unc) |
|---|---|---|---|---|---|---|---|---|---|
| exact_v1 | 82 | 14 | 0 | 68 | 16 | 0 | 0 | 16 | 1→0/0/1 |
| fact_v1 | 82 | 70 | 0 | 12 | 16 | 0 | 16 | 0 | 1→0/0/1 |
| fact_v2 | 82 | 74 | 0 | 8 | 16 | 0 | 16 | 0 | 1→0/0/1 |
| fact_v3 | 82 | 74 | 0 | 8 | 16 | 0 | 16 | 0 | 1→0/0/1 |
| fact_v4 | 82 | 74 | 0 | 8 | 16 | 0 | 16 | 0 | 1→0/0/1 |
| fact_v4_1 | 82 | 74 | 0 | 8 | 16 | 0 | 16 | 0 | 1→0/0/1 |
| fact_v4_1_1 | 82 | 74 | 0 | 8 | 16 | 0 | 16 | 0 | 1→0/0/1 |

### 模型辅助开发核读·dev（用于修订）
| run | 正例n | 命中 | 误拒 | 弃权 | 负例n | 误判same | 明确different | 弃权 | 金标uncertain→(same/diff/unc) |
|---|---|---|---|---|---|---|---|---|---|
| exact_v1 | 45 | 10 | 0 | 35 | 65 | 0 | 0 | 65 | 7→0/0/7 |
| fact_v1 | 45 | 39 | 1 | 5 | 65 | 4 | 50 | 11 | 7→0/3/4 |
| fact_v2 | 45 | 39 | 1 | 5 | 65 | 0 | 59 | 6 | 7→0/4/3 |
| fact_v3 | 45 | 39 | 1 | 5 | 65 | 0 | 59 | 6 | 7→0/4/3 |
| fact_v4 | 45 | 39 | 1 | 5 | 65 | 0 | 45 | 20 | 7→0/0/7 |
| fact_v4_1 | 45 | 39 | 1 | 5 | 65 | 0 | 45 | 20 | 7→0/0/7 |
| fact_v4_1_1 | 45 | 39 | 1 | 5 | 65 | 0 | 45 | 20 | 7→0/0/7 |

### 模型辅助开发核读·selfheld（实现者自留，非独立）
| run | 正例n | 命中 | 误拒 | 弃权 | 负例n | 误判same | 明确different | 弃权 | 金标uncertain→(same/diff/unc) |
|---|---|---|---|---|---|---|---|---|---|
| exact_v1 | 33 | 11 | 0 | 22 | 46 | 0 | 0 | 46 | 0→0/0/0 |
| fact_v1 | 33 | 28 | 0 | 5 | 46 | 0 | 44 | 2 | 0→0/0/0 |
| fact_v2 | 33 | 28 | 0 | 5 | 46 | 0 | 45 | 1 | 0→0/0/0 |
| fact_v3 | 33 | 28 | 0 | 5 | 46 | 0 | 45 | 1 | 0→0/0/0 |
| fact_v4 | 33 | 28 | 0 | 5 | 46 | 0 | 45 | 1 | 0→0/0/0 |
| fact_v4_1 | 33 | 28 | 0 | 5 | 46 | 0 | 45 | 1 | 0→0/0/0 |
| fact_v4_1_1 | 33 | 28 | 0 | 5 | 46 | 0 | 45 | 1 | 0→0/0/0 |

## 最终归组层（双方都回放过即评价，含距离>50无法直接比较的对）
| run | 标签集 | 正例同组 | 正例分裂 | 正例未完整回放 | 分裂原因 | 负例同组（污染） | 负例分开 |
|---|---|---|---|---|---|---|---|
| exact_v1 | public_115 | 14 | 68 | 7 | {"comparator_uncertain": 68} | 0 | 16 |
| exact_v1 | model_read_dev | 10 | 35 | 0 | {"comparator_uncertain": 35} | 0 | 65 |
| exact_v1 | model_read_selfheld | 11 | 22 | 0 | {"comparator_uncertain": 22} | 0 | 46 |
| fact_v1 | public_115 | 62 | 20 | 7 | {"comparator_uncertain": 12, "split_ambiguous_multiple_groups": 8} | 0 | 16 |
| fact_v1 | model_read_dev | 33 | 12 | 0 | {"split_ambiguous_multiple_groups": 6, "comparator_uncertain": 5, "comparator_different_event": 1} | 4 | 61 |
| fact_v1 | model_read_selfheld | 23 | 10 | 0 | {"split_other_members_insufficient": 4, "comparator_uncertain": 5, "split_ambiguous_multiple_groups": 1} | 0 | 46 |
| fact_v2 | public_115 | 66 | 16 | 7 | {"comparator_uncertain": 8, "split_ambiguous_multiple_groups": 8} | 0 | 16 |
| fact_v2 | model_read_dev | 33 | 12 | 0 | {"split_ambiguous_multiple_groups": 6, "comparator_uncertain": 5, "comparator_different_event": 1} | 0 | 65 |
| fact_v2 | model_read_selfheld | 23 | 10 | 0 | {"split_other_members_insufficient": 4, "comparator_uncertain": 5, "split_ambiguous_multiple_groups": 1} | 0 | 46 |
| fact_v3 | public_115 | 66 | 16 | 7 | {"comparator_uncertain": 8, "split_ambiguous_multiple_groups": 8} | 0 | 16 |
| fact_v3 | model_read_dev | 33 | 12 | 0 | {"split_ambiguous_multiple_groups": 6, "comparator_uncertain": 5, "comparator_different_event": 1} | 0 | 65 |
| fact_v3 | model_read_selfheld | 23 | 10 | 0 | {"split_other_members_insufficient": 4, "comparator_uncertain": 5, "split_ambiguous_multiple_groups": 1} | 0 | 46 |
| fact_v4 | public_115 | 66 | 16 | 7 | {"comparator_uncertain": 8, "split_ambiguous_multiple_groups": 8} | 0 | 16 |
| fact_v4 | model_read_dev | 33 | 12 | 0 | {"split_ambiguous_multiple_groups": 6, "comparator_uncertain": 5, "comparator_different_event": 1} | 0 | 65 |
| fact_v4 | model_read_selfheld | 23 | 10 | 0 | {"split_other_members_insufficient": 4, "comparator_uncertain": 5, "split_ambiguous_multiple_groups": 1} | 0 | 46 |
| fact_v4_1 | public_115 | 66 | 16 | 7 | {"comparator_uncertain": 8, "split_ambiguous_multiple_groups": 8} | 0 | 16 |
| fact_v4_1 | model_read_dev | 33 | 12 | 0 | {"split_ambiguous_multiple_groups": 6, "comparator_uncertain": 5, "comparator_different_event": 1} | 0 | 65 |
| fact_v4_1 | model_read_selfheld | 23 | 10 | 0 | {"split_other_members_insufficient": 4, "comparator_uncertain": 5, "split_ambiguous_multiple_groups": 1} | 0 | 46 |
| fact_v4_1_1 | public_115 | 66 | 16 | 7 | {"comparator_uncertain": 8, "split_ambiguous_multiple_groups": 8} | 0 | 16 |
| fact_v4_1_1 | model_read_dev | 33 | 12 | 0 | {"split_ambiguous_multiple_groups": 6, "comparator_uncertain": 5, "comparator_different_event": 1} | 0 | 65 |
| fact_v4_1_1 | model_read_selfheld | 23 | 10 | 0 | {"split_other_members_insufficient": 4, "comparator_uncertain": 5, "split_ambiguous_multiple_groups": 1} | 0 | 46 |

## 标签负例被分进同组（疑似污染组）
- exact_v1: 无
- fact_v1: G000644（3 篇：Robinhood Assets Listing: $TOR / Robinhood Assets Listing: $AI  / Robinhood Assets Listing: $PRI）; G000695（3 篇：皮查伊：新 AI 模型 Carbon 是谷歌最强模型 / 皮查伊：新 AI 模型 Carbon 正与美政府测试 / 皮查伊：新 AI 模型 Carbon 首推网络安全合作伙伴）
- fact_v2: 无
- fact_v3: 无
- fact_v4: 无
- fact_v4_1: 无
- fact_v4_1_1: 无

## 公开 4 类同事件主题的最终组数
| run | ETH旧钱包转账 | Prabang被黑 | Allium安全事件与恢复 | 韩股同一行情快报 |
|---|---|---|---|---|
| exact_v1 | 3篇→3组 | 3篇→1组 | 6篇→4组 | 2篇→1组 |
| fact_v1 | 3篇→1组 | 3篇→1组 | 6篇→1组 | 2篇→1组 |
| fact_v2 | 3篇→1组 | 3篇→1组 | 6篇→1组 | 2篇→1组 |
| fact_v3 | 3篇→1组 | 3篇→1组 | 6篇→1组 | 2篇→1组 |
| fact_v4 | 3篇→1组 | 3篇→1组 | 6篇→1组 | 2篇→1组 |
| fact_v4_1 | 3篇→1组 | 3篇→1组 | 6篇→1组 | 2篇→1组 |
| fact_v4_1_1 | 3篇→1组 | 3篇→1组 | 6篇→1组 | 2篇→1组 |

## 耗时（秒；主评测分母只用 main 段，预热段单列）
| run | 解析快照 | 背景表 | 单篇预处理 main / warmup | 配对比较 main / warmup | 归组 main / warmup |
|---|---|---|---|---|---|
| exact_v1 | 0.0581 | 0.0 | 0.0004 / 0.0 | 9.8713 / 0.1675 | 0.0545 / 0.0011 |
| fact_v1 | 0.0424 | 0.9075 | 1.9512 / 0.0611 | 20.0206 / 0.4609 | 0.105 / 0.0014 |
| fact_v2 | 0.0832 | 0.9438 | 2.0262 / 0.0635 | 20.5153 / 0.4382 | 0.0876 / 0.0016 |
| fact_v3 | 0.0424 | 0.8809 | 1.8861 / 0.0611 | 20.0941 / 0.4403 | 0.0854 / 0.0014 |
| fact_v4 | 0.044 | 0.8799 | 1.9233 / 0.0625 | 20.244 / 0.427 | 0.0856 / 0.0014 |
| fact_v4_1 | 0.0489 | 0.9262 | 1.9456 / 0.0676 | 20.6157 / 0.4918 | 0.0841 / 0.0015 |
| fact_v4_1_1 | 0.0434 | 0.7916 | 2.1112 / 0.0641 | 20.5972 / 0.4747 | 0.0933 / 0.0014 |
