"""Configuration for the wamplius bot."""

import konfi

__all__ = ["Config", "load_config"]


@konfi.template()
class Config:
    """Config for the wamplius bot."""
    command_prefix: str = ">"
    discord_token: str


def load_config(path: str) -> Config:
    """Load the configuration from the given path.

    The file configs are then overwritten by the environment variables
    with the "BOT_" prefix.
    """
    konfi.set_sources(
        konfi.FileLoader(path, ignore_not_found=True),
        konfi.Env("BOT_"),
    )

    return konfi.load(Config)
