import konfi

__all__ = ["Config", "load_config"]


@konfi.template()
class Config:
    command_prefix: str = ">"
    discord_token: str


def load_config(path: str) -> Config:
    konfi.set_sources(
        konfi.FileLoader(path, ignore_not_found=True),
        konfi.Env("BOT_"),
    )

    return konfi.load(Config)
