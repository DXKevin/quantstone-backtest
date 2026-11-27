"""One trusted strategy per disposable process; never run inside the web server."""
import argparse
import contextlib
import os
import traceback
from pathlib import Path

from .config import RunConfig
from .data import demo_data, sdk_data
from .demand_data import DemandData
from .engine import Engine, load_strategy
from .models import FeeModel
from .storage import read_json, write_json


class BoundedOutput:
    def __init__(self, stream, limit=1_000_000):
        self.stream, self.remaining = stream, limit

    def write(self, text):
        text = str(text)
        if self.remaining > 0:
            part = text[:self.remaining]
            self.stream.write(part)
            self.stream.flush()
            self.remaining -= len(part)
        return len(text)

    def flush(self):
        self.stream.flush()


def run(directory: Path):
    config = RunConfig.model_validate(read_json(directory / "config.json"))
    def progress(value):
        write_json(directory / "progress.json", value)
    progress({"stage": "loading", "completed": 0, "total": 1})
    client = None
    if config.source == "sdk":
        try:
            from quantstone_sdk import QuantStoneClient
        except ImportError as exc:
            raise RuntimeError("请安装 quantstone-data/sdk/python 后使用真实数据") from exc
        client = QuantStoneClient(os.environ.get("QUANTSTONE_API_URL", "http://127.0.0.1:8000/api/v1"), timeout=120)
    data = DemandData(config, client)
    bundle = data.bundle
    initial_bars = [bar for symbol in config.symbols for bar in data.load(symbol)]
    strategy = load_strategy(config.strategy)
    fees = FeeModel(config.commission_rate, config.minimum_commission, config.sell_tax_rate, config.transfer_rate)
    engine = Engine(initial_bars, strategy, initial_cash=config.initial_cash, fees=fees,
                    frequency=config.frequency, sessions=bundle.sessions, start=config.start, end=config.end,
                    symbols=config.symbols, data_loader=data.load,
                    progress=lambda p: progress({"stage": "running", **p}))
    result = engine.run()
    result["data"] = bundle.metadata
    result["configuration"] = config.model_dump(mode="json", exclude={"strategy"})
    write_json(directory / "result.json", result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    with (args.directory / "console.log").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(BoundedOutput(log)), contextlib.redirect_stderr(BoundedOutput(log)):
            try:
                run(args.directory)
            except BaseException:
                write_json(args.directory / "error.json", {"error": traceback.format_exc(limit=15)[-16000:]})
                raise SystemExit(1)


if __name__ == "__main__":
    main()
