# Python 策略接口

策略是包含约定函数的 Python 源码文件，可在 Web 编辑器中运行。函数均为普通同步函数。普通策略只需处理初始化、盘前、行情和盘后阶段，不需要处理委托回报或成交回报。至少实现盘前、行情或盘后回调之一。

```python
def init(context):
    context.set_fees(commission_rate=0.0003, minimum_commission=5,
                     sell_tax_rate=0.0005, transfer_rate=0.00001)
    context.state.ready = False
    context.state.ordered = False


def after_trading(context):
    # 可以在此查询当天已完成日线并保存自己的选股结果。
    context.state.ready = True


def handle_bar(context):
    if not context.state.ready or context.state.ordered:
        return
    symbol = "000001.SZ"
    if context.current(symbol) is not None:
        context.order(symbol, "buy", 100)
        context.state.ordered = True
```

### 基础生命周期

| 接口 | 说明 |
| --- | --- |
| `init(context)` | 整个回测执行一次；可读取预热历史 |
| `before_trading(context)` | 每个交易日 09:00 执行，先完成可卖数量结转 |
| `handle_bar(context)` | 日线于15:00调用；分钟按09:31–11:30、13:01–15:00调用；无预加载股票也会触发 |
| `after_trading(context)` | 每个交易日 15:01 执行 |
| `on_finish(context)` | 所有交易日结束、剩余订单到期后执行，禁止再下单 |

### 可选高级回调

系统内部始终维护订单和成交生命周期，用于账户记账、结果展示和审计。普通策略不必实现下面两个回调；只有需要根据拒单、撤单或成交结果立即采取额外行动的策略才使用它们。

| 接口 | 说明 |
| --- | --- |
| `on_order(context, order)` | 接受、拒绝、成交、撤销、到期回报；得到订单副本 |
| `on_trade(context, trade)` | 成交记账完成后执行，此时账户已经更新 |

`context.now` 为有时区模拟时间；`context.frequency` 为 `1d` 或 `1m`。自定义状态和选股结果放到 `context.state`。不要求配置股票池或订阅行情。旧双参数 `handle_bar(context, bars)` 兼容保留，bars 只含后台已加载股票在该时点结束的行情，可能为空；`context.symbols` 仅是已加载代码的兼容视图，不是全市场股票列表。

`context.current(symbol, frequency=None)` 返回最近已完成的 Bar，没有历史时返回 None。它不保证是当前交易日的 Bar；有停牌或缺口时应检查 Bar.end。首次查询或下单会由后台加载该股票，并自动继续回放、撮合和估值。

`context.set_fees(commission_rate=0.0003, minimum_commission=5, sell_tax_rate=0.0005, transfer_rate=0.00001)` 只允许在 init 中首次下单前调用；省略的参数使用上述默认值。不调用则使用默认费率。结果保存 effective_fees 及 loaded_symbols，便于复查。

`context.history(symbol, count=20, frequency=None)` 返回已完成 Bar 的 tuple。数量不足时返回已有数据，不回填未来数据。分钟任务可以指定 `frequency="1d"`。目前日线任务不加载分钟线；所有任务的分钟历史都不预取开始日前数据。

`context.order(symbol, side, quantity, limit_price=None)` 返回订单编号；普通策略可以忽略返回值。无需预先查询或订阅股票，side 为 `buy/sell`，quantity 单位为股。无 limit_price 时按下一次开盘价尝试成交；有 limit_price 时只在开盘价满足限价时尝试成交。没有已知参考价时需要限价，否则拒单。账户拒绝会保留订单记录和原因，并可在网页订单明细或 `context.orders()` 中查询；参数格式错误直接抛异常，使策略失败。

首版按需加载以单股票、完整任务区间为缓存单位；策略只能查询模拟时点已完成的部分。最多加载20只不同股票，分钟合计最多500000根、天数×股票数不超过2500。真实数据缺失或公司行动暂不支持的区间会明确失败。全市场选股数据接口尚未提供。

`context.cancel(order_id)` 返回是否撤销成功；终态订单返回 False。`context.orders()` 返回订单列表副本。

`context.portfolio` 是字典快照，含 initial_cash/cash/available_cash/frozen_cash/market_value/equity/net_value/realized_pnl/unrealized_pnl/total_pnl/total_fees/positions。内部金额是 Decimal，API 导出时为字符串，避免隐式浮点误差。

`context.log(message)` 记录带模拟时间的日志；最多 10000 条，每条最多 4000 字符。标准 print 输出也会被捕获并限制大小。

初始账户为空，不支持融资融券和外部入出金。本版策略依赖使用工作进程所在 Python 环境；依赖安装由项目维护者管理。
