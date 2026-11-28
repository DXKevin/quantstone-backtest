"""Internal per-run cache. Strategies do not manage subscriptions."""
import re

from .data import INDEX_DAILY, demo_benchmark, demo_data, sdk_data


class DemandData:
    def __init__(self, config, client=None):
        self.config, self.client = config, client
        self.cache = {}
        self.minute_count = 0
        self.bundle = self._read([])

    def _read(self, symbols):
        c = self.config
        if c.source == "demo":
            return demo_data(c.start, c.end, symbols, c.frequency)
        return sdk_data(self.client, c.start, c.end, symbols, c.frequency,
                        minute_label=c.minute_label)

    def load(self, symbol):
        if symbol in self.cache:
            return self.cache[symbol]
        if len(self.cache) >= 20:
            raise ValueError("首版每次回测最多自动加载 20 只不同股票")
        c = self.config
        if c.frequency == "1m" and (c.end - c.start).days * (len(self.cache) + 1) > 2500:
            raise ValueError("分钟数据加载超过天数×股票数 2500 的限制")
        bundle = self._read([symbol])
        count = sum(b.frequency == "1m" for b in bundle.bars)
        if self.minute_count + count > 500_000:
            raise ValueError("单任务最多加载 500000 根分钟线")
        self.minute_count += count
        self.cache[symbol] = bundle.bars
        skipped = self.bundle.metadata.get("skipped_0930_rows", 0) + bundle.metadata.get("skipped_0930_rows", 0)
        self.bundle.metadata.update(bundle.metadata)
        self.bundle.metadata["skipped_0930_rows"] = skipped
        if c.source == "sdk":
            self.bundle.metadata["note"] = (
                f"未复权行情；复权因子变化按分红再投资调整持股数量（允许小数股）。分钟标签={c.minute_label}，"
                f"跳过09:30记录 {skipped} 条；无逐笔流动性模拟。"
            )
        return bundle.bars

    def load_benchmark(self, symbol):
        symbol = str(symbol).strip().upper()
        if not re.fullmatch(r"\d{6}\.(?:SH|SZ|CSI)", symbol):
            raise ValueError(f"非法基准指数代码：{symbol}")
        c = self.config
        if c.source == "demo":
            return demo_benchmark(c.start, c.end, symbol)
        rows = self.client.read(INDEX_DAILY, start=c.start, end=c.end, ts_code=symbol).to_pylist()
        result = sorted(
            ({"date": str(row["trade_date"]), "close": str(row["close"])} for row in rows if row.get("close") is not None),
            key=lambda row: row["date"],
        )
        if not result:
            raise ValueError(f"基准指数 {symbol} 在回测区间内没有日线数据")
        self.bundle.metadata.setdefault("datasets", []).append(INDEX_DAILY)
        return result
