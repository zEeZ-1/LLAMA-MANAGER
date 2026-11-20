import asyncio
import json
import sqlite3
from contextlib import closing
import uuid
from ..core.errors import ManagerError

TABLES = {"nodes", "workers", "jobs", "credentials"}


class ManagerStore:
    def __init__(self, path):
        self.path = path
        self.write_lock = asyncio.Lock()
        self.controller_id = None

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    async def initialize(self):
        def init():
            with closing(self.connect()) as db, db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version > 2:
                    raise RuntimeError("Database schema is newer than this manager")
                if version < 2:
                    backup = self.path.with_suffix(".pre-v2.bak")
                    if self.path.exists() and not backup.exists():
                        with closing(sqlite3.connect(backup)) as target, target:
                            db.backup(target)
                    db.execute("BEGIN IMMEDIATE")
                    for table in TABLES:
                        db.execute(
                            f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, record TEXT NOT NULL)"
                        )
                    db.execute(
                        "CREATE TABLE IF NOT EXISTS claims (worker_id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, state TEXT NOT NULL)"
                    )
                    db.execute(
                        "CREATE TABLE IF NOT EXISTS manager_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO manager_meta VALUES (?,?)",
                        ("controller_id", str(uuid.uuid4())),
                    )
                    db.execute("PRAGMA user_version=2")
                return db.execute(
                    "SELECT value FROM manager_meta WHERE key='controller_id'"
                ).fetchone()[0]

        self.controller_id = await asyncio.to_thread(init)

    async def all(self, table):
        assert table in TABLES

        def read():
            with closing(self.connect()) as db, db:
                return [
                    json.loads(row[0])
                    for row in db.execute(f"SELECT record FROM {table} ORDER BY rowid")
                ]

        return await asyncio.to_thread(read)

    async def put(self, table, record):
        assert table in TABLES
        data = json.dumps(record, ensure_ascii=False)
        async with self.write_lock:

            def write():
                with closing(self.connect()) as db, db:
                    db.execute(
                        f"INSERT INTO {table} VALUES (?,?) ON CONFLICT(id) DO UPDATE SET record=excluded.record",
                        (record["id"], data),
                    )

            await asyncio.to_thread(write)

    async def delete(self, table, identifier):
        assert table in TABLES
        async with self.write_lock:

            def write():
                with closing(self.connect()) as db, db:
                    db.execute(f"DELETE FROM {table} WHERE id=?", (identifier,))

            await asyncio.to_thread(write)

    async def claims(self):
        def read():
            with closing(self.connect()) as db, db:
                return [
                    dict(worker_id=r[0], instance_id=r[1], state=r[2])
                    for r in db.execute(
                        "SELECT worker_id,instance_id,state FROM claims"
                    )
                ]

        return await asyncio.to_thread(read)

    async def reserve(self, worker_ids, instance_id):
        async with self.write_lock:

            def write():
                with closing(self.connect()) as db, db:
                    db.execute("BEGIN IMMEDIATE")
                    for worker in worker_ids:
                        row = db.execute(
                            "SELECT instance_id,state FROM claims WHERE worker_id=?",
                            (worker,),
                        ).fetchone()
                        if row:
                            raise ManagerError(
                                "worker_busy",
                                f"Worker is reserved by instance {row[0]} ({row[1]})",
                            )
                        db.execute(
                            "INSERT INTO claims VALUES (?,?,?)",
                            (worker, instance_id, "reserved"),
                        )

            await asyncio.to_thread(write)

    async def claim_state(self, instance_id, state):
        async with self.write_lock:

            def write():
                with closing(self.connect()) as db, db:
                    if state == "released":
                        db.execute(
                            "DELETE FROM claims WHERE instance_id=?", (instance_id,)
                        )
                    else:
                        db.execute(
                            "UPDATE claims SET state=? WHERE instance_id=?",
                            (state, instance_id),
                        )

            await asyncio.to_thread(write)
