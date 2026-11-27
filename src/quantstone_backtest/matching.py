from __future__ import annotations

from collections import deque
from dataclasses import replace

from .account import Account
from .models import Bar, Fill, Order, ZERO, decimal, money


class MatchingEngine:
    """Opening-price execution; no OHLC path or future bar-volume assumptions."""

    def __init__(self, account: Account):
        self.account = account
        self.orders: dict[str, Order] = {}
        self.fills: list[Fill] = []
        self.reports = deque()

    def submit(self, symbol, side, quantity, timestamp, reference_price, limit_price=None) -> str:
        if side not in ("buy", "sell"):
            raise ValueError("side 必须是 buy 或 sell")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("quantity 必须为正整数股数")
        price = decimal(limit_price) if limit_price is not None else None
        if price is not None and price <= ZERO:
            raise ValueError("限价必须大于零")
        reference = decimal(reference_price) if reference_price is not None else price
        order = Order(f"O{len(self.orders) + 1:06d}", symbol, side, quantity, timestamp, reference or ZERO, price)
        self.orders[order.id] = order
        try:
            if len(self.orders) > 100_000:
                raise ValueError("单次回测订单超过上限")
            if reference is None or reference <= ZERO:
                raise ValueError("无已知参考价，请先等待行情或提供限价")
            if side == "buy" and quantity % 100:
                raise ValueError("本版买入数量必须为 100 股的整数倍")
            if side == "sell" and quantity % 100:
                p = self.account.positions.get(symbol)
                if p is None or quantity != p.available:
                    raise ValueError("零股卖出必须一次卖完可卖持仓")
            self.account.reserve(order)
        except ValueError as exc:
            order.status = "rejected"
            order.reason = str(exc)
        order.updated_at = timestamp
        self.reports.append(("order", replace(order)))
        return order.id

    def cancel(self, order_id, timestamp):
        order = self.orders.get(order_id)
        if order is None:
            raise ValueError("订单不存在")
        if order.status != "accepted":
            return False
        self._finish(order, "cancelled", "策略撤单", timestamp)
        return True

    def _finish(self, order, status, reason, timestamp):
        self.account.release(order)
        order.status, order.reason, order.updated_at = status, reason, timestamp
        self.reports.append(("order", replace(order)))

    def match(self, bars: dict[str, Bar], timestamp):
        # Snapshot before executing any order. Strategy callbacks cannot join this open.
        eligible = [o for o in self.orders.values() if o.status == "accepted" and o.symbol in bars]
        self.account.mark({s: b.open for s, b in bars.items()})
        for order in eligible:
            bar = bars[order.symbol]
            price = bar.open
            if order.limit_price is not None:
                if order.side == "buy" and price > order.limit_price:
                    continue
                if order.side == "sell" and price < order.limit_price:
                    continue
            # Conservative model: do not assume a fill at the adverse daily limit.
            if order.side == "buy" and bar.upper_limit is not None and price >= bar.upper_limit:
                continue
            if order.side == "sell" and bar.lower_limit is not None and price <= bar.lower_limit:
                continue
            if not self.account.can_pay(order, price):
                self._finish(order, "rejected", "开盘价格变化后资金不足", timestamp)
                continue
            notional = money(price * order.quantity)
            fill = Fill(f"F{len(self.fills) + 1:06d}", order.id, order.symbol, order.side,
                        order.quantity, price, timestamp, self.account.fees.calculate(order.side, notional))
            self.account.apply_fill(order, fill)
            self.fills.append(fill)
            order.status, order.filled_quantity, order.updated_at = "filled", order.quantity, timestamp
            self.reports.append(("order", replace(order)))
            self.reports.append(("trade", fill))

    def expire(self, timestamp):
        for order in self.orders.values():
            if order.status == "accepted":
                self._finish(order, "expired", "回测结束，未成交", timestamp)
