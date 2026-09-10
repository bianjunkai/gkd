import argparse
from pathlib import Path

import uvicorn

from .config import Settings


def main():
    parser = argparse.ArgumentParser(description="启动归刻本地 API 与已构建的 Web 工作台")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    uvicorn.run("app.main:create_app", factory=True, host=args.host or settings.host,
                port=args.port or settings.port, reload=args.reload,
                reload_dirs=[str(Path(__file__).parent)] if args.reload else None,
                access_log=False)


if __name__ == "__main__":
    main()
