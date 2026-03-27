from __future__ import annotations

from pathlib import Path

from application import AgentApplication


def main() -> None:
    config_path = Path(__file__).with_name("config.json")
    application = AgentApplication(config_path)
    application.run()


if __name__ == "__main__":
    main()
