"""日K MA5 收盘价突破策略。

已完成日K收盘价向上突破 MA5 时全仓买入，向下跌破时清仓。
信号在收盘后产生，订单由回测引擎在下一根 K 线开盘尝试成交。
"""

SYMBOL = "000001.SZ"


def init(context):
    context.set_benchmark("000300.SH")
    context.set_fees(
        commission_rate=0.0003,
        minimum_commission=5,
        sell_tax_rate=0.0005,
        transfer_rate=0.00001,
    )
    context.log(f"MA5 策略初始化：{SYMBOL}")


def handle_bar(context):
    # 六根日K才能同时计算“上一日 MA5”和“当日 MA5”。
    bars = context.history(SYMBOL, 6, frequency="1d")
    if len(bars) < 6:
        return

    previous_close = bars[-2].close
    current_close = bars[-1].close
    previous_ma5 = sum(bar.close for bar in bars[-6:-1]) / 5
    current_ma5 = sum(bar.close for bar in bars[-5:]) / 5

    was_above = previous_close > previous_ma5
    is_above = current_close > current_ma5

    positions = context.portfolio["positions"]
    position = next((item for item in positions if item["symbol"] == SYMBOL), None)
    quantity = position["quantity"] if position else 0
    sellable = position["sellable"] if position else 0

    if not was_above and is_above and quantity == 0:
        context.order_all(SYMBOL, "buy")
        context.log(
            f"向上突破 MA5：close={current_close}, ma5={current_ma5}; "
            "按下一根日K开盘价全仓买入"
        )
    elif was_above and not is_above and sellable > 0:
        context.order_all(SYMBOL, "sell")
        context.log(
            f"向下跌破 MA5：close={current_close}, ma5={current_ma5}; "
            f"卖出 {sellable} 股"
        )
