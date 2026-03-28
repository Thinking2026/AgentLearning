from __future__ import annotations

import sys
from pathlib import Path

from application import AgentApplication
from utils.log import Logger, zap


def main() -> None:
    config_path = Path(__file__).with_name("config.json")
    logger = Logger()
    try:
        application = AgentApplication(config_path)
        application.run()
    except Exception as exc:
        logger.error(
            "Agent application initialization failed or run encountered an error",
            zap.any("config_path", config_path),
            zap.any("error", exc),
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
