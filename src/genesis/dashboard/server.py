"""`genesis dashboard <world>`: the observer dashboard (doc 12), first version.

A separate, read-only process: it opens the world file read-only (WAL lets it read while
`genesis run` or `genesis view` writes), so it can start, stop or crash without touching
the simulation. Every page is a few indexed SQL queries; communication statistics are
updated incrementally.

Conversations: every message between the same two agents in the same tick belongs to one
conversation (a greeting and its replies, a request and its answer).
"""

import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from genesis.dashboard.common import World, dashboard_dist
from genesis.dashboard.routes import MODULES


def create_app(path: Path) -> FastAPI:
    world = World(path)
    app = FastAPI(title="Genesis dashboard")
    for module in MODULES:
        module.register(app, world)

    dist = dashboard_dist()
    if (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
    else:

        @app.get("/")
        def missing():
            return HTMLResponse(
                "<h2>Genesis dashboard</h2><p>The browser app isn't built yet. Run:</p>"
                "<pre>cd dashboard && npm install && npm run build</pre>"
            )

    return app


def serve(path: Path, host="127.0.0.1", port=8810, open_browser=True):
    import uvicorn

    url = f"http://{host}:{port}"
    print(
        f"Genesis dashboard on {url} (read-only; safe beside `genesis run` or `view`)", flush=True
    )
    if open_browser:
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(path), host=host, port=port, log_level="warning")
