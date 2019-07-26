from typing import Optional

import konfi
from .wamplius import ComponentConfig


@konfi.template()
class Config:
    command_prefix: str = ">"

    component: Optional[ComponentConfig]


def load_config(path: str) -> Config:
    konfi.set_sources(
        konfi.FileLoader(path, ignore_not_found=True),
        konfi.Env("BOT_"),
    )

    return konfi.load(Config)
