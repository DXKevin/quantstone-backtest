from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quantstone_backtest.config import RunConfig
from quantstone_backtest.demand_data import DemandData
from quantstone_backtest.engine import Engine
from quantstone_backtest.example import EXAMPLE
from quantstone_backtest.engine import load_strategy


def run(strategy, frequency="1d"):
    config = RunConfig(strategy=EXAMPLE, frequency=frequency, start=date(2026, 11, 2), end=date(2026, 11, 4))
    data = DemandData(config)
    engine = Engine([], strategy, frequency=frequency, start=config.start, end=config.end,
                    sessions=data.bundle.sessions, data_loader=data.load)
    return engine.run(), data


@pytest.mark.parametrize("frequency", ["1d", "1m"])
def test_clock_without_symbols_and_default_example(frequency):
    calls = []
    result, data = run(SimpleNamespace(handle_bar=lambda c: calls.append(c.now)), frequency)
    assert len(calls) == (3 if frequency == "1d" else 720)
    assert not data.cache
    assert result["loaded_symbols"] == []
    result, _ = run(load_strategy(EXAMPLE), frequency)
    assert len(result["trades"]) == 1
    assert result["loaded_symbols"] == ["000001.SZ"]


def test_first_minute_query_then_same_boundary_next_open():
    seen = []
    def handle(c):
        if not seen:
            symbol = "600000.SH"
            current = c.current(symbol)
            seen.append((c.now, current.end))
            assert all(b.end <= c.now for b in c.history(symbol, 100))
            assert c.current(symbol, "1d").end.date() < c.now.date()
            c.order(symbol, "buy", 100)
    result, data = run(SimpleNamespace(handle_bar=handle), "1m")
    assert seen[0][0] == seen[0][1]
    assert result["trades"][0]["timestamp"].startswith("2026-11-02T09:31")
    assert len(data.cache) == 1
    assert Decimal(result["account"]["positions"][0]["market_value"]) > 0


def test_direct_order_loads_symbol_and_code_controls_fees():
    def init(c):
        c.set_fees(commission_rate=0, minimum_commission=0, sell_tax_rate=0, transfer_rate=0)
    def before(c):
        if c.now.day == 2:
            c.order("600000.SH", "buy", 100)
        if c.now.day == 3:
            c.order("000002.SZ", "buy", 100)
    result, data = run(SimpleNamespace(init=init, before_trading=before))
    assert len(data.cache) == 2
    assert len(result["trades"]) == 2
    assert result["trades"][0]["timestamp"].startswith("2026-11-02T09:30")
    assert Decimal(result["account"]["total_fees"]) == 0
    assert result["effective_fees"]["minimum_commission"] == "0"


def test_after_close_loads_history_once_and_future_trade():
    observations = []
    def after(c):
        rows = c.history("600000.SH", 100)
        observations.append(len(rows))
        assert len({b.start for b in rows}) == len(rows)
        assert all(b.end <= c.now for b in rows)
        if c.now.day == 2:
            c.order("600000.SH", "buy", 100)
    result, _ = run(SimpleNamespace(after_trading=after))
    assert observations == [2, 3, 4]
    assert result["trades"][0]["timestamp"].startswith("2026-11-03T09:30")


def test_no_fee_changes_after_init_or_reserved_orders():
    with pytest.raises(ValueError, match="费用仅"):
        run(SimpleNamespace(handle_bar=lambda c: c.set_fees(minimum_commission=0)))
    def init(c):
        c.order("000001.SZ", "buy", 100)
        c.set_fees(minimum_commission=0)
    with pytest.raises(ValueError, match="费用仅"):
        run(SimpleNamespace(init=init))


def test_user_type_error_is_not_retried_as_legacy_callback():
    calls = []
    def handle(c):
        calls.append(c.now)
        raise TypeError("inside strategy")
    with pytest.raises(TypeError, match="inside strategy"):
        run(SimpleNamespace(handle_bar=handle))
    assert len(calls) == 1


def test_bad_symbol_and_unsupported_frequency_are_explicit():
    with pytest.raises(ValueError, match="暂不支持"):
        run(SimpleNamespace(handle_bar=lambda c: c.current("688001.SH")))
    with pytest.raises(ValueError, match="日线回测"):
        run(SimpleNamespace(handle_bar=lambda c: c.current("000001.SZ", "1m")))
