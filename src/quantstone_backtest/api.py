from contextlib import asynccontextmanager
from ipaddress import ip_address
import os
from pathlib import Path
import socket

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from .config import RunConfig
from .example import EXAMPLE
from .jobs import JobManager
from .strategies import FolderRename, StrategyInput, StrategyStore, StrategyUpdate


def local_allowed_hosts(extra_hosts=None):
    """Return exact host names accepted in addition to private LAN addresses."""
    hosts = {"localhost", "testserver", socket.gethostname()}
    hosts.update(host.strip() for host in (extra_hosts or []) if host.strip())
    return sorted(hosts)


def host_name(value):
    value = value.strip()
    if value.startswith("["):
        closing = value.find("]")
        return value[1:closing] if closing > 0 else value
    name, separator, port = value.rpartition(":")
    return name if separator and port.isdigit() else value


def create_app(root=None, allowed_hosts=None, strategy_root=None, data_source=None):
    data_source = data_source or os.environ.get("QUANTSTONE_DATA_SOURCE", "sdk")
    if data_source not in {"demo", "sdk"}:
        raise ValueError("QUANTSTONE_DATA_SOURCE 必须为 demo 或 sdk")
    root = Path(root or os.environ.get("QUANTSTONE_BACKTEST_HOME", "var/runs"))
    strategy_root = Path(strategy_root or root.parent / "strategies")
    accepted_hosts = {host.lower() for host in local_allowed_hosts(allowed_hosts)}

    @asynccontextmanager
    async def lifespan(app):
        app.state.jobs = JobManager(root)
        app.state.strategies = StrategyStore(strategy_root)
        yield
        app.state.jobs.close()

    app = FastAPI(title="QuantStone Backtest", lifespan=lifespan)

    @app.middleware("http")
    async def local_write_guard(request: Request, call_next):
        host = host_name(request.headers.get("host", "")).lower()
        accepted = host in accepted_hosts
        if not accepted:
            try:
                address = ip_address(host)
                accepted = address.is_private or address.is_loopback or address.is_link_local
            except ValueError:
                accepted = False
        if not accepted:
            return JSONResponse(
                {"detail": "不允许的 Host；局域网域名请用 --allowed-host 添加"},
                status_code=400,
            )
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            if request.headers.get("x-quantstone-client") != "workbench":
                return JSONResponse({"detail": "写操作需要本地工作台请求头"}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
                return JSONResponse({"detail": "不允许跨站写操作"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        # This is a local research workbench: stale JS/CSS can leave new HTML unusable.
        # Always revalidate pages and static assets after a deployment or service restart.
        if request.url.path.endswith((".html", ".css", ".js")) or request.url.path == "/" or request.url.path.startswith("/strategies/"):
            response.headers["Cache-Control"] = "no-cache"
        # CodeMirror uses style-mod to inject its bundled theme and layout rules.
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'"
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/example")
    def example():
        return {"strategy": EXAMPLE}

    @app.get("/api/environment")
    def environment():
        return {"data_source": data_source}

    @app.get("/strategies/{identifier}")
    def strategy_page(identifier: str, request: Request):
        try:
            request.app.state.strategies.get(identifier)
        except KeyError as exc:
            raise HTTPException(404, "策略不存在") from exc
        return FileResponse(Path(__file__).parent / "static" / "strategy.html")

    @app.get("/api/strategies")
    def list_strategies(request: Request):
        strategies = request.app.state.strategies.list()
        runs = request.app.state.jobs.list()
        for strategy in strategies:
            related = [run for run in runs if run.get("strategy_id") == strategy["id"]]
            strategy["run_count"] = len(related)
            strategy["last_run"] = related[0] if related else None
            strategy.pop("code", None)
        return strategies

    @app.post("/api/strategies", status_code=201)
    def create_strategy(value: StrategyInput, request: Request):
        try:
            compile(value.code, "strategy.py", "exec")
        except SyntaxError as exc:
            raise HTTPException(422, f"策略第 {exc.lineno} 行：{exc.msg}") from exc
        return request.app.state.strategies.create(value)

    @app.get("/api/strategies/{identifier}")
    def get_strategy(identifier: str, request: Request):
        try:
            return request.app.state.strategies.get(identifier)
        except KeyError as exc:
            raise HTTPException(404, "策略不存在") from exc

    @app.put("/api/strategies/{identifier}")
    def update_strategy(identifier: str, value: StrategyUpdate, request: Request):
        try:
            compile(value.code, "strategy.py", "exec")
            return request.app.state.strategies.update(identifier, value)
        except SyntaxError as exc:
            raise HTTPException(422, f"策略第 {exc.lineno} 行：{exc.msg}") from exc
        except KeyError as exc:
            raise HTTPException(404, "策略不存在") from exc

    @app.post("/api/strategy-folders/rename")
    def rename_strategy_folder(value: FolderRename, request: Request):
        try:
            return request.app.state.strategies.rename_folder(value.current, value.replacement)
        except KeyError as exc:
            raise HTTPException(404, "策略目录不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/runs")
    def list_runs(request: Request):
        return request.app.state.jobs.list()

    @app.post("/api/runs", status_code=201)
    def create_run(config: RunConfig, request: Request):
        if data_source == "demo" and (config.end - config.start).days > 90:
            raise HTTPException(
                422,
                "合成测试行情仅用于验证流程，回测区间最多 90 天；长期策略研究请使用真实行情数据源。",
            )
        if config.strategy_id:
            try:
                strategy = request.app.state.strategies.get(config.strategy_id)
                if (config.instrument_type, config.market) != (strategy["instrument_type"], strategy["market"]):
                    raise HTTPException(422, "回测品种与策略不一致")
            except KeyError as exc:
                raise HTTPException(422, "关联的策略不存在") from exc
        # Compile only; execution happens exclusively in the child process.
        try:
            compile(config.strategy, "strategy.py", "exec")
        except SyntaxError as exc:
            raise HTTPException(422, f"策略第 {exc.lineno} 行：{exc.msg}") from exc
        try:
            # Data routing is deployment policy, never a client-selected parameter.
            return request.app.state.jobs.submit(config.model_copy(update={"source": data_source}))
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/runs/{identifier}")
    def get_run(identifier: str, request: Request):
        try:
            return request.app.state.jobs.get(identifier)
        except KeyError as exc:
            raise HTTPException(404, "回测不存在") from exc

    @app.post("/api/runs/{identifier}/cancel")
    def cancel_run(identifier: str, request: Request):
        try:
            return request.app.state.jobs.cancel(identifier)
        except KeyError as exc:
            raise HTTPException(404, "回测不存在") from exc

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="web")
    return app
