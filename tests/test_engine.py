from datetime import datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from quantstone_backtest.account import Account
from quantstone_backtest.engine import Engine, load_strategy
from quantstone_backtest.matching import MatchingEngine
from quantstone_backtest.models import Bar, FeeModel, SHANGHAI

FREE = FeeModel(0, 0, 0, 0)
S = '000001.SZ'


def bar(day=2, op=10, close=10, minute=None, symbol=S, **kwargs):
    start = datetime(2026, 11, day, 9, 30, tzinfo=SHANGHAI)
    if minute is None:
        end = start.replace(hour=15, minute=0)
    else:
        start += timedelta(minutes=minute)
        end = start + timedelta(minutes=1)
    return Bar(symbol, start, end, D(op), D(max(op, close)), D(min(op, close)), D(close),
               frequency='1d' if minute is None else '1m', **kwargs)


def test_next_daily_open_and_no_future_history():
    observations = []
    def handle(c, bars):
        observations.append((c.now.day, [b.end.day for b in c.history(S)]))
        if c.now.day == 2:
            c.order(S, 'buy', 100)
    engine = Engine([bar(2, 10, 12), bar(3, 13, 14)], SimpleNamespace(handle_bar=handle), fees=FREE)
    result = engine.run()
    assert observations == [(2, [2]), (3, [2, 3])]
    assert result['trades'][0]['price'] == '13'
    assert '2026-11-03T09:30' in result['trades'][0]['timestamp']
    assert D(result['account']['total_pnl']) == 100


def test_progress_publishes_each_completed_equity_point():
    updates = []
    Engine([bar(2), bar(3, close=11)], SimpleNamespace(), fees=FREE, progress=updates.append).run()
    assert [item['completed'] for item in updates] == [1, 2]
    assert updates[-1]['equity_point']['date'] == '2026-11-03'
    assert updates[-1]['equity_point']['net_value'] == D('1')


def test_adjacent_minute_close_then_next_open():
    seen = []
    def handle(c, bars):
        if len(c.history(S)) == 1:
            c.order(S, 'buy', 100)
    def trade(c, fill):
        seen.append((len(c.history(S)), c.portfolio['positions'][0]['quantity']))
    result = Engine([bar(minute=0), bar(op=11, close=12, minute=1)],
                    SimpleNamespace(handle_bar=handle, on_trade=trade), frequency='1m', fees=FREE).run()
    assert result['trades'][0]['timestamp'].startswith('2026-11-02T09:31')
    assert result['trades'][0]['price'] == '11'
    assert seen == [(1, 100)]


def test_before_after_trading_and_daily_history_in_minute_run():
    observed = []
    def before(c):
        observed.append(('before', c.now.day, len(c.history(S, frequency='1d'))))
    def after(c):
        observed.append(('after', c.now.day, len(c.history(S, frequency='1d'))))
        if c.now.day == 2:
            c.order(S, 'buy', 100)
    result = Engine([bar(2), bar(3), bar(2, minute=0), bar(3, op=11, minute=0)],
                    SimpleNamespace(before_trading=before, after_trading=after), frequency='1m', fees=FREE).run()
    assert observed == [('before', 2, 0), ('after', 2, 1), ('before', 3, 1), ('after', 3, 2)]
    assert result['trades'][0]['price'] == '11'


def test_t_plus_one_and_fee_accounting():
    ids = []
    def handle(c, bars):
        if c.now.day == 2:
            ids.append(c.order(S, 'buy', 100))
        if c.now.day == 3:
            ids.append(c.order(S, 'sell', 100))  # bought this morning: reject
    def before(c):
        if c.now.day == 4:
            ids.append(c.order(S, 'sell', 100))
    result = Engine([bar(2), bar(3, 10), bar(4, 12)],
                    SimpleNamespace(handle_bar=handle, before_trading=before), initial_cash=2000).run()
    assert [o['status'] for o in result['orders']] == ['filled', 'rejected', 'filled']
    assert len(result['trades']) == 2
    assert D(result['account']['total_fees']) == D('10.62')
    assert D(result['account']['cash']) == D('2189.38')
    assert D(result['account']['realized_pnl']) == D('189.38')
    assert result['account']['positions'] == []


def test_multiple_buys_cannot_spend_same_cash():
    def handle(c, bars):
        if c.now.day == 2:
            c.order(S, 'buy', 100)
            c.order(S, 'buy', 100)
    result = Engine([bar(2), bar(3)], SimpleNamespace(handle_bar=handle), fees=FREE, initial_cash=1500).run()
    assert [o['status'] for o in result['orders']] == ['filled', 'rejected']
    assert D(result['account']['cash']) == 500


def test_gap_up_rejects_and_releases_cash():
    result = Engine([bar(2), bar(3, 20)], load_strategy('''
def handle_bar(c, bars):
    if c.now.day == 2: c.order('000001.SZ', 'buy', 100)
'''), fees=FREE, initial_cash=1100).run()
    assert result['orders'][0]['status'] == 'rejected'
    assert result['trades'] == []
    assert D(result['account']['available_cash']) == 1100


def test_order_all_uses_actual_next_open_and_whole_lots():
    def handle(c, bars):
        if c.now.day == 2:
            c.order_all(S, 'buy')
        elif c.now.day == 4:
            c.order_all(S, 'sell')

    result = Engine(
        [bar(2, 10), bar(3, 13), bar(4, 14), bar(5, 15)],
        SimpleNamespace(handle_bar=handle),
        fees=FREE,
        initial_cash=2500,
    ).run()
    assert [trade['quantity'] for trade in result['trades']] == [100, 100]
    assert [trade['price'] for trade in result['trades']] == ['13', '15']
    assert D(result['account']['cash']) == 2700


def test_corporate_action_reinvests_into_fractional_shares_and_can_sell_all():
    def handle(c, bars):
        if c.now.day == 2:
            c.order(S, 'buy', 100)
        elif c.now.day == 4:
            c.order_all(S, 'sell')

    result = Engine(
        [bar(2), bar(3), bar(4, 9.95, adjustment_ratio=D('1.005')), bar(5, 9.95)],
        SimpleNamespace(handle_bar=handle), fees=FREE, initial_cash=2000,
    ).run()
    assert [trade['quantity'] for trade in result['trades']] == [100, '100.5']
    assert result['account']['positions'] == []
    assert [row['date'] for row in result['position_history']] == [
        '2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05',
    ]
    assert result['position_history'][2]['positions'][0]['quantity'] == '100.5'
    assert 'corporate_actions' not in result
    assert result['logs'] == [{
        'time': '2026-11-04T09:00:00+08:00',
        'message': f'复权调整 {S}：比例 1.005，持股 100 → 100.5（分红再投资）',
    }]


def test_strategy_benchmark_is_normalized_and_produces_excess_return():
    def init(context):
        context.set_benchmark("000300.SH")

    rows = [
        {"date": "2026-11-02", "close": "100"},
        {"date": "2026-11-03", "close": "110"},
    ]
    result = Engine(
        [bar(2), bar(3)], SimpleNamespace(init=init), fees=FREE,
        benchmark_loader=lambda symbol: rows,
    ).run()
    assert result["benchmark"] == {"symbol": "000300.SH"}
    assert result["equity_curve"][-1]["benchmark_net_value"] == "1.1"
    assert D(result["metrics"]["benchmark_return"]) == D("0.1")
    assert D(result["metrics"]["excess_return"]) == D("-0.0909090909090909090909090909")


def test_corporate_action_cancels_pre_action_pending_order():
    def handle(c, bars):
        if c.now.day == 1:
            c.order(S, 'buy', 100)
        elif c.now.day == 3:
            c.order(S, 'sell', 100, limit_price=20)

    result = Engine(
        [bar(1), bar(2), bar(3), bar(4, adjustment_ratio=D('1.1'))],
        SimpleNamespace(handle_bar=handle), fees=FREE, initial_cash=2000,
    ).run()
    assert result['orders'][1]['status'] == 'cancelled'
    assert result['orders'][1]['reason'] == '公司行动生效，原委托已取消'
    assert result['account']['positions'][0]['quantity'] == 110


def test_order_all_includes_fees_when_sizing():
    def handle(c, bars):
        if c.now.day == 2:
            c.order_all(S, 'buy')

    result = Engine([bar(2), bar(3, 10)], SimpleNamespace(handle_bar=handle), initial_cash=1005)
    result = result.run()
    assert result['orders'][0]['status'] == 'rejected'
    assert result['orders'][0]['reason'] == '可用资金不足以买入一手'


def test_cancel_and_end_expiry_release_reservations():
    def handle(c, bars):
        order = c.order(S, 'buy', 100)
        assert c.portfolio['frozen_cash'] == 1000
        assert c.cancel(order)
        assert not c.cancel(order)
        c.order(S, 'buy', 100)
    result = Engine([bar()], SimpleNamespace(handle_bar=handle), fees=FREE).run()
    assert [o['status'] for o in result['orders']] == ['cancelled', 'expired']
    assert D(result['account']['frozen_cash']) == 0
    assert result['trades'] == []


def test_limit_orders_only_use_open_not_intrabar_low():
    def handle(c, bars):
        if c.now.day == 2:
            c.order(S, 'buy', 100, limit_price=9)
    result = Engine([bar(2), bar(3, 11, 8), bar(4, 9)], SimpleNamespace(handle_bar=handle), fees=FREE).run()
    assert len(result['trades']) == 1
    assert result['trades'][0]['timestamp'].startswith('2026-11-04')


def test_upper_limit_blocks_and_next_open_can_fill():
    def handle(c, bars):
        if c.now.day == 2: c.order(S, 'buy', 100)
    result = Engine([bar(2), bar(3, 11, upper_limit=11), bar(4, 10, upper_limit=11)],
                    SimpleNamespace(handle_bar=handle), fees=FREE).run()
    assert result['trades'][0]['timestamp'].startswith('2026-11-04')


def test_order_from_trade_callback_waits_for_next_open():
    def handle(c, bars):
        if len(c.history(S)) == 1: c.order(S, 'buy', 100)
    def trade(c, fill):
        if fill.id == 'F000001': c.order(S, 'buy', 100)
    result = Engine([bar(minute=i) for i in range(3)], SimpleNamespace(handle_bar=handle, on_trade=trade),
                    frequency='1m', fees=FREE).run()
    assert [t['timestamp'][11:16] for t in result['trades']] == ['09:31', '09:32']


def test_snapshots_do_not_mutate_account():
    def handle(c, bars):
        p = c.portfolio
        p['cash'] = 0
    result = Engine([bar()], SimpleNamespace(handle_bar=handle), fees=FREE).run()
    assert D(result['account']['cash']) == 1_000_000


def test_duplicate_fills_are_idempotent():
    a = Account(2000, FREE)
    x = MatchingEngine(a)
    b = bar()
    identifier = x.submit(S, 'buy', 100, b.start, D(10))
    x.match({S: b}, b.start)
    a.apply_fill(x.orders[identifier], x.fills[0])
    assert a.cash == 1000
    assert a.positions[S].quantity == 100


def test_sell_freeze_prevents_duplicate_sell_and_releases():
    a = Account(3000, FREE)
    x = MatchingEngine(a)
    b = bar()
    x.submit(S, 'buy', 100, b.start, D(10))
    x.match({S: b}, b.start)
    a.new_session()
    sell = x.submit(S, 'sell', 100, b.end, D(10))
    rejected = x.submit(S, 'sell', 100, b.end, D(10))
    assert x.orders[rejected].status == 'rejected'
    assert a.positions[S].available == 0
    x.cancel(sell, b.end)
    assert a.positions[S].available == 100


@pytest.mark.parametrize('quantity', [True, -100, 0, 1.5])
def test_bad_quantities_rejected(quantity):
    a = Account(2000, FREE)
    with pytest.raises(ValueError):
        MatchingEngine(a).submit(S, 'buy', quantity, bar().end, D(10))


def test_invalid_and_duplicate_bars_rejected():
    with pytest.raises(ValueError):
        Engine([bar(), bar()], SimpleNamespace())
    with pytest.raises(ValueError):
        bar(op=-1)


def test_valuation_and_pnl_identity_with_open_position():
    def handle(c, bars):
        if c.now.day == 2: c.order(S, 'buy', 100)
    result = Engine([bar(2), bar(3, 10, 11)], SimpleNamespace(handle_bar=handle)).run()
    a = result['account']
    assert D(a['total_pnl']) == D(a['realized_pnl']) + D(a['unrealized_pnl'])
    assert D(a['equity']) == D(a['cash']) + D(a['market_value'])
    assert set(('annualized_return', 'annualized_volatility', 'sharpe', 'positive_day_rate',
                'max_drawdown_start', 'max_drawdown_end', 'trading_days')) <= result['metrics'].keys()


def test_strategy_syntax_error_and_missing_callbacks():
    with pytest.raises(SyntaxError): load_strategy('def broken(')
    with pytest.raises(ValueError): load_strategy('x = 1')


def test_trade_callback_cannot_mutate_recorded_fees():
    def handle(c, bars):
        if c.now.day == 2: c.order(S, "buy", 100)
    def trade(c, fill):
        fill.fees["commission"] = D(9999)
    result = Engine([bar(2), bar(3)], SimpleNamespace(handle_bar=handle, on_trade=trade)).run()
    assert D(result["trades"][0]["fees"]["commission"]) == 5
    assert D(result["account"]["total_fees"]) == D("5.01")
