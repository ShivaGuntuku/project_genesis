"""Live updates (server-sent events)."""

import asyncio
import json

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from genesis.dashboard.common import (
    World,
)
from genesis.persistence.db import WorldStore


def register(app: FastAPI, world: World) -> None:
    @app.get("/api/live")
    async def live():
        async def stream():
            last = None
            while True:
                try:
                    store = WorldStore(world.path, readonly=True)
                    meta = store.meta()
                    store.close()
                    now = (meta.get("tick"), meta.get("saved_at"))
                except Exception:
                    now = None
                if now != last and now is not None:
                    last = now
                    tick = int(now[0])
                    data = {"tick": tick, "clock": world.clock(tick), "savedAt": now[1]}
                    yield f"data: {json.dumps(data)}\n\n"
                else:
                    yield ": still here\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(stream(), media_type="text/event-stream")
