from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AuditLog:
    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._io_lock = threading.Lock()
        self._session_offsets: Dict[str, List[int]] = {}
        self._all_offsets: List[int] = []
        self._indexed_size = 0

    def append(self, event: Dict[str, Any]) -> Dict[str, Any]:
        enriched = {"ts": utc_now(), **event}
        encoded = (json.dumps(enriched, ensure_ascii=False) + "\n").encode("utf-8")
        with self._io_lock:
            offset = self.path.stat().st_size if self.path.exists() else 0
            with self.path.open("ab") as handle:
                handle.write(encoded)
            self._all_offsets.append(offset)
            session_id = enriched.get("session_id")
            if isinstance(session_id, str):
                self._session_offsets.setdefault(session_id, []).append(offset)
            self._indexed_size = offset + len(encoded)
        return enriched

    def read(self, session_id: Optional[str] = None, last: int = 20) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._io_lock:
            self._ensure_index_locked()
            if session_id is None:
                offsets = self._all_offsets[-last:]
            else:
                offsets = self._session_offsets.get(session_id, [])[-last:]
            return self._read_offsets_locked(offsets)

    def _ensure_index_locked(self) -> None:
        if not self.path.exists():
            self._session_offsets = {}
            self._all_offsets = []
            self._indexed_size = 0
            return
        current_size = self.path.stat().st_size
        if current_size < self._indexed_size:
            self._session_offsets = {}
            self._all_offsets = []
            self._indexed_size = 0
        if current_size == self._indexed_size:
            return
        with self.path.open("rb") as handle:
            handle.seek(self._indexed_size)
            while True:
                offset = handle.tell()
                line = handle.readline()
                if not line:
                    break
                try:
                    item = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                self._all_offsets.append(offset)
                session_id = item.get("session_id")
                if isinstance(session_id, str):
                    self._session_offsets.setdefault(session_id, []).append(offset)
            self._indexed_size = handle.tell()

    def _read_offsets_locked(self, offsets: List[int]) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        with self.path.open("rb") as handle:
            for offset in offsets:
                handle.seek(offset)
                line = handle.readline()
                if not line:
                    continue
                entries.append(json.loads(line.decode("utf-8")))
        return entries
