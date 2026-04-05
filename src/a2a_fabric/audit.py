from __future__ import annotations

import json
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

    def append(self, event: Dict[str, Any]) -> Dict[str, Any]:
        enriched = {"ts": utc_now(), **event}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(enriched, ensure_ascii=False) + "\n")
        return enriched

    def read(self, session_id: Optional[str] = None, last: int = 20) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        if not self.path.exists():
            return entries
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if session_id and item.get("session_id") != session_id:
                    continue
                entries.append(item)
        return entries[-last:]
