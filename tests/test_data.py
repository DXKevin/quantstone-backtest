from datetime import date
from decimal import Decimal

import pytest

from quantstone_backtest.data import DAILY, MINUTE, CALENDAR, FACTORS, LIMITS, row_bar, sdk_data, demo_data


def row(day=2, **values):
    return dict(ts_code='000001.SZ', trade_date=date(2026, 11, day), open=10, high=11, low=9, close=10, vol=200, volume=200, bar_time=93000, **values)


class Table:
    def __init__(self, rows): self.rows = rows
    def to_pylist(self): return self.rows


class Client:
    def __init__(self, changed=False, missing_minute=False):
        self.changed, self.missing_minute = changed, missing_minute
    def read(self, dataset, **kwargs):
        if dataset == CALENDAR:
            rows = [dict(cal_date=date(2026, 11, d), is_open=1) for d in (2, 3)]
        elif dataset == DAILY:
            rows = [row(d) for d in (2, 3)]
        elif dataset == FACTORS:
            rows = [dict(trade_date=date(2026, 11, d), adj_factor=2 if self.changed and d == 3 else 1) for d in (2, 3)]
        elif dataset == LIMITS:
            rows = [dict(ts_code='000001.SZ', trade_date=date(2026, 11, d), up_limit=11, down_limit=9) for d in (2, 3)]
        elif dataset == MINUTE:
            rows = [] if self.missing_minute else [{**row(d), 'bar_time': 93100} for d in (2, 3)]
        return Table(rows)


def test_minute_start_time_and_lot_conversion():
    b = row_bar(row(), '1m')
    assert b.start.strftime('%H:%M') == '09:30'
    assert b.end.strftime('%H:%M') == '09:31'
    assert b.volume == 20000


def test_daily_session_times():
    b = row_bar(row(), '1d')
    assert b.start.hour == 9 and b.end.hour == 15


def test_sdk_reads_daily_and_minutes():
    bundle = sdk_data(Client(), date(2026, 11, 2), date(2026, 11, 3), ['000001.SZ'], '1m')
    assert len(bundle.bars) == 4
    assert bundle.bars[0].upper_limit == Decimal(11)


def test_corporate_action_fails_explicitly():
    with pytest.raises(ValueError, match='复权因子变化'):
        sdk_data(Client(changed=True), date(2026, 11, 2), date(2026, 11, 3), ['000001.SZ'], '1d')


def test_missing_minute_days_fail():
    with pytest.raises(ValueError, match='缺少分钟数据'):
        sdk_data(Client(missing_minute=True), date(2026, 11, 2), date(2026, 11, 3), ['000001.SZ'], '1m')


def test_demo_deterministic_and_has_240_minutes_per_day():
    args = (date(2026, 11, 2), date(2026, 11, 3), ['000001.SZ'], '1m')
    a, b = demo_data(*args), demo_data(*args)
    assert a == b
    assert len([bar for bar in a.bars if bar.frequency == '1m']) == 720


def test_demo_prices_are_window_independent_and_have_no_long_term_drift():
    short = demo_data(date(2026, 11, 2), date(2026, 11, 3), ['000001.SZ'], '1d')
    long = demo_data(date(2022, 1, 1), date(2026, 11, 3), ['000001.SZ'], '1d')
    short_close = next(bar.close for bar in short.bars if bar.start.date() == date(2026, 11, 2))
    long_close = next(bar.close for bar in long.bars if bar.start.date() == date(2026, 11, 2))
    prices = [bar.close for bar in long.bars]
    assert short_close == long_close
    assert max(prices) - min(prices) < Decimal('2')


def test_minute_end_label_maps_to_prior_open():
    b = row_bar({**row(), "bar_time": 150000}, "1m", minute_label="end")
    assert b.start.strftime("%H:%M") == "14:59"
    assert b.end.strftime("%H:%M") == "15:00"


def test_calendar_snapshot_has_no_date_filter():
    class SnapshotClient(Client):
        def read(self, dataset, **kwargs):
            if dataset == CALENDAR:
                assert "start" not in kwargs and "end" not in kwargs
            return super().read(dataset, **kwargs)
    sdk_data(SnapshotClient(), date(2026, 11, 2), date(2026, 11, 3), ["000001.SZ"], "1d")
