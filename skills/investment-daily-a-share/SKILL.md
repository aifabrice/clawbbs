---
name: investment-daily-a-share
description: A股“投资今日持仓”自动化技能。维护一个可执行的日频组合：每日生成关注清单与目标持仓、按交易成本执行调仓模拟、收盘回测策略表现、早晨输出持仓与收益日报。适用于希望用规则化流程管理短中线A股组合的场景。
metadata:
  openclaw:
    requires:
      bins: ["python3"]
      packages: ["requests"]
---

# 投资今日持仓（A股）

这个技能做三件事：

1. **每日关注清单**：从候选池中按规则打分，产出当日优先关注标的。
2. **组合调仓模拟**：考虑交易摩擦成本（佣金、印花税、过户费、滑点）执行调仓。
3. **日报+回测**：
   - 早晨：输出当前持仓、盈亏、风险提示。
   - 收盘后：输出当日交易、组合净值、近期开窗回测。

> 说明：这是规则化辅助决策，不是绝对投资建议。你可以改参数，让风格更激进或更保守。

---

## 默认路径

- 技能目录：`<workspace>/skills/investment-daily-a-share`
- 运行状态：`~/.clawdbot/skills/investment_daily_a_share/`
  - `state.json`：持仓与资金
  - `reports/`：日报
  - `backtest_history.json`：回测记录

---

## 手动执行

```bash
# 早晨报告（持仓、收益、关注清单）
{baseDir}/scripts/run.sh morning

# 收盘任务（调仓模拟 + 成本计入 + 回测）
{baseDir}/scripts/run.sh close

# 查看当前状态快照
{baseDir}/scripts/run.sh status
```

---

## 核心规则（默认）

- 候选池：`config.py` 中 `DEFAULT_UNIVERSE`
- 持仓上限：`MAX_POSITIONS = 5`
- 初始资金：`INITIAL_CASH = 200000`
- 成本模型：
  - 买入：佣金 + 过户费 + 滑点
  - 卖出：佣金 + 过户费 + 印花税 + 滑点
- 信号逻辑：
  - 趋势（MA20/MA60）
  - 动量（5日/20日收益）
  - 新进仅允许 Top5（`BUY_TOP_RANK`）
  - 已持仓在 Top8 内继续持有（`HOLD_BUFFER_RANK`）
- 风险约束：
  - 每只股票目标等权
  - 预留现金缓冲
  - 100股整手
  - 主调仓日仅周一/周三/周五（其余交易日只更新估值与回测）

---

## Cron 建议

### 1) 早晨播报（工作日 08:45）

```bash
node /Users/fabrice/Desktop/agi/OpenClaw/openclaw.mjs cron add \
  --name "A股投资今日持仓-早报" \
  --cron "45 8 * * 1-5" \
  --tz "Asia/Shanghai" \
  --session main \
  --session-key "agent:main:feishu:group:oc_9da39cfddec61f814c607bb689ff1d3a" \
  --system-event "运行 investment-daily-a-share 早晨任务：执行 /Users/fabrice/.openclaw/workspace/skills/investment-daily-a-share/scripts/run.sh morning，并把简版结果发到当前群。"
```

### 2) 收盘回测（工作日 15:10）

```bash
node /Users/fabrice/Desktop/agi/OpenClaw/openclaw.mjs cron add \
  --name "A股投资今日持仓-收盘回测" \
  --cron "10 15 * * 1-5" \
  --tz "Asia/Shanghai" \
  --session main \
  --session-key "agent:main:feishu:group:oc_9da39cfddec61f814c607bb689ff1d3a" \
  --system-event "运行 investment-daily-a-share 收盘任务：执行 /Users/fabrice/.openclaw/workspace/skills/investment-daily-a-share/scripts/run.sh close，并把持仓、调仓、成本、回测摘要发到当前群。"
```

---

## 可调参数

在 `scripts/config.py` 里可改：

- `DEFAULT_UNIVERSE`：候选股票池
- `MAX_POSITIONS`：最多持仓数
- `COMMISSION_RATE / STAMP_DUTY_SELL_RATE / SLIPPAGE_RATE`：成本参数
- `MIN_SCORE_TO_BUY`：入选分数阈值
- `BUY_TOP_RANK`：新进持仓允许的最高排名（默认 Top5）
- `HOLD_BUFFER_RANK`：旧持仓容忍排名（默认 Top8）
- `MAIN_REBALANCE_WEEKDAYS`：主调仓日（默认周一/周三/周五）
- `CASH_BUFFER_RATE`：现金缓冲
- `REBALANCE_BAND`：调仓带宽（越小越频繁）

如果你说“调得更激进/更保守”，就改这些参数。
