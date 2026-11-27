from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .storage import read_json, write_json


def now():
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    def __init__(self, root: Path, max_workers=2, timeout=600):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_workers, self.timeout = max_workers, timeout
        self.lock = threading.RLock()
        self.active = {}
        self.closed = False
        self.file_lock = (self.root / ".server.lock").open("a")
        try:
            fcntl.flock(self.file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.file_lock.close()
            raise RuntimeError("此任务目录已由另一个服务使用；仅支持一个 API worker") from exc
        for path in self.root.glob("*/job.json"):
            job = read_json(path)
            if job["status"] == "running":
                job.update(status="interrupted", finished_at=now(), error="服务重启，旧任务结果不作为完成结果")
                write_json(path, job)

    def path(self, identifier):
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise KeyError(identifier)
        path = self.root / identifier
        if not (path / "job.json").is_file():
            raise KeyError(identifier)
        return path

    def submit(self, config):
        with self.lock:
            if self.closed or len(self.active) >= self.max_workers:
                raise RuntimeError("运行中的任务已达上限，请等待或取消任务")
            identifier = uuid4().hex
            path = self.root / identifier
            path.mkdir()
            write_json(path / "config.json", config.model_dump(mode="json"))
            (path / "strategy.py").write_text(config.strategy, encoding="utf-8")
            job = {"id": identifier, "name": config.name, "status": "running", "created_at": now(),
                   "strategy_id": config.strategy_id,
                   "source": config.source, "frequency": config.frequency,
                   "start": str(config.start), "end": str(config.end)}
            write_json(path / "job.json", job)
            try:
                process = subprocess.Popen([sys.executable, "-m", "quantstone_backtest.worker", str(path)],
                                           cwd=path, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError as exc:
                job.update(status="failed", error=str(exc), finished_at=now())
                write_json(path / "job.json", job)
                raise
            self.active[identifier] = process
            threading.Thread(target=self._watch, args=(identifier, process), daemon=True).start()
            return job

    @staticmethod
    def _stop(process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            except ProcessLookupError:
                pass

    def _watch(self, identifier, process):
        expired = False
        try:
            process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            expired = True
            self._stop(process)
        with self.lock:
            path = self.root / identifier
            job = read_json(path / "job.json")
            if job["status"] == "running":
                success = process.returncode == 0 and (path / "result.json").is_file()
                job.update(status="completed" if success and not expired else "failed", finished_at=now())
                if expired:
                    job["error"] = f"超过 {self.timeout} 秒运行时限"
                elif not success:
                    job["error"] = read_json(path / "error.json", {}).get("error", "回测进程异常退出")
                write_json(path / "job.json", job)
            self.active.pop(identifier, None)

    def cancel(self, identifier):
        with self.lock:
            path = self.path(identifier)
            job = read_json(path / "job.json")
            if job["status"] != "running":
                return job
            process = self.active.get(identifier)
            job.update(status="cancelled", finished_at=now())
            write_json(path / "job.json", job)
            if process:
                self._stop(process)
            return job

    def get(self, identifier, detail=True):
        path = self.path(identifier)
        job = read_json(path / "job.json")
        job["progress"] = read_json(path / "progress.json", {})
        if detail:
            job["config"] = read_json(path / "config.json")
            if job["status"] == "completed":
                job["result"] = read_json(path / "result.json")
            console = path / "console.log"
            job["console"] = console.read_text(encoding="utf-8", errors="replace")[-16000:] if console.exists() else ""
        return job

    def list(self):
        return sorted([self.get(p.parent.name, detail=False) for p in self.root.glob("*/job.json")],
                      key=lambda j: j["created_at"], reverse=True)

    def close(self):
        with self.lock:
            self.closed = True
            for identifier in list(self.active):
                self.cancel(identifier)
            fcntl.flock(self.file_lock, fcntl.LOCK_UN)
            self.file_lock.close()
