__all__ = ["AgentApplication"]


def __getattr__(name: str):
    if name == "AgentApplication":
        from driver.application import AgentApplication

        return AgentApplication
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
