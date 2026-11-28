from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, time, timedelta
from inspect import signature
from math import sqrt
from statistics import fmean, pstdev
from types import MappingProxyType, SimpleNamespace

from .account import Account
from .matching import MatchingEngine
from .models import Bar, FeeModel, SHANGHAI, ZERO, decimal, jsonable


class Context:
    """Trusted strategy API. Only completed bars are exposed; snapshots are copies."""

    def __init__(self, engine):
        self._engine = engine
        self.state = SimpleNamespace()
        self.symbols = tuple(engine.symbols)
        self.frequency = engine.frequency

    @property
    def now(self):
        return self._engine.now

    @property
    def portfolio(self):
        return deepcopy(self._engine.account.snapshot())

    def history(self, symbol, count=20, frequency=None):
        if not isinstance(count, int) or not 1 <= count <= 100_000:
            raise ValueError("历史窗口必须为 1 至 100000")
        frequency = frequency or self.frequency
        if frequency not in ("1d", self.frequency):
            raise ValueError("日线回测不能查询分钟行情")
        symbol = self._engine.ensure_symbol(symbol)
        return tuple(self._engine.history[(frequency, symbol)][-count:])

    def current(self, symbol, frequency=None):
        """Latest completed bar, or None; never exposes a forming bar."""
        rows = self.history(symbol, 1, frequency)
        return rows[-1] if rows else None

    def set_fees(self, **parameters):
        if not self._engine.initializing or self._engine.exchange.orders:
            raise ValueError("费用仅可在 init 中、首次下单前设置")
        self._engine.account.fees = FeeModel(**parameters)

    def set_benchmark(self, symbol):
        if not self._engine.initializing or self._engine.exchange.orders:
            raise ValueError("基准指数仅可在 init 中设置")
        if self._engine.benchmark_symbol is not None:
            raise ValueError("基准指数只能设置一次")
        if self._engine.benchmark_loader is None:
            raise ValueError("当前运行环境未提供基准指数数据")
        self._engine.benchmark_symbol = str(symbol).strip().upper()
        rows = self._engine.benchmark_loader(self._engine.benchmark_symbol)
        self._engine.benchmark_closes = {row["date"]: decimal(row["close"]) for row in rows}

    def order(self, symbol, side, quantity, limit_price=None):
        if self._engine.finishing:
            raise ValueError("回测结束阶段不能下单")
        symbol = self._engine.ensure_symbol(symbol)
        reference = self._engine.last_prices.get(symbol)
        return self._engine.exchange.submit(symbol, side, quantity, self.now, reference, limit_price)

    def order_all(self, symbol, side):
        """Buy the maximum whole lots at the next open, or sell all sellable shares."""
        if self._engine.finishing:
            raise ValueError("回测结束阶段不能下单")
        symbol = self._engine.ensure_symbol(symbol)
        reference = self._engine.last_prices.get(symbol)
        return self._engine.exchange.submit_all(symbol, side, self.now, reference)

    def cancel(self, order_id):
        return self._engine.exchange.cancel(order_id, self.now)

    def orders(self):
        return deepcopy(list(self._engine.exchange.orders.values()))

    def log(self, message):
        if len(self._engine.logs) < 10_000:
            self._engine.logs.append({"time": self.now.isoformat(), "message": str(message)[:4000]})


class Engine:
    def __init__(self, bars: list[Bar], strategy, *, initial_cash=1_000_000, fees=None,
                 frequency="1d", sessions=None, start=None, end=None, symbols=None, progress=None,
                 data_loader=None, benchmark_loader=None):
        if frequency not in ("1d", "1m"):
            raise ValueError("频率必须是 1d 或 1m")
        self.frequency = frequency
        self.bars = sorted(bars, key=lambda b: (b.start, b.frequency, b.symbol))
        keys = [(b.frequency, b.symbol, b.start) for b in bars]
        if len(keys) != len(set(keys)):
            raise ValueError("行情存在重复 K 线")
        driving = [b for b in bars if b.frequency == frequency]
        if not driving and not (data_loader and start and end and sessions):
            raise ValueError("没有可用于回测的行情")
        self.start = start or min(b.start.date() for b in driving)
        self.end = end or max(b.start.date() for b in driving)
        if self.start > self.end:
            raise ValueError("开始日期晚于结束日期")
        self.sessions = sorted(set(d for d in (sessions or [b.start.date() for b in driving]) if self.start <= d <= self.end))
        if not self.sessions:
            raise ValueError("区间内无交易日")
        self.symbols = sorted(set(symbols or [b.symbol for b in driving]))
        self.strategy = strategy
        self.account = Account(initial_cash, fees)
        self.exchange = MatchingEngine(self.account)
        self.history = defaultdict(list)
        self.last_prices = {}
        self.logs = []
        self.curve = []
        self.position_history = []
        self.now = datetime.combine(self.start, time(9), SHANGHAI)
        self.context = Context(self)
        self.progress = progress or (lambda value: None)
        self.finishing = False
        self.initializing = False
        self.data_loader = data_loader
        self.benchmark_loader = benchmark_loader
        self.benchmark_symbol = None
        self.benchmark_closes = {}
        self.benchmark_base = None
        self.benchmark_last = None
        self.loaded_symbols = {b.symbol for b in bars}
        self.events = defaultdict(lambda: defaultdict(lambda: {"open": {}, "close": []}))
        self.clock_closes = set()
        self.adjustments = defaultdict(dict)
        self.initial_bars = list(self.bars)
        self.bars = []
        self.legacy_handle_bar = False
        handler = getattr(strategy, "handle_bar", None)
        if handler:
            try:
                signature(handler).bind(self.context)
            except TypeError:
                signature(handler).bind(self.context, {})
                self.legacy_handle_bar = True

    def ingest(self, bars):
        """Cache past bars and schedule future events without exposing future OHLC."""
        for bar in sorted(bars, key=lambda b: (b.end, b.frequency, b.symbol)):
            if bar.frequency == "1d" and bar.adjustment_ratio != 1:
                existing = self.adjustments[bar.start.date()].get(bar.symbol)
                if existing is not None and existing != bar.adjustment_ratio:
                    raise ValueError("同一股票交易日存在冲突的复权比例")
                self.adjustments[bar.start.date()][bar.symbol] = bar.adjustment_ratio
            if bar.end <= self.now:
                self.history[(bar.frequency, bar.symbol)].append(bar)
                self.last_prices[bar.symbol] = bar.close
            elif bar.start.date() in self.sessions:
                self.events[bar.start.date()][bar.end]["close"].append(bar)
            if bar.start >= self.now and bar.start.date() in self.sessions and bar.frequency == self.frequency:
                self.events[bar.start.date()][bar.start]["open"][bar.symbol] = bar
        self.bars.extend(bars)

    def ensure_symbol(self, symbol):
        from .config import RunConfig
        symbol = RunConfig.symbols_supported([symbol])[0]
        if symbol not in self.loaded_symbols:
            if self.data_loader is None:
                raise ValueError(f"没有 {symbol} 的行情数据")
            bars = self.data_loader(symbol)
            keys = [(b.frequency, b.symbol, b.start) for b in bars]
            if len(keys) != len(set(keys)) or any(b.symbol != symbol for b in bars):
                raise ValueError("按需行情存在重复或股票代码不匹配")
            self.ingest(bars)
            self.loaded_symbols.add(symbol)
            self.symbols = sorted(self.loaded_symbols)
            self.context.symbols = tuple(self.symbols)  # Legacy informational view.
        return symbol

    def callback(self, name, *args):
        fn = getattr(self.strategy, name, None)
        if fn is not None:
            fn(self.context, *args)

    def reports(self):
        count = 0
        while self.exchange.reports:
            kind, report = self.exchange.reports.popleft()
            self.callback("on_" + kind, deepcopy(report))
            count += 1
            if count > 10_000:
                raise RuntimeError("订单回调连续产生过多委托，请检查策略")

    def run(self):
        events = self.events
        self.ingest(self.initial_bars)
        if self.data_loader:
            for day in self.sessions:
                starts = [datetime.combine(day, time(9, 30), SHANGHAI)]
                if self.frequency == "1m":
                    starts = [starts[0] + timedelta(minutes=i if i < 120 else i + 90) for i in range(240)]
                for stamp in starts:
                    end = stamp + timedelta(minutes=1) if self.frequency == "1m" else stamp.replace(hour=15, minute=0)
                    events[day][stamp]
                    events[day][end]
                    self.clock_closes.add(end)
        self.initializing = True
        try:
            self.callback("init")
        finally:
            self.initializing = False
        self.reports()
        for index, session in enumerate(self.sessions):
            self.now = datetime.combine(session, time(9), SHANGHAI)
            for symbol, ratio in sorted(self.adjustments[session].items()):
                action = self.exchange.apply_corporate_action(symbol, ratio, self.now)
                self.logs.append({
                    "time": self.now.isoformat(),
                    "message": (
                        f"复权调整 {symbol}：比例 {action['ratio']}，持股 "
                        f"{action['quantity_before']} → {action['quantity_after']}（分红再投资）"
                    ),
                })
            self.reports()
            self.account.new_session()
            self.callback("before_trading")
            self.reports()
            for timestamp, event in sorted(events[session].items()):
                self.now = timestamp
                closed = {}
                # Publish all completed frequencies before calling the driving callback.
                for bar in event["close"]:
                    self.history[(bar.frequency, bar.symbol)].append(bar)
                    self.last_prices[bar.symbol] = bar.close
                    if bar.frequency == self.frequency:
                        closed[bar.symbol] = bar
                if closed or timestamp in self.clock_closes:
                    self.account.mark({s: b.close for s, b in closed.items()})
                    if self.legacy_handle_bar:
                        self.callback("handle_bar", MappingProxyType(closed))
                    else:
                        self.callback("handle_bar")
                    self.reports()
                if event["open"]:
                    self.last_prices.update({s: b.open for s, b in event["open"].items()})
                    self.exchange.match(event["open"], timestamp)
                    self.reports()
            self.now = datetime.combine(session, time(15, 1), SHANGHAI)
            self.callback("after_trading")
            self.reports()
            snapshot = self.account.snapshot()
            point = {"date": session.isoformat(), "equity": snapshot["equity"], "net_value": snapshot["net_value"]}
            if self.benchmark_symbol:
                close = self.benchmark_closes.get(session.isoformat(), self.benchmark_last)
                if close is not None:
                    self.benchmark_last = close
                    self.benchmark_base = self.benchmark_base or close
                    benchmark_net_value = close / self.benchmark_base
                    point["benchmark_net_value"] = benchmark_net_value
                    point["excess_net_value"] = snapshot["net_value"] / benchmark_net_value
            self.curve.append(point)
            self.position_history.append({
                "date": session.isoformat(), "positions": snapshot["positions"],
                "equity": snapshot["equity"], "cash": snapshot["cash"],
                "total_pnl": snapshot["total_pnl"], "market_value": snapshot["market_value"],
            })
            self.progress({"completed": index + 1, "total": len(self.sessions), "date": session.isoformat(),
                           "equity_point": self.curve[-1]})
        self.finishing = True
        self.exchange.expire(self.now)
        self.reports()
        self.callback("on_finish")
        snapshot = self.account.snapshot()
        peak = self.account.initial_cash
        drawdown = ZERO
        peak_index = trough_index = 0
        current_peak_index = 0
        for index, row in enumerate(self.curve):
            if row["equity"] > peak:
                peak, current_peak_index = row["equity"], index
            current_drawdown = (peak - row["equity"]) / peak
            if current_drawdown > drawdown:
                drawdown, peak_index, trough_index = current_drawdown, current_peak_index, index
        net_values = [float(row["net_value"]) for row in self.curve]
        daily_returns = [current / previous - 1 for previous, current in zip(net_values, net_values[1:]) if previous]
        periods = max(len(daily_returns), 1)
        total_return = snapshot["equity"] / self.account.initial_cash - 1
        annualized_return = (float(snapshot["net_value"]) ** (252 / periods) - 1) if self.curve else 0
        daily_volatility = pstdev(daily_returns) if len(daily_returns) > 1 else 0
        annualized_volatility = daily_volatility * sqrt(252)
        sharpe = fmean(daily_returns) / daily_volatility * sqrt(252) if daily_volatility else 0
        positive_days = sum(value > 0 for value in daily_returns)
        benchmark_return = (self.benchmark_last / self.benchmark_base - 1) if self.benchmark_base else None
        return jsonable({
            "account": snapshot, "equity_curve": self.curve,
            "orders": list(self.exchange.orders.values()), "trades": self.exchange.fills,
            "logs": self.logs,
            "effective_fees": self.account.fees,
            "loaded_symbols": sorted(self.loaded_symbols),
            "position_history": self.position_history,
            "benchmark": {"symbol": self.benchmark_symbol} if self.benchmark_symbol else None,
            "metrics": {
                "total_return": total_return,
                "annualized_return": annualized_return,
                "annualized_volatility": annualized_volatility,
                "sharpe": sharpe,
                "positive_day_rate": positive_days / len(daily_returns) if daily_returns else 0,
                "max_drawdown": drawdown,
                "max_drawdown_start": self.curve[peak_index]["date"] if self.curve else None,
                "max_drawdown_end": self.curve[trough_index]["date"] if self.curve else None,
                "trade_count": len(self.exchange.fills),
                "trading_days": len(self.curve),
                "benchmark_return": benchmark_return,
                "excess_return": (1 + total_return) / (1 + benchmark_return) - 1 if benchmark_return is not None else None,
            },
        })


def load_strategy(source: str):
    if len(source.encode()) > 200_000:
        raise ValueError("策略文件过大")
    namespace = {"__name__": "quantstone_strategy"}
    exec(compile(source, "strategy.py", "exec"), namespace)
    names = ("init", "before_trading", "handle_bar", "after_trading", "on_order", "on_trade", "on_finish")
    if not any(callable(namespace.get(n)) for n in ("before_trading", "handle_bar", "after_trading")):
        raise ValueError("策略至少需要定义一个盘前、行情或盘后回调")
    return SimpleNamespace(**{n: namespace[n] for n in names if callable(namespace.get(n))})
