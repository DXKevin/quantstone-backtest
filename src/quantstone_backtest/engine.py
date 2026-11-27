from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, time
from types import MappingProxyType, SimpleNamespace

from .account import Account
from .matching import MatchingEngine
from .models import Bar, FeeModel, SHANGHAI, ZERO, jsonable


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
        return tuple(self._engine.history[(frequency or self.frequency, symbol)][-count:])

    def order(self, symbol, side, quantity, limit_price=None):
        if self._engine.finishing:
            raise ValueError("回测结束阶段不能下单")
        if symbol not in self.symbols:
            raise ValueError("股票不在本次回测股票池中")
        reference = self._engine.last_prices.get(symbol)
        return self._engine.exchange.submit(symbol, side, quantity, self.now, reference, limit_price)

    def cancel(self, order_id):
        return self._engine.exchange.cancel(order_id, self.now)

    def orders(self):
        return deepcopy(list(self._engine.exchange.orders.values()))

    def log(self, message):
        if len(self._engine.logs) < 10_000:
            self._engine.logs.append({"time": self.now.isoformat(), "message": str(message)[:4000]})


class Engine:
    def __init__(self, bars: list[Bar], strategy, *, initial_cash=1_000_000, fees=None,
                 frequency="1d", sessions=None, start=None, end=None, symbols=None, progress=None):
        if frequency not in ("1d", "1m"):
            raise ValueError("频率必须是 1d 或 1m")
        self.frequency = frequency
        self.bars = sorted(bars, key=lambda b: (b.start, b.frequency, b.symbol))
        keys = [(b.frequency, b.symbol, b.start) for b in bars]
        if len(keys) != len(set(keys)):
            raise ValueError("行情存在重复 K 线")
        driving = [b for b in bars if b.frequency == frequency]
        if not driving:
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
        self.now = datetime.combine(self.start, time(9), SHANGHAI)
        self.context = Context(self)
        self.progress = progress or (lambda value: None)
        self.finishing = False

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
        events = defaultdict(lambda: defaultdict(lambda: {"open": {}, "close": []}))
        for bar in self.bars:
            if bar.end < self.now:
                self.history[(bar.frequency, bar.symbol)].append(bar)
                self.last_prices[bar.symbol] = bar.close
            elif bar.start.date() in self.sessions:
                events[bar.start.date()][bar.end]["close"].append(bar)
                if bar.frequency == self.frequency:
                    events[bar.start.date()][bar.start]["open"][bar.symbol] = bar
        for values in self.history.values():
            values.sort(key=lambda b: b.end)
        self.callback("init")
        self.reports()
        for index, session in enumerate(self.sessions):
            self.now = datetime.combine(session, time(9), SHANGHAI)
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
                if closed:
                    self.account.mark({s: b.close for s, b in closed.items()})
                    self.callback("handle_bar", MappingProxyType(closed))
                    self.reports()
                if event["open"]:
                    self.last_prices.update({s: b.open for s, b in event["open"].items()})
                    self.exchange.match(event["open"], timestamp)
                    self.reports()
            self.now = datetime.combine(session, time(15, 1), SHANGHAI)
            self.callback("after_trading")
            self.reports()
            snapshot = self.account.snapshot()
            self.curve.append({"date": session.isoformat(), "equity": snapshot["equity"], "net_value": snapshot["net_value"]})
            self.progress({"completed": index + 1, "total": len(self.sessions), "date": session.isoformat()})
        self.finishing = True
        self.exchange.expire(self.now)
        self.reports()
        self.callback("on_finish")
        snapshot = self.account.snapshot()
        peak = self.account.initial_cash
        drawdown = ZERO
        for row in self.curve:
            peak = max(peak, row["equity"])
            drawdown = max(drawdown, (peak - row["equity"]) / peak)
        return jsonable({
            "account": snapshot, "equity_curve": self.curve,
            "orders": list(self.exchange.orders.values()), "trades": self.exchange.fills,
            "logs": self.logs,
            "metrics": {"total_return": snapshot["equity"] / self.account.initial_cash - 1,
                        "max_drawdown": drawdown, "trade_count": len(self.exchange.fills)},
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
