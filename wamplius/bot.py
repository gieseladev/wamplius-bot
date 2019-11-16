"""Simple discord bot using the commands framework to run wamplius."""

import asyncio
import logging

import aiowamp
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
    async def on_message_edit(_, after: discord.Message) -> None:
        await bot.process_commands(after)

    @bot.listen()
    async def on_command(ctx: commands.Context) -> None:
        log.info("command: %s", ctx.command)

    @bot.listen()
    async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
        log.info("command error: %s", error)

        if isinstance(error, commands.CommandInvokeError):
            error = error.original

        embed = discord.Embed(title=type(error).__name__,
                              description=str(error),
                              colour=discord.Colour.red())
        await ctx.send(embed=embed)

    @bot.command("shutdown")
    async def shutdown_cmd(ctx: commands.Context) -> None:
        """Shut the bot down."""
        await ctx.send(embed=discord.Embed(title="Goodbye", colour=discord.Colour.green()))
        await bot.close()

    @bot.command("version")
    async def version_cmd(ctx: commands.Context) -> None:
        """Show the version of the bot."""

        import libwampli
        import wamplius

        description = f"wamplius: `{wamplius.__version__}`\n" \
                      f"libwampli: `{libwampli.__version__}`\n" \
                      f"aiowamp: `{aiowamp.__version__}`"

        await ctx.send(embed=discord.Embed(
            title="Version",
            description=description,
            colour=discord.Colour.blue(),
        ))

    bot.add_cog(WampliusCog(bot))

    return bot
