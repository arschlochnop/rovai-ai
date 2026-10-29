# 资讯同事件去重：严格前 50 篇窗口离线回放

独立离线实验：识别“同一具体事件的不同报道”，给出三态配对判断（`same_event / different_event / uncertain`）
和保守的临时事件归组。**保留全部文章与 ID，不删除资讯**；不连接数据库、不调用任何产品/生产接口、
不修改产品代码或配置、不部署。所有代码、日志、输出都在本目录内，输入文件只读。

结论、指标与仍然存在的错误见 [`reports/REPORT.md`](reports/REPORT.md)；各版本对照表（程序生成）见
[`reports/run_comparison.md`](reports/run_comparison.md)。

## 运行环境

- Python **3.13.17**（`/usr/bin/python3`，Linux x86_64）；**只用标准库**，无需安装任何第三方包。
- 语义 embedding **未运行**：本会话环境里没有本地模型，`huggingface.co` 被环境网络策略拒绝（HTTP 403）。
  若以后要做语义消融，需要在云环境设置的 Network access 里放行 `huggingface.co`
  （步骤：<https://code.claude.com/docs/en/cloud-environments#network-access>），再按 REPORT 第 9 节的约束补做。

## 输入

| 文件 | 本次实际路径 | sha256 |
|---|---|---|
| 快照（2606 篇） | `/root/.claude/uploads/ebbb30f4-339c-5eb8-8b28-c0460cd544c5/4706cc15-0-____.json` | `6fedb605…8dcd798` |
| 公开 115 对参考标签 | 同目录 `2a9d2854-______115_.json`（副本：`labels/public_reference/public_pairs_115.json`） | `d147a34e…8ee9249e` |
| 公开 4 类同事件主题 | 同目录 `bb64632e-1-______.json`（副本：`labels/public_reference/public_topics_4.json`） | `123ed651…8fc80fdc` |

快照本身不入库（含大量原文）；换路径运行时以 sha256 核对。上传路径在会话容器回收后可能失效。

## 目录

```
dedup/                核心模块
  snapshot.py         读取、严格时间解析（小数秒 1~6 位无损补齐到微秒，失败即停）、排序、窗口划分、比较器字段白名单
  textnorm.py         NFKC 规范化、千分位保护、通用样板剥离、分句、综述识别
  facts.py            单篇事实抽取：数值（符号/单位/量级/精度/角色/口径/主体/对手方/来源）、时间、地址、实体、表态
  background.py       背景词频表（只用回放起点之前的文章，只衡量稀有度，不提供事实支持）
  comparator_exact.py 精确基线（VERSION / DEFAULT_CONFIG / compare_articles）
  comparator_fact.py  文字/事实增强比较器（VERSION / DEFAULT_CONFIG / compare_articles）
  contract.py         比较器契约：非法 label、空 reason、缺 VERSION/DEFAULT_CONFIG、缺事实槽位均报错
  grouping.py         严格窗口保守顺序归组
  replay.py           回放驱动（全部 50 个候选都比较，不做 top-k）
  evaluate.py         配对层 / 最终归组层评测与守恒校验
run_replay.py         回放入口（输出目录必须不存在）
run_audit.py          独立执行核查（不导入回放/归组代码，从原始快照重新推导）
run_eval.py           标签评测（回放本身不读标签）
tools/                候选抽样、标签写出与家族划分、组导出、对照表、错误案例、复核模板、打包
labels/               开发核读标签 v1（冻结）、家族划分、公开标签副本、人工复核模板
runs/<run>/           每次回放一个目录：manifest.json / summary.json / assignments.jsonl /
                      pairs.jsonl.gz（部分）/ pairs_labels.tsv.gz / background_df.json.gz / audit/ / eval/ / run.log
reports/              REPORT.md、run_comparison.{md,json}、error_cases_fact_v4_1_1.md、label_free_stats.json
tests/test_core.py    单元测试
```

## 命令（以下路径对本次上传有效）

```bash
cd experiments/news_event_dedup
IN=/root/.claude/uploads/ebbb30f4-339c-5eb8-8b28-c0460cd544c5/4706cc15-0-____.json
PUB=labels/public_reference/public_pairs_115.json
TOP=labels/public_reference/public_topics_4.json

# 0. 单元测试
python3 -m unittest discover -s tests -v

# 1. 严格窗口回放（新目录；精确基线用 --comparator exact）
python3 run_replay.py --input "$IN" --comparator fact --out runs/my_run

# 2. 独立执行核查（写入 runs/my_run/audit/）
python3 run_audit.py --run runs/my_run --input "$IN"

# 3. 标签评测（写入 runs/my_run/eval/；评分后修订必须另开回放目录）
python3 run_eval.py --run runs/my_run --input "$IN" --public-pairs "$PUB" --topics "$TOP" \
    --dev-labels labels/dev_labels_v1.jsonl --dev-split labels/dev_split_v1.json

# 4. 汇总 / 错误案例 / 组导出 / 复核模板
python3 tools/compare_runs.py --out reports/run_comparison
python3 tools/error_cases.py --run runs/fact_v4_1_1 --input "$IN" --history runs/fact_v1 --out reports/error_cases_fact_v4_1_1.md
python3 tools/dump_groups.py runs/fact_v4_1_1 "$IN" /tmp/groups.txt
python3 tools/review_template.py --run runs/fact_v4_1_1 --input "$IN" --labels labels/dev_labels_v1.jsonl \
    --public-pairs "$PUB" --out-dir labels/human_review_new
```

`--replay-from snapshot_start` 可从快照第一篇起完整回放（诊断用；此时没有更早的文章可建背景表，
所有词项等权，结果不能与主评测直接比较；本次交付未使用）。

### 单独调用比较器

```python
import sys; sys.path.insert(0, "experiments/news_event_dedup")
from dedup import comparator_fact as cf
res = cf.compare_articles(left_article, right_article, cf.DEFAULT_CONFIG)   # 只读取白名单字段
print(res["label"], res["reason"])          # 三态 + 中文理由
print(res["evidence"]["facts"])             # 主体/动作/数值/时间/地址/来源槽位，未识别显式为 unknown
print(res["diagnostics"]["score_note"])     # 分数只是证据强度，不是同事件概率
```

不传背景表时所有词项等权（稀有度只影响锚点权重）；回放中使用的背景表见各 run 的 `background_df.json.gz`。
`story_id` 等非白名单字段即使传入也会被丢弃。

## 输出字段

- `pairs.jsonl`（每次比较一行）：`left_id/right_id`、`distance`（前序距离 1~50）、`phase`、`main_eval`（主评测标记）、
  `same_source`、`label`、`reason`、`reason_codes`、`final_evidence`（support/conflict/unknown/facts，最终有效事实证据）、
  `diagnostics`（tfidf 余弦、包含度、锚点分等基础诊断，与事实证据分开）、`grouping`（左右组、是否同组、
  same 对的归组结局：joined_same_group / split_ambiguous_multiple_groups / split_other_members_insufficient）。
- `assignments.jsonl`（每篇一行）：`action`（join/new/ambiguous_new）、`group_id`、`support_member_ids`
  （加入时该组当前窗口内全部可见成员）、`qualifying_groups`、`blocked_groups`（含每个非 same 成员及其标签）、窗口范围。
- `summary.json`：计数、守恒校验、归组结局、`story_id` 现状对照（预测完成后统计，非质量真值）。
- `manifest.json`：输入/代码（逐文件 sha256 + git 提交）/配置/背景表 hash、窗口顺序 hash、分阶段耗时、输出 hash。
  manifest 不包含自身 hash。

## 版本（全部保留，不覆盖）

| run | 比较器 | git 提交 | 说明 |
|---|---|---|---|
| exact_v1 | exact-1.0.0 | a904537 | 精确基线 |
| fact_v1 | fact-1.0.0 | a904537 | 首个冻结版本（看到开发标签结果之前） |
| fact_v2 | fact-2.0.0 | 17d5110 | 依据 v1 在 dev 上的真实误合并修订 |
| fact_v3 | fact-3.0.0 | 75ef95f | 修复 v2 引入的拆散回归（全组核读发现） |
| fact_v4 | fact-4.0.0 | 70954e5 | 表态分歧只阻止 same、不再断言 different |
| fact_v4_1 | fact-4.1.0 | e90aa1c | 单元测试发现的负号丢失缺陷 |
| **fact_v4_1_1** | **fact-4.1.1** | **6c22ca6** | **最终版**：证据顺序与背景表 gzip 确定化（判定逻辑不变） |

`tools/compare_runs.py` 会逐文件核对每个 run 记录的代码 hash 与其 git 提交一致。
v1–v4.1 的 `pairs.jsonl` 证据列表顺序依赖集合遍历顺序（标签与归组不受影响），因此只提交了
`pairs_labels.tsv.gz`；exact_v1、fact_v1、fact_v4_1_1 提交了完整 `pairs.jsonl.gz`（解压后 sha256 与 manifest 一致）。
v4.1.1 起回放输出跨进程逐字节一致（已用不同 `PYTHONHASHSEED` 验证）。
