import json
import os
from pathlib import Path
from uuid import uuid4

from .models import jsonable


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp.write_text(json.dumps(jsonable(value), ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(temp, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
