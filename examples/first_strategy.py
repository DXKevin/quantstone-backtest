# 示例：策略自行决定交易对象，无需配置股票池或订阅行情。
def init(context):
    context.set_fees(
        commission_rate=0.0003,
        minimum_commission=5,
        sell_tax_rate=0.0005,
        transfer_rate=0.00001,
    )
    context.state.ready = False
    context.state.ordered = False
    context.log("策略初始化")


def after_trading(context):
    context.state.ready = True


def handle_bar(context):
    if not context.state.ready or context.state.ordered:
        return
    symbol = "000001.SZ"
    bar = context.current(symbol)
    if bar is not None:
        context.order(symbol, "buy", 100)
        context.state.ordered = True
