"""Startpunkt: `python -m homecontrol`."""

import uvicorn

from homecontrol.app import create_app
from homecontrol.config import Settings


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(create_app(settings.web_dir), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
