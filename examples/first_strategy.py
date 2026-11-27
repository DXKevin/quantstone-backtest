# 示例：盘后准备股票池，下一交易日根据行情买入。
# history() 只返回当前时点已经结束的 K 线。
def init(context):
    context.state.pool = []
    context.state.buy_order = None
    context.log("策略初始化")


def after_trading(context):
    context.state.pool = list(context.symbols)


def handle_bar(context, bars):
    if not context.state.pool or context.state.buy_order:
        return
    symbol = context.state.pool[0]
    if symbol in bars:
        context.state.buy_order = context.order(symbol, "buy", 100)


def on_trade(context, trade):
    context.log(f"{trade.symbol} {trade.side} {trade.quantity}股 @ {trade.price}")
