import asyncio
import logging

from discord.ext import commands

from .cog import WampliusCog
from .config import Config

__all__ = ["create_bot"]

log = logging.getLogger(__name__)


def create_bot(config: Config, *,
               loop: asyncio.AbstractEventLoop = None) -> commands.Bot:
    bot = commands.Bot(config.command_prefix, loop=loop)

    @bot.listen()
    async def on_command(ctx: commands.Context) -> None:
        log.info("command: %s", ctx.command)

    @bot.listen()
    async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandInvokeError):
            error = error.original

        await ctx.send(f"Error: {error}")

    bot.add_cog(WampliusCog(bot, loop=loop))

    return bot
