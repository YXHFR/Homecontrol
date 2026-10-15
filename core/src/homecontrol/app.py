"""HTTP-gränssnittet: hälsokontroll och det byggda webbgränssnittet."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from homecontrol import __version__


def create_app(web_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Homecontrol", version=__version__, docs_url=None, redoc_url=None)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    # Webbgränssnittet monteras sist så att API-vägarna ovan har företräde.
    if web_dir is not None and web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")

    return app
