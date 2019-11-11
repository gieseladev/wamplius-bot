"""Command-line interface for wamplius."""

import argparse
import logging
import logging.config

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.config.dictConfig({
        "version": 1,

        "formatters": {
            "colored": {
                "()": "colorlog.ColoredFormatter",
                "format": "{log_color}{bold}{levelname:8}{reset} "
                          "{thin_purple}{name}:{reset} "
                          "{msg_log_color}{message}",
                "style": "{",
                "secondary_log_colors": {
                    "msg": {
                        "DEBUG": "white",
                        "INFO": "blue",
                        "WARNING": "yellow",
                        "ERROR": "red",
                        "CRITICAL": "bold_red",
                    },
                },
            },
        },

        "handlers": {
            "console": {
                "class": "colorlog.StreamHandler",
                "formatter": "colored",
            },
        },

        "loggers": {
            "aiowamp": {
                "level": "DEBUG",
            },
            "libwampli": {
                "level": "DEBUG",
            },
            "wamplius": {
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
        import uvloop
    except ImportError:
        log.info("not using uvloop")
    else:
        log.info("using uvloop")
        uvloop.install()


def get_parser() -> argparse.ArgumentParser:
    """Get the argument parser.

    The parser provides the config argument.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument("-c", "--config", default="config.toml", help="specify config file")

    return parser


def run(args: argparse.Namespace) -> None:
    """Run the bot with the given arguments from `get_parser`."""
    _setup_logging()
    _setup_uvloop()

    import wamplius

    config = wamplius.load_config(args.config)

    bot = wamplius.create_bot(config)

    log.info("starting bot")
    bot.run(config.discord_token)


def main() -> None:
    """Main entry point.

    Parses the command-line arguments and runs the bot.
    """
    parser = get_parser()
    args = parser.parse_args()

    run(args)


if __name__ == "__main__":
    main()
