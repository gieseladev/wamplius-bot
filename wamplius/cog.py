import asyncio
import atexit
import logging
import pathlib
import shelve
from typing import Any, Dict, Optional

from autobahn import wamp
from discord.ext import commands

import libwampli

__all__ = ["WampliusCog",
           "get_conn_id"]

log = logging.getLogger(__name__)

DB_PATH = pathlib.Path("data/connections/db")


class WampliusCog(commands.Cog, name="Wamplius"):
    loop: Optional[asyncio.AbstractEventLoop]
    bot: commands.Bot

    _connections: Dict[int, libwampli.Connection]
    _conn_db: shelve.Shelf

    def __init__(self, bot: commands.Bot, *,
                 loop: asyncio.AbstractEventLoop = None) -> None:
        self.loop = loop
        self.bot = bot

        self._connections = {}

        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn_db = shelve.open(str(DB_PATH), "c")
        atexit.register(self._conn_db.close)

        self.__load_connections_from_db()

    def __load_connections_from_db(self) -> None:
        for conn_id, config in self._conn_db.items():
            conn_id = int(conn_id)

            connection = libwampli.Connection(config, loop=self.loop)
            log.debug("loaded %s for id %s from database", connection, conn_id)
            self._connections[conn_id] = connection

    @commands.Cog.listener()
    async def on_connect(self) -> None:
        coros = (conn.open() for conn in self._connections.values())
        await asyncio.gather(*coros, loop=self.loop)

    @commands.Cog.listener()
    async def on_disconnect(self) -> None:
        coros = (conn.close() for conn in self._connections.values())
        await asyncio.gather(*coros, loop=self.loop)

    def _remove_connection(self, conn_id: int) -> asyncio.Future:
        # let the KeyError bubble
        connection = self._connections.pop(conn_id)

        try:
            del self._conn_db[str(conn_id)]
        except KeyError:
            pass

        loop = self.loop or asyncio.get_event_loop()
        return loop.create_task(connection.close())

    def _switch_connection(self, conn_id: int, new_connection: libwampli.Connection) -> None:
        try:
            connection = self._connections[conn_id]
        except KeyError:
            pass
        else:
            loop = self.loop or asyncio.get_event_loop()
            loop.create_task(connection.close())

        self._connections[conn_id] = new_connection
        self._conn_db[str(conn_id)] = new_connection.config

        log.debug("switched connection %s to %s", conn_id, new_connection)

    def _cmd_get_connection(self, ctx: commands.Context) -> libwampli.Connection:
        try:
            return self._connections[get_conn_id(ctx)]
        except KeyError:
            raise commands.CommandError("Not in a session") from None

    async def _cmd_get_session(self, ctx: commands.Context) -> wamp.ISession:
        connection = self._cmd_get_connection(ctx)

        try:
            return await asyncio.wait_for(connection.session, timeout=5, loop=self.loop)
        except asyncio.TimeoutError:
            loop = self.loop or asyncio.get_event_loop()
            loop.create_task(connection.close())
            raise commands.CommandError("Joining Session timed out!") from None

    @commands.command("status")
    async def status_cmd(self, ctx: commands.Context) -> None:
        try:
            connection = self._connections[get_conn_id(ctx)]
        except KeyError:
            await ctx.send("Not connected and not configured")
            return

        config = connection.config

        state = "Connected" if connection.connected else "Configured"

        await ctx.send(f"{state} to realm {config.realm} on {config.endpoint}")

    @commands.command("connect")
    async def connect_cmd(self, ctx: commands.Context, url: str, realm: str) -> None:
        conn_id = get_conn_id(ctx)

        transports = libwampli.get_transports(url)
        connection = libwampli.Connection(
            libwampli.ConnectionConfig(realm, transports),
            loop=self.loop
        )

        # TODO handle exception
        try:
            await connection.open()
        except OSError:
            raise commands.CommandError("Couldn't connect") from None

        self._switch_connection(conn_id, connection)

        await ctx.send(f"joined realm {realm}")

    @commands.command("disconnect")
    async def disconnect_cmd(self, ctx: commands.Context) -> None:
        # TODO just "disconnect", use separate command for clear!

        try:
            await self._remove_connection(get_conn_id(ctx))
        except KeyError:
            raise commands.CommandError("not connected")

        await ctx.send("disconnected")

    @commands.command("call")
    async def call_cmd(self, ctx: commands.Context, *, args: str) -> None:
        session = await self._cmd_get_session(ctx)

        args, kwargs = libwampli.parse_args(args)
        libwampli.ready_uri(args)

        try:
            result = await session.call(*args, **kwargs)
        except wamp.ApplicationError:
            raise

        await ctx.send(discord_format(result))

    @commands.command("publish")
    async def publish_cmd(self, ctx: commands.Context, *, args) -> None:
        session = await self._cmd_get_session(ctx)

        args, kwargs = libwampli.parse_args(args)
        libwampli.ready_uri(args)

        try:
            # TODO options set acknowledge to True
            session.publish(*args, **kwargs)
        except wamp.ApplicationError as e:
            raise commands.CommandError(e.error_message()) from None

        await ctx.send(f"published: {args[0]}")


def get_conn_id(ctx: commands.Context) -> int:
    guild = ctx.guild

    if guild is not None:
        return guild.id
    else:
        return ctx.author.id


def discord_format(o: Any) -> str:
    s = libwampli.human_result(o)
    if s.count("\n") > 1:
        return f"```yaml\n{s}```"

    return s
