from __future__ import annotations

from decimal import Decimal

from .models import FeeModel, Fill, Order, Position, ZERO, decimal, display_shares, money, shares


class Account:
    """Single owner of assets; execution reports are applied exactly once."""

    def __init__(self, initial_cash, fees: FeeModel | None = None):
        self.initial_cash = money(initial_cash)
        if self.initial_cash <= ZERO:
            raise ValueError("初始资金必须大于零")
        self.cash = self.initial_cash
        self.fees = fees or FeeModel()
        self.positions: dict[str, Position] = {}
        self.reservations: dict[str, Decimal] = {}
        self.sell_reservations: dict[str, tuple[str, int | Decimal]] = {}
        self.applied_fills: set[str] = set()
        self.total_fees = ZERO
        self.realized_pnl = ZERO

    @property
    def frozen_cash(self):
        return sum(self.reservations.values(), ZERO)

    @property
    def available_cash(self):
        return self.cash - self.frozen_cash

    def new_session(self):
        for p in self.positions.values():
            p.settled = p.quantity

    def reinvest_distribution(self, symbol: str, ratio) -> dict:
        """Model an adjustment-factor change as fractional-share reinvestment."""
        ratio = decimal(ratio)
        if ratio <= ZERO:
            raise ValueError("复权比例必须大于零")
        p = self.positions.get(symbol)
        before = p.quantity if p else ZERO
        if p and p.quantity:
            p.quantity = shares(p.quantity * ratio)
            p.settled = shares(p.settled * ratio)
            p.frozen = shares(p.frozen * ratio)
        return {
            "symbol": symbol,
            "ratio": ratio,
            "quantity_before": display_shares(before),
            "quantity_after": display_shares(p.quantity if p else ZERO),
        }

    def reserve(self, order: Order):
        if order.side == "buy":
            if order.sizing == "all_cash":
                self.reservations[order.id] = self.available_cash
                return
            price = order.limit_price or order.reference_price
            amount = money(price * order.quantity)
            required = amount + self.fees.total("buy", amount)
            if required > self.available_cash:
                raise ValueError("可用资金不足")
            self.reservations[order.id] = required
        else:
            p = self.positions.get(order.symbol)
            if p is None or order.quantity > p.available:
                raise ValueError("可卖持仓不足（含 T+1 或冻结限制）")
            p.frozen += order.quantity
            self.sell_reservations[order.id] = (order.symbol, order.quantity)

    def release(self, order: Order):
        self.reservations.pop(order.id, None)
        reservation = self.sell_reservations.pop(order.id, None)
        if reservation:
            symbol, quantity = reservation
            self.positions[symbol].frozen -= quantity

    def can_pay(self, order: Order, price: Decimal) -> bool:
        notional = money(price * order.quantity)
        if order.side == "sell":
            return self.cash + notional >= self.fees.total("sell", notional)
        usable = self.available_cash + self.reservations.get(order.id, ZERO)
        return notional + self.fees.total("buy", notional) <= usable

    def apply_fill(self, order: Order, fill: Fill):
        if fill.id in self.applied_fills:
            return
        if (fill.order_id, fill.symbol, fill.side, fill.quantity) != (order.id, order.symbol, order.side, order.quantity):
            raise ValueError("成交与委托不一致")
        if not self.can_pay(order, fill.price):
            raise ValueError("成交资金不足")
        notional = money(fill.price * fill.quantity)
        fee = sum(fill.fees.values(), ZERO)
        p = self.positions.setdefault(fill.symbol, Position(fill.symbol))
        self.release(order)
        if fill.side == "buy":
            self.cash -= notional + fee
            p.cost += notional + fee
            p.quantity += fill.quantity
        else:
            cost_removed = p.cost if fill.quantity == p.quantity else money(p.cost * fill.quantity / p.quantity)
            self.cash += notional - fee
            p.cost -= cost_removed
            p.quantity -= fill.quantity
            p.settled -= fill.quantity
            self.realized_pnl += fill.realized_pnl
        p.price = fill.price
        self.total_fees += fee
        self.applied_fills.add(fill.id)

    def sale_realized_pnl(self, symbol, quantity, price, fees):
        p = self.positions[symbol]
        notional = money(price * quantity)
        cost_removed = p.cost if quantity == p.quantity else money(p.cost * quantity / p.quantity)
        return notional - sum(fees.values(), ZERO) - cost_removed

    def mark(self, prices: dict[str, Decimal]):
        for symbol, price in prices.items():
            if symbol in self.positions:
                self.positions[symbol].price = price

    def snapshot(self) -> dict:
        positions = [p.snapshot() for p in self.positions.values() if p.quantity]
        value = sum((p["market_value"] for p in positions), ZERO)
        equity = self.cash + value
        return {
            "initial_cash": self.initial_cash, "cash": self.cash,
            "available_cash": self.available_cash, "frozen_cash": self.frozen_cash,
            "market_value": value, "equity": equity, "net_value": equity / self.initial_cash,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": sum((p["unrealized_pnl"] for p in positions), ZERO),
            "total_pnl": equity - self.initial_cash, "total_fees": self.total_fees,
            "positions": positions,
        }
