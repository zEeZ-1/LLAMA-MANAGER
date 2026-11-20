import uvicorn
from .core.config import load_settings

if __name__ == "__main__":
    cfg = load_settings()
    uvicorn.run(
        "backend.app.main:app", host=cfg.server.host, port=cfg.server.port, workers=1
    )
