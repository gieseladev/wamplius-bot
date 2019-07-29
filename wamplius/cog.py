import asyncio
import atexit
import logging
import pathlib
import shelve
from typing import Any, Dict

import discord
import libwampli
from autobahn import wamp
from discord.ext import commands

__all__ = ["WampliusCog",
           "get_conn_id"]

log = logging.getLogger(__name__)

DB_PATH = pathlib.Path("data/connections/db")


class WampliusCog(commands.Cog, name="Wamplius"):
    bot: commands.Bot

    _connections: Dict[int, libwampli.Connection]
    _channels: Dict[libwampli.Connection, Dict[str, discord.TextChannel]]
    _conn_db: shelve.Shelf

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        self._connections = {}
        self._channels = {}

        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            db = shelve.open(str(DB_PATH))
        except Exception:
            log.exception("couldn't open connection database, creating new one")
            db = shelve.open(str(DB_PATH), "n")

        self._conn_db = db
        atexit.register(self._conn_db.close)

        try:
            self.__load_connections_from_db()
        except Exception:
            log.exception("couldn't load connections from database, resetting it")
            self._conn_db.clear()

    def __load_connections_from_db(self) -> None:
        for conn_id, config in self._conn_db.items():
            conn_id = int(conn_id)

            connection = libwampli.Connection(config)
            self.__add_connection_listeners(connection)
            log.debug("loaded %s for id %s from database", connection, conn_id)
            self._connections[conn_id] = connection

    def __add_connection_listeners(self, connection: libwampli.Connection) -> None:
        def on_event(event):
            return self.on_subscription_event(connection, event)

        connection.on(libwampli.SubscriptionEvent, on_event)

    @commands.Cog.listener()
    async def on_disconnect(self) -> None:
        coros = (conn.close() for conn in self._connections.values())
        await asyncio.gather(*coros)

    def _set_connection_config(self, conn_id: int, config: libwampli.ConnectionConfig) -> None:
        self._conn_db[str(conn_id)] = config

    def _remove_connection(self, conn_id: int) -> asyncio.Future:
        # let the KeyError bubble
        connection = self._connections.pop(conn_id)

        try:
            del self._conn_db[str(conn_id)]
        except KeyError:
            pass

        loop = asyncio.get_event_loop()
        return loop.create_task(connection.close())

    def _switch_connection(self, conn_id: int, new_connection: libwampli.Connection) -> None:
        try:
            connection = self._connections[conn_id]
        except KeyError:
            pass
        else:
            loop = asyncio.get_event_loop()
            loop.create_task(connection.close())

        self._connections[conn_id] = new_connection
        self._conn_db[str(conn_id)] = new_connection.config

        self.__add_connection_listeners(new_connection)

        log.debug("switched connection %s to %s", conn_id, new_connection)

    def _cmd_get_connection(self, ctx: commands.Context) -> libwampli.Connection:
        try:
            return self._connections[get_conn_id(ctx)]
        except KeyError:
            raise commands.CommandError("Not configured to a router") from None

    def _cmd_get_session(self, ctx: commands.Context) -> wamp.ISession:
        connection = self._cmd_get_connection(ctx)

        if not connection.connected:
            raise commands.CommandError("Not in a session, need to connect first!")

        return connection.component_session

    @commands.command("status")
    async def status_cmd(self, ctx: commands.Context) -> None:
        try:
            connection = self._connections[get_conn_id(ctx)]
        except KeyError:
            await ctx.send("Not connected and not configured")
            return

        config = connection.config

        state = "Connected" if connection.connected else "Configured"
        colour = discord.Colour.blue() if connection.connected else discord.Colour.gold()
        embed = discord.Embed(title=state, colour=colour)

        embed.add_field(name="endpoint", value=config.endpoint)
        embed.add_field(name="realm", value=config.realm)

        await ctx.send(embed=embed)

    @commands.command("connect")
    async def connect_cmd(self, ctx: commands.Context, url: str = None, realm: str = None) -> None:
        if bool(url) != bool(realm):
            raise commands.UserInputError("if url is specified realm cannot be omitted")

        if url:
            transports = libwampli.get_transports(url)
            connection = libwampli.Connection(libwampli.ConnectionConfig(realm, transports))
        else:
            connection = self._cmd_get_connection(ctx)
            realm = connection.config.realm

        try:
            await connection.open()
        except OSError:
            raise commands.CommandError("Couldn't connect") from None

        self._switch_connection(get_conn_id(ctx), connection)

        embed = discord.Embed(title="Joined session", colour=discord.Colour.green())
        embed.add_field(name="realm", value=realm)

        await ctx.send(embed=embed)

    @commands.command("disconnect")
    async def disconnect_cmd(self, ctx: commands.Context) -> None:
        connection = self._connections.get(get_conn_id(ctx))

        if not (connection and connection.connected):
            raise commands.CommandError("not connected")

        await connection.close()

        embed = discord.Embed(title="disconnected", colour=discord.Colour.green())
        await ctx.send(embed=embed)

    @commands.command("call")
    async def call_cmd(self, ctx: commands.Context, *, args: str) -> None:
        session = self._cmd_get_session(ctx)

        args, kwargs = libwampli.parse_args(args)
        libwampli.ready_uri(args)

        try:
            result = await session.call(*args, **kwargs)
        except wamp.ApplicationError:
            raise

        embed = discord.Embed(description=discord_format(result), colour=discord.Colour.green())
        await ctx.send(embed=embed)

    @commands.command("publish")
    async def publish_cmd(self, ctx: commands.Context, *, args) -> None:
        session = self._cmd_get_session(ctx)

        args, kwargs = libwampli.parse_args(args)
        libwampli.ready_uri(args)

        kwargs["options"] = wamp.PublishOptions(acknowledge=True)

        try:
            await session.publish(*args, **kwargs)
        except wamp.ApplicationError as e:
            raise commands.CommandError(e.error_message()) from None

        embed = discord.Embed(title="Done", colour=discord.Colour.green())
        await ctx.send(embed=embed)

    def __get_channel_map(self, connection: libwampli.Connection) -> Dict[str, discord.TextChannel]:
        try:
            value = self._channels[connection]
        except KeyError:
            value = self._channels[connection] = {}

        return value

    async def on_subscription_event(self, connection: libwampli.Connection,
                                    event: libwampli.SubscriptionEvent) -> None:
        channels = self.__get_channel_map(connection)
        try:
            channel = channels[event.uri]
        except KeyError:
            log.error(f"Couldn't find text channel for event {event}")
            return

        embed = discord.Embed(title=f"Event {event.uri}",
                              # TODO add special format_args and format_kwargs
                              #     methods to SubscriptionEvent so we can
                              #     customise it here.
                              description=str(event),
                              colour=discord.Colour.blue())

        await channel.send(embed=embed)

    @commands.command("subscribe")
    async def subscribe_cmd(self, ctx: commands.Context, topic: str) -> None:
        connection = self._cmd_get_connection(ctx)

        if connection.has_subscription(topic):
            raise commands.CommandError(f"already subscribed to {topic}")

        await connection.add_subscription(topic)
        self.__get_channel_map(connection)[topic] = ctx.channel

        embed = discord.Embed(title=f"Subscribed to {topic}", colour=discord.Colour.green())
        await ctx.send(embed=embed)

    @commands.command("unsubscribe")
    async def unsubscribe_cmd(self, ctx: commands.Context, topic: str) -> None:
        connection = self._cmd_get_connection(ctx)

        if not connection.has_subscription(topic):
            raise commands.CommandError(f"not subscribed to {topic}")

        await connection.remove_subscription(topic)
        del self.__get_channel_map(connection)[topic]

        embed = discord.Embed(title=f"Unsubscribed from {topic}", colour=discord.Colour.green())
        await ctx.send(embed=embed)


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
