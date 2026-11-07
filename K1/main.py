from __future__ import annotations

from k1edge.app import create_app_runtime, serve
from k1edge.config import parse_config


def main() -> None:
    config = parse_config()
    runtime = create_app_runtime(config)
    serve(runtime)


if __name__ == "__main__":
    main()
