from contextlib import asynccontextmanager
from ipaddress import ip_address
import os
from pathlib import Path
import socket

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import RunConfig
from .example import EXAMPLE
from .jobs import JobManager


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


def create_app(root=None, allowed_hosts=None):
    root = Path(root or os.environ.get("QUANTSTONE_BACKTEST_HOME", "var/runs"))
    accepted_hosts = {host.lower() for host in local_allowed_hosts(allowed_hosts)}

    @asynccontextmanager
    async def lifespan(app):
        app.state.jobs = JobManager(root)
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
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/example")
    def example():
        return {"strategy": EXAMPLE}

    @app.get("/api/runs")
    def list_runs(request: Request):
        return request.app.state.jobs.list()

    @app.post("/api/runs", status_code=201)
    def create_run(config: RunConfig, request: Request):
        # Compile only; execution happens exclusively in the child process.
        try:
            compile(config.strategy, "strategy.py", "exec")
        except SyntaxError as exc:
            raise HTTPException(422, f"策略第 {exc.lineno} 行：{exc.msg}") from exc
        try:
            return request.app.state.jobs.submit(config)
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
