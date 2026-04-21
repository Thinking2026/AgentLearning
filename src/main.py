from __future__ import annotations

import os
import sys
from pathlib import Path

from application import AgentApplication
from utils.env_loader import load_dotenv
from utils.log import Logger, zap
from utils.runtime_env import set_project_root


def main() -> None:
    project_root = set_project_root(Path(__file__).resolve().parent)
    os.chdir(project_root)
    load_dotenv(project_root / ".env")
    config_path = project_root / "config.json"
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
