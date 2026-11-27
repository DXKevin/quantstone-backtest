from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading
from uuid import uuid4
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .example import EXAMPLE
from .storage import read_json, write_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_folder(value: str) -> str:
    parts = [part.strip() for part in value.replace("\\", "/").split("/") if part.strip()]
    if not parts:
        return "默认目录"
    if any(part in {".", ".."} for part in parts):
        raise ValueError("策略目录不能包含 . 或 ..")
    return "/".join(parts)


class StrategyInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    instrument_type: Literal["stock"] = "stock"
    market: Literal["A股"] = "A股"
    frequency: str = Field(default="日线", max_length=30)
    folder: str = Field(default="默认目录", max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=8)
    code: str = Field(min_length=1, max_length=200_000)

    @field_validator("name", "description", "market", "frequency", "folder", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, tags):
        return list(dict.fromkeys(tag.strip()[:24] for tag in tags if tag.strip()))

    @field_validator("folder")
    @classmethod
    def clean_folder(cls, value):
        return normalize_folder(value)


class FolderRename(BaseModel):
    current: str = Field(min_length=1, max_length=120)
    replacement: str = Field(min_length=1, max_length=120)

    @field_validator("current", "replacement")
    @classmethod
    def clean_folder(cls, value):
        return normalize_folder(value)


class StrategyUpdate(StrategyInput):
    status: str = "active"

    @field_validator("status")
    @classmethod
    def known_status(cls, value):
        if value not in {"active", "archived"}:
            raise ValueError("策略状态必须是 active 或 archived")
        return value


class StrategyStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        if not any(self.root.glob("*.json")):
            self.create(
                StrategyInput(
                    name="我的第一个策略",
                    description="盘后准备股票池，在下一交易日根据行情买入。",
                    market="A股",
                    frequency="日线",
                    tags=["示例", "事件驱动"],
                    code=EXAMPLE,
                )
            )

    def path(self, identifier: str) -> Path:
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise KeyError(identifier)
        path = self.root / f"{identifier}.json"
        if not path.is_file():
            raise KeyError(identifier)
        return path

    def list(self) -> list[dict]:
        with self.lock:
            items = [self.get(path.stem) for path in self.root.glob("*.json")]
        return sorted(items, key=lambda item: item["updated_at"], reverse=True)

    def get(self, identifier: str) -> dict:
        with self.lock:
            item = read_json(self.path(identifier))
            item.setdefault("instrument_type", "stock")
            item.setdefault("folder", "默认目录")
            return item

    def create(self, value: StrategyInput) -> dict:
        now = utc_now()
        item = {
            "id": uuid4().hex,
            **value.model_dump(),
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        with self.lock:
            write_json(self.root / f"{item['id']}.json", item)
        return item

    def update(self, identifier: str, value: StrategyUpdate) -> dict:
        with self.lock:
            previous = self.get(identifier)
            item = {
                **previous,
                **value.model_dump(),
                "id": identifier,
                "updated_at": utc_now(),
            }
            write_json(self.path(identifier), item)
        return item

    def rename_folder(self, current: str, replacement: str) -> dict:
        with self.lock:
            items = [self.get(path.stem) for path in self.root.glob("*.json")]
            matches = [item for item in items if item["folder"] == current]
            if not matches:
                raise KeyError(current)
            if current != replacement and any(item["folder"] == replacement for item in items):
                raise ValueError("同名策略目录已存在")
            now = utc_now()
            for item in matches:
                item["folder"] = replacement
                item["updated_at"] = now
                write_json(self.path(item["id"]), item)
        return {"current": current, "replacement": replacement, "strategy_count": len(matches)}
