# Python 策略接口

策略是包含约定函数的 Python 源码文件，可在 Web 编辑器中运行。函数均为普通同步函数。普通策略只需处理初始化、盘前、行情和盘后阶段，不需要处理委托回报或成交回报。至少实现盘前、行情或盘后回调之一。

```python
def init(context):
    context.state.pool = []
    context.state.ordered = False


def after_trading(context):
    # 可以在此查询已经结束的当天日线，生成次日股票池。
    context.state.pool = list(context.symbols)


def handle_bar(context, bars):
    if not context.state.pool or context.state.ordered:
        return
    symbol = context.state.pool[0]
    if symbol in bars:
        context.order(symbol, "buy", 100)
        context.state.ordered = True
```

### 基础生命周期

| 接口 | 说明 |
| --- | --- |
| `init(context)` | 整个回测执行一次；可读取预热历史 |
| `before_trading(context)` | 每个交易日 09:00 执行，先完成可卖数量结转 |
| `handle_bar(context, bars)` | 驱动频率 K 线结束后执行；bars 为股票代码到不可变 Bar 的只读映射 |
| `after_trading(context)` | 每个交易日 15:01 执行 |
| `on_finish(context)` | 所有交易日结束、剩余订单到期后执行，禁止再下单 |

### 可选高级回调

系统内部始终维护订单和成交生命周期，用于账户记账、结果展示和审计。普通策略不必实现下面两个回调；只有需要根据拒单、撤单或成交结果立即采取额外行动的策略才使用它们。

| 接口 | 说明 |
| --- | --- |
| `on_order(context, order)` | 接受、拒绝、成交、撤销、到期回报；得到订单副本 |
| `on_trade(context, trade)` | 成交记账完成后执行，此时账户已经更新 |

`context.now` 为有时区模拟时间。`context.symbols` 是任务配置股票池；`context.frequency` 为 `1d` 或 `1m`。自定义状态放到 `context.state`。

`context.history(symbol, count=20, frequency=None)` 返回已完成 Bar 的 tuple。数量不足时返回已有数据，不回填未来数据。分钟任务可以指定 `frequency="1d"`。目前日线任务不加载分钟线；所有任务的分钟历史都不预取开始日前数据。

`context.order(symbol, side, quantity, limit_price=None)` 返回订单编号；普通策略可以忽略返回值。股票必须属于任务股票池，side 为 `buy/sell`，quantity 单位为股。无 limit_price 时按下一次开盘价尝试成交；有 limit_price 时只在开盘价满足限价时尝试成交。账户拒绝会保留订单记录和原因，并可在网页订单明细或 `context.orders()` 中查询；参数格式错误直接抛异常，使策略失败。

`context.cancel(order_id)` 返回是否撤销成功；终态订单返回 False。`context.orders()` 返回订单列表副本。

`context.portfolio` 是字典快照，含 initial_cash/cash/available_cash/frozen_cash/market_value/equity/net_value/realized_pnl/unrealized_pnl/total_pnl/total_fees/positions。内部金额是 Decimal，API 导出时为字符串，避免隐式浮点误差。

`context.log(message)` 记录带模拟时间的日志；最多 10000 条，每条最多 4000 字符。标准 print 输出也会被捕获并限制大小。

初始账户为空，不支持融资融券和外部入出金。本版策略依赖使用工作进程所在 Python 环境；依赖安装由项目维护者管理。
