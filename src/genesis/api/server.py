"""`genesis view <world>`: run a world and watch it live in the browser.

One process:
- a simulation thread runs ticks at the chosen speed and saves each tick (exactly like
  `genesis run`), then hands a small frame to the web server;
- FastAPI serves the pixel-art viewer (ui/dist), a WebSocket with live frames, and read-only
  JSON for the inspect panel and "why?".

Viewing never changes history: pacing only decides how long to wait between ticks.
"""

import asyncio
import json
import os
import queue
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from genesis.api import views
from genesis.llm import mind as llm_mind
from genesis.persistence.db import WorldStore

MAX_FRAMES_PER_SECOND = 30  # at "max" speed, frames are merged to this rate
SPEEDS = (0.5, 1, 2, 5, 20, 0)  # ticks per real second; 0 = as fast as possible


def ui_dist() -> Path:
    override = os.environ.get("GENESIS_UI_DIST")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "ui" / "dist"


class Runner:
    """Owns the simulation and its world file. Runs in its own thread."""

    def __init__(
        self,
        path: Path,
        speed: float = 1,
        paused: bool = False,
        max_ticks: int | None = None,
        llm: bool | None = None,  # None: as the world's config says; False: rules only
        gateway=None,  # tests pass a fake model
    ):
        self.path = path
        self.llm = llm
        self.gateway = gateway
        self.llm_status = ""
        self.max_ticks = max_ticks  # stop after this many ticks (tests, timed showings)
        self.ticks_done = 0
        self.finished = threading.Event()
        self.speed = speed
        self.paused = paused
        self.lock = threading.Lock()  # held while ticking; API reads take it too
        self.commands: queue.Queue = queue.Queue()
        self.stop = threading.Event()
        self.sim = None
        self.ready = threading.Event()
        self.publish = None  # set by the server: publish(dict) from this thread
        self.error: Exception | None = None
        self.stop_reason = "viewer closed"

    # --------------------------------

    def run(self):
        try:
            self._run()
        except Exception as e:  # surface crashes instead of dying silently
            self.error = e
            self.ready.set()
            raise
        finally:
            self.finished.set()

    def _run(self):
        store = WorldStore(self.path)
        with self.lock:
            self.sim = store.load()
            self.llm_status = llm_mind.attach(self.sim, self.llm, self.gateway)
        sim = self.sim
        run_id = store.start_run(sim.tick_count)
        self.ready.set()

        pending, last_sent = [], 0.0
        try:
            while not self.stop.is_set():
                step = self._apply_commands()
                if self.paused and not step:
                    time.sleep(0.03)
                    continue

                started = time.monotonic()
                with self.lock:
                    first_new = sim.log.next_id
                    sim.tick()
                    if sim.tick_count % sim.config.save_every_ticks == 0 or step or self.paused:
                        store.save(sim)
                    new = [e for e in sim.log.recent if e.id >= first_new]
                    names = {a.id: a.name for a in sim.population.agents}
                    pending.extend(new)
                    now = time.monotonic()
                    if self.speed or now - last_sent >= 1 / MAX_FRAMES_PER_SECOND or step:
                        self._send(views.frame(sim, pending, names))
                        pending, last_sent = [], now
                    everyone_dead = not sim.population.get_alive()
                self.ticks_done += 1
                if self.max_ticks is not None and self.ticks_done >= self.max_ticks:
                    self.stop_reason = "finished"
                    break

                if everyone_dead:
                    self.paused = True
                    self._send(
                        {
                            "type": "status",
                            "paused": True,
                            "speed": self.speed,
                            "message": "Everyone has died.",
                        }
                    )

                if self.speed:
                    wait = 1 / self.speed - (time.monotonic() - started)
                    if wait > 0:
                        self.stop.wait(wait)
        finally:
            with self.lock:
                store.save(sim)  # whatever ran since the last save
            store.end_run(run_id, sim.tick_count, self.stop_reason)
            store.close()

    def _apply_commands(self) -> bool:
        step = False
        while True:
            try:
                cmd = self.commands.get_nowait()
            except queue.Empty:
                break
            match cmd.get("cmd"):
                case "pause":
                    self.paused = True
                case "play":
                    self.paused = False
                case "step":
                    step = True
                case "speed":
                    value = float(cmd.get("value", 1))
                    if value in SPEEDS:
                        self.speed = value
            self._send({"type": "status", "paused": self.paused, "speed": self.speed})
        return step

    def _send(self, message: dict):
        if self.publish is not None:
            self.publish(message)


def create_app(runner: Runner) -> FastAPI:
    clients: set[WebSocket] = set()
    outbox: asyncio.Queue = asyncio.Queue()

    async def broadcaster():
        while True:
            message = await outbox.get()
            text = json.dumps(message, separators=(",", ":"))
            for ws in list(clients):
                try:
                    await ws.send_text(text)
                except Exception:
                    clients.discard(ws)

    @asynccontextmanager
    async def lifespan(_app):
        loop = asyncio.get_running_loop()
        runner.publish = lambda m: loop.call_soon_threadsafe(outbox.put_nowait, m)
        task = asyncio.create_task(broadcaster())
        thread = threading.Thread(target=runner.run, name="genesis-sim", daemon=True)
        thread.start()
        await asyncio.to_thread(runner.ready.wait)
        if runner.error:
            raise runner.error
        yield
        runner.stop.set()
        await asyncio.to_thread(thread.join, 10)
        task.cancel()

    app = FastAPI(title="Genesis viewer", lifespan=lifespan)

    def reader() -> WorldStore:
        return WorldStore(runner.path, readonly=True)

    @app.get("/api/state")
    def state():
        with runner.lock:
            return views.snapshot(runner.sim, runner.paused, runner.speed)

    @app.get("/api/agent/{agent_id}")
    def agent(agent_id: int):
        store = reader()
        try:
            with runner.lock:
                detail = views.agent_detail(runner.sim, agent_id, store)
        finally:
            store.close()
        if detail is None:
            raise HTTPException(404, "No such agent")
        return detail

    @app.get("/api/why/{event_id}")
    def why(event_id: int):
        store = reader()
        try:
            with runner.lock:
                result = views.why(store, runner.sim, event_id)
        finally:
            store.close()
        if result is None:
            raise HTTPException(404, "No such event (it may not be saved yet)")
        return result

    @app.websocket("/ws")
    async def live(ws: WebSocket):
        await ws.accept()
        with runner.lock:
            first = views.snapshot(runner.sim, runner.paused, runner.speed)
        await ws.send_text(json.dumps(first, separators=(",", ":")))
        clients.add(ws)
        try:
            while True:
                runner.commands.put(json.loads(await ws.receive_text()))
        except (WebSocketDisconnect, json.JSONDecodeError):
            pass
        finally:
            clients.discard(ws)

    dist = ui_dist()
    if (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
    else:

        @app.get("/")
        def missing_ui():
            return HTMLResponse(
                "<h2>Genesis viewer</h2><p>The browser app isn't built yet. Run:</p>"
                "<pre>cd ui && npm install && npm run build</pre>"
                "<p>then restart <code>genesis view</code>.</p>"
            )

    return app


def serve(
    path: Path,
    host: str = "127.0.0.1",
    port: int = 8800,
    speed: float = 1,
    paused: bool = False,
    open_browser: bool = True,
    llm: bool | None = None,
):
    import uvicorn

    runner = Runner(path, speed=speed, paused=paused, llm=llm)
    app = create_app(runner)
    url = f"http://{host}:{port}"
    print(
        f"Genesis viewer on {url}  (Ctrl+C stops safely; the world is saved every tick)",
        flush=True,
    )
    threading.Timer(1.5, lambda: print(runner.llm_status, flush=True)).start()
    if open_browser:
        threading.Timer(1.0, _open, args=(url,)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")


def _open(url):
    import webbrowser

    webbrowser.open(url)
