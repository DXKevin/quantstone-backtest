from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class RunConfig(BaseModel):
    instrument_type: Literal["stock"] = "stock"
    market: Literal["A股"] = "A股"
    strategy_id: str | None = Field(default=None, min_length=32, max_length=32)
    name: str = Field(default="新回测", min_length=1, max_length=100)
    strategy: str = Field(min_length=1, max_length=200_000)
    source: Literal["demo", "sdk"] = "demo"
    frequency: Literal["1d", "1m"] = "1d"
    minute_label: Literal["start", "end"] = "end"
    start: date = date(2026, 11, 2)
    end: date = date(2026, 11, 13)
    # Compatibility only: new strategies request symbols through context queries/orders.
    symbols: list[str] = Field(default_factory=list, max_length=20)
    initial_cash: Decimal = Field(default=Decimal("1000000"), gt=0, le=Decimal("1000000000000"))
    commission_rate: Decimal = Field(default=Decimal("0.0003"), ge=0, le=1)
    minimum_commission: Decimal = Field(default=Decimal("5"), ge=0, le=100000)
    sell_tax_rate: Decimal = Field(default=Decimal("0.0005"), ge=0, le=1)
    transfer_rate: Decimal = Field(default=Decimal("0.00001"), ge=0, le=1)

    @field_validator("strategy_id")
    @classmethod
    def valid_strategy_id(cls, identifier):
        if identifier is not None and any(c not in "0123456789abcdef" for c in identifier):
            raise ValueError("strategy_id 必须是 32 位小写十六进制标识")
        return identifier

    @field_validator("symbols")
    @classmethod
    def symbols_supported(cls, symbols):
        import re
        result = list(dict.fromkeys(s.strip().upper() for s in symbols))
        for s in result:
            if not re.fullmatch(r"(?:(?:000|001|002|003|300|301)\d{3}\.SZ|(?:600|601|603|605)\d{3}\.SH)", s):
                raise ValueError(f"首版支持沪深主板及创业板股票，暂不支持此代码：{s}")
        return result

    @model_validator(mode="after")
    def valid_range(self):
        if self.end < self.start:
            raise ValueError("结束日期不能早于开始日期")
        if (self.end - self.start).days > 3660:
            raise ValueError("单次区间最多十年")
        if self.frequency == "1m" and (self.end - self.start).days * len(self.symbols) > 2500:
            raise ValueError("分钟回测请缩小股票池或时间区间（天数×股票数≤2500）")
        return self
