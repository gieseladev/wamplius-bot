"""Simple discord bot using the commands framework to run wamplius."""

import asyncio
import logging

import discord
from discord.ext import commands

from .cog import WampliusCog
from .config import Config

__all__ = ["create_bot"]

log = logging.getLogger(__name__)


def create_bot(config: Config, *,
               loop: asyncio.AbstractEventLoop = None) -> commands.Bot:
    """Create a commands bot with the wamplius cog loaded.

    There are also a few utility commands and event listeners added.
    """
    bot = commands.Bot(config.command_prefix, loop=loop)

    @bot.listen()
    async def on_command(ctx: commands.Context) -> None:
        log.info("command: %s", ctx.command)

    @bot.listen()
    async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
        log.info("command error:", error)

        if isinstance(error, commands.CommandInvokeError):
            error = error.original

        embed = discord.Embed(title=type(error).__name__,
                              description=str(error),
                              colour=discord.Colour.red())
        await ctx.send(embed=embed)

    @bot.command("shutdown")
    async def shutdown_cmd(ctx: commands.Context) -> None:
        await ctx.send(embed=discord.Embed(title="Goodbye", colour=discord.Colour.green()))
        await bot.close()

    bot.add_cog(WampliusCog(bot))

    return bot
