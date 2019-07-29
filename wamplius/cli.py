import argparse
import asyncio
import logging
import logging.config

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    import txaio
    # start the loggers in txaio so we can then overwrite them
    txaio.start_logging(level="debug")

    logging.config.dictConfig({
        "version": 1,

        "formatters": {
            "default": {
                "format": "{levelname} {name}: {message}",
                "style": "{",
            },
        },

        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
            },
        },

        "loggers": {
            "libwampli": {
                "level": "DEBUG",
            },
            "wamplius": {
                "level": "DEBUG",
            },

            "autobahn": {
                "level": "DEBUG",
            },
        },

        "root": {
            "level": "INFO",
            "handlers": [
                "console",
            ],
        },
    })


def _setup_uvloop() -> None:
    try:
        import txaio
        import uvloop
    except ImportError:
        log.info("not using uvloop")
    else:
        log.info("using uvloop")
        uvloop.install()
        # update txaio loop because god knows they can't update it themselves
        txaio.config.loop = asyncio.get_event_loop()


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()

    parser.add_argument("-c", "--config", default="config.toml", help="specify version")

    return parser


def run(args: argparse.Namespace) -> None:
    _setup_logging()
    _setup_uvloop()

    import wamplius

    config = wamplius.load_config(args.config)

    bot = wamplius.create_bot(config)

    log.info("starting bot")
    bot.run(config.discord_token)


def main() -> None:
    parser = get_parser()
    args = parser.parse_args()

    run(args)


if __name__ == "__main__":
    main()
