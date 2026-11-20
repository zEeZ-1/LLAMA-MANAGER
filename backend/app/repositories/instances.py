"""Small SQLite repository; all disk operations run outside the event loop."""

import asyncio
import json
import sqlite3
from pathlib import Path


class InstanceRepository:
    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()

    async def initialize(self):
        def init():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS instances (id TEXT PRIMARY KEY, record TEXT NOT NULL)"
                )

        await asyncio.to_thread(init)

    async def all(self):
        def read():
            with sqlite3.connect(self.path) as db:
                return [
                    json.loads(r[0])
                    for r in db.execute(
                        "SELECT record FROM instances ORDER BY rowid DESC"
                    )
                ]

        return await asyncio.to_thread(read)

    async def save(self, record):
        serialized = json.dumps(record, ensure_ascii=False)

        def write():
            with sqlite3.connect(self.path) as db:
                db.execute(
                    "INSERT INTO instances VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET record=excluded.record",
                    (record["id"], serialized),
                )

        async with self.lock:
            await asyncio.to_thread(write)
