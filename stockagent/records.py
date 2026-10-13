"""Append-only CSV / JSONL logging (the original rewrote an .xlsx file on every trade)."""
from __future__ import annotations

import csv
import json
import os
from typing import Dict, List


class Recorder:
    def __init__(self, out_dir: str):
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self._writers: Dict[str, tuple] = {}

    def row(self, name: str, row: dict) -> None:
        if name not in self._writers:
            path = os.path.join(self.out_dir, f"{name}.csv")
            fh = open(path, "w", newline="", encoding="utf-8")
            writer = csv.DictWriter(fh, fieldnames=list(row.keys()), extrasaction="ignore")
            writer.writeheader()
            self._writers[name] = (fh, writer)
        fh, writer = self._writers[name]
        writer.writerow(row)

    def rows(self, name: str, rows: List[dict]) -> None:
        for r in rows:
            self.row(name, r)

    def jsonl(self, name: str, obj: dict) -> None:
        with open(os.path.join(self.out_dir, f"{name}.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def json(self, name: str, obj) -> str:
        path = os.path.join(self.out_dir, f"{name}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, ensure_ascii=False, default=str)
        return path

    def text(self, name: str, content: str) -> str:
        path = os.path.join(self.out_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    def close(self) -> None:
        for fh, _ in self._writers.values():
            fh.close()
        self._writers.clear()
