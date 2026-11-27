from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from .models import Bar, SHANGHAI, decimal, money

DAILY = "tushare.cn.stock.daily"
MINUTE = "tb_sbkj2077.cn.stock.minute_1m"
CALENDAR = "tushare.cn.stock.trade_cal"
LIMITS = "tushare.cn.stock.stk_limit"
FACTORS = "tushare.cn.stock.adj_factor"


@dataclass
class DataBundle:
    bars: list[Bar]
    sessions: list[date]
    metadata: dict


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def row_bar(row, frequency, limits=None, minute_label="start"):
    day = as_date(row["trade_date"])
    if frequency == "1d":
        start, end = datetime.combine(day, time(9, 30), SHANGHAI), datetime.combine(day, time(15), SHANGHAI)
        volume = row["vol"]
    else:
        hhmmss = int(row["bar_time"])
        t = time(hhmmss // 10000, hhmmss // 100 % 100, hhmmss % 100)
        if minute_label not in ("start", "end"):
            raise ValueError("分钟标签必须为 start 或 end")
        if minute_label == "end":
            if not (time(9, 30) < t <= time(11, 30) or time(13) < t <= time(15)):
                raise ValueError(f"分钟结束时间不合法：{t}")
            t = (datetime.combine(day, t) - timedelta(minutes=1)).time()
        if not (time(9, 30) <= t < time(11, 30) or time(13) <= t < time(15)):
            raise ValueError(f"分钟时间不符合开始时间契约: {row['ts_code']} {day} {t}")
        start = datetime.combine(day, t, SHANGHAI)
        end = start + timedelta(minutes=1)
        volume = row["volume"]
    if volume is None:
        raise ValueError("行情成交量为空")
    limit = (limits or {}).get((row["ts_code"], day), {})
    return Bar(row["ts_code"], start, end, *(decimal(row[k]) for k in ("open", "high", "low", "close")),
               int(decimal(volume) * 100), frequency, limit.get("up_limit"), limit.get("down_limit"))


def demo_data(start, end, symbols, frequency):
    sessions = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            sessions.append(cursor)
        cursor += timedelta(days=1)
    if not sessions:
        raise ValueError("示例区间没有工作日")
    prior = start - timedelta(days=1)
    while prior.weekday() >= 5:
        prior -= timedelta(days=1)
    bars = []
    for di, day in enumerate([prior] + sessions):
        for si, symbol in enumerate(symbols):
            base = 10 + si * 5 + di * .025 + math.sin(di / 3) * .25
            op = money(base)
            cl = money(base + math.sin(di * .8) * .12 + .02)
            bars.append(Bar(symbol, datetime.combine(day, time(9, 30), SHANGHAI),
                            datetime.combine(day, time(15), SHANGHAI), op, max(op, cl) + Decimal('.1'),
                            min(op, cl) - Decimal('.1'), cl, 1_000_000, "1d"))
            if frequency == "1m":
                for mi in range(240):
                    offset = mi if mi < 120 else mi + 90
                    stamp = datetime.combine(day, time(9, 30), SHANGHAI) + timedelta(minutes=offset)
                    mo = money(op + (cl - op) * Decimal(mi) / 240)
                    mc = money(op + (cl - op) * Decimal(mi + 1) / 240)
                    bars.append(Bar(symbol, stamp, stamp + timedelta(minutes=1), mo,
                                    max(mo, mc) + Decimal('.01'), min(mo, mc) - Decimal('.01'), mc, 10000, "1m"))
    return DataBundle(bars, sessions, {"source": "demo", "note": "确定性合成行情；工作日历不包含法定节假日，不代表真实投资表现"})


def sdk_data(client, start, end, symbols, frequency, progress=None, minute_label="end"):
    progress = progress or (lambda value: None)
    warmup = start - timedelta(days=45)
    calendar = client.read(CALENDAR, exchange="SSE").to_pylist()
    sessions = sorted({as_date(r["cal_date"]) for r in calendar if r["is_open"] == 1 and start <= as_date(r["cal_date"]) <= end})
    if not sessions:
        raise ValueError("数据服务未返回区间内交易日历")
    bars, row_count, skipped_opening_rows = [], 0, 0
    for si, symbol in enumerate(symbols):
        daily = client.read(DAILY, start=warmup, end=end, ts_code=symbol).to_pylist()
        if not any(start <= as_date(r["trade_date"]) <= end for r in daily):
            raise ValueError(f"{symbol} 区间内没有日线数据")
        limits = {(r["ts_code"], as_date(r["trade_date"])): r for r in client.read(LIMITS, start=warmup, end=end, ts_code=symbol).to_pylist()}
        factors = {as_date(r["trade_date"]): decimal(r["adj_factor"]) for r in client.read(FACTORS, start=warmup, end=end, ts_code=symbol).to_pylist()}
        last_factor = None
        for row in sorted(daily, key=lambda r: as_date(r["trade_date"])):
            day = as_date(row["trade_date"])
            if day >= start:
                if day not in factors:
                    raise ValueError(f"缺少复权因子：{symbol} {day}")
                if last_factor is not None and factors[day] != last_factor:
                    raise ValueError(f"{symbol} {day} 存在复权因子变化；本版尚未实现公司行动记账，请缩短区间")
                limit = limits.get((symbol, day))
                if not limit or limit.get("up_limit") is None or limit.get("down_limit") is None:
                    raise ValueError(f"缺少涨跌停价格：{symbol} {day}")
            last_factor = factors.get(day, last_factor)
            bars.append(row_bar(row, "1d", limits))
        if frequency == "1m":
            cursor = start
            while cursor <= end:
                chunk_end = min(cursor + timedelta(days=6), end)
                rows = client.read(MINUTE, start=cursor, end=chunk_end, ts_code=symbol).to_pylist()
                row_count += len(rows)
                if row_count > 500_000:
                    raise ValueError("首版单任务最多加载 500000 根分钟线，请缩小区间或股票池")
                daily_dates = {as_date(r["trade_date"]) for r in daily}
                for row in rows:
                    if minute_label == "end" and int(row["bar_time"]) == 93000:
                        skipped_opening_rows += 1
                        continue
                    if as_date(row["trade_date"]) not in daily_dates:
                        raise ValueError("分钟行情缺少对应日线/交易约束数据")
                    bars.append(row_bar(row, "1m", limits, minute_label))
                cursor = chunk_end + timedelta(days=1)
            expected = {as_date(r["trade_date"]) for r in daily if start <= as_date(r["trade_date"]) <= end}
            available = {b.start.date() for b in bars if b.symbol == symbol and b.frequency == "1m"}
            if expected - available:
                raise ValueError(f"{symbol} 缺少分钟数据交易日：{sorted(expected - available)[:5]}")
        progress({"stage": "loading", "completed": si + 1, "total": len(symbols)})
    return DataBundle(bars, sessions, {
        "source": "sdk", "datasets": [DAILY, CALENDAR, LIMITS, FACTORS] + ([MINUTE] if frequency == "1m" else []),
        "price_basis": "raw", "timezone": "Asia/Shanghai", "minute_timestamp": minute_label,
        "skipped_0930_rows": skipped_opening_rows,
        "note": f"未复权行情；公司行动区间暂不支持。分钟标签={minute_label}，跳过09:30记录 {skipped_opening_rows} 条；无逐笔流动性模拟。",
    })
