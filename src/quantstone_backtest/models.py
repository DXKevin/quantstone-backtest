from __future__ import annotations

from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
ZERO = Decimal("0")
CENT = Decimal("0.01")
SHARE = Decimal("0.000001")


def decimal(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("数值必须有限")
    return result


def money(value: Any) -> Decimal:
    return decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def shares(value: Any) -> Decimal:
    """Company-action share precision; regular orders remain integer-valued."""
    return decimal(value).quantize(SHARE, rounding=ROUND_HALF_UP)


def display_shares(value: Any) -> int | Decimal:
    value = decimal(value)
    return int(value) if value == value.to_integral() else value.normalize()


def jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {f.name: jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(v) for v in value]
    return value


@dataclass(frozen=True)
class Bar:
    symbol: str
    start: datetime
    end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = 0  # shares, never used as future liquidity at the open
    frequency: str = "1d"
    upper_limit: Decimal | None = None
    lower_limit: Decimal | None = None
    adjustment_ratio: Decimal = Decimal("1")

    def __post_init__(self):
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise ValueError("K线必须包含有时区的起止时间")
        if self.start.date() != self.end.date():
            raise ValueError("K线不得跨交易日")
        if self.frequency not in ("1d", "1m"):
            raise ValueError("仅支持 1d/1m")
        for name in ("open", "high", "low", "close"):
            object.__setattr__(self, name, decimal(getattr(self, name)))
        for name in ("upper_limit", "lower_limit"):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, decimal(getattr(self, name)))
        object.__setattr__(self, "adjustment_ratio", decimal(self.adjustment_ratio))
        if self.adjustment_ratio <= ZERO:
            raise ValueError("复权比例必须大于零")
        if self.low <= ZERO or self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("非法 OHLC 价格")
        if self.volume < 0:
            raise ValueError("成交量不能为负数")


@dataclass(frozen=True)
class FeeModel:
    commission_rate: Decimal = Decimal("0.0003")
    minimum_commission: Decimal = Decimal("5")
    sell_tax_rate: Decimal = Decimal("0.0005")
    transfer_rate: Decimal = Decimal("0.00001")

    def __post_init__(self):
        for f in fields(self):
            v = decimal(getattr(self, f.name))
            if v < ZERO:
                raise ValueError("费用参数不得为负")
            object.__setattr__(self, f.name, v)

    def calculate(self, side: str, notional: Decimal) -> dict[str, Decimal]:
        return {
            "commission": money(max(notional * self.commission_rate, self.minimum_commission)),
            "tax": money(notional * self.sell_tax_rate) if side == "sell" else ZERO,
            "transfer": money(notional * self.transfer_rate),
        }

    def total(self, side: str, notional: Decimal) -> Decimal:
        return sum(self.calculate(side, notional).values(), ZERO)


@dataclass
class Order:
    id: str
    symbol: str
    side: str
    quantity: int | Decimal
    created_at: datetime
    reference_price: Decimal
    limit_price: Decimal | None = None
    status: str = "accepted"
    reason: str = ""
    filled_quantity: int | Decimal = 0
    updated_at: datetime | None = None
    sizing: str = "fixed"


@dataclass(frozen=True)
class Fill:
    id: str
    order_id: str
    symbol: str
    side: str
    quantity: int | Decimal
    price: Decimal
    timestamp: datetime
    fees: dict[str, Decimal]


@dataclass
class Position:
    symbol: str
    quantity: Decimal = ZERO
    settled: Decimal = ZERO
    frozen: Decimal = ZERO
    cost: Decimal = ZERO  # total remaining cost, including acquisition fees
    price: Decimal = ZERO

    @property
    def available(self) -> Decimal:
        return self.settled - self.frozen

    def snapshot(self) -> dict:
        value = money(self.price * self.quantity)
        return {
            "symbol": self.symbol, "quantity": display_shares(self.quantity),
            "sellable": display_shares(self.available), "frozen": display_shares(self.frozen), "cost": self.cost,
            "average_cost": self.cost / self.quantity if self.quantity else ZERO,
            "price": self.price, "market_value": value, "unrealized_pnl": value - self.cost,
        }
