import asyncio
import atexit
import contextlib
import dataclasses
import dbm
import json
import logging
import pathlib
from typing import Any, Dict, Iterator, MutableMapping, Optional

import discord
import libwampli
from autobahn import wamp
from discord.ext import commands

__all__ = ["WampliusCog",
           "get_conn_id"]

log = logging.getLogger(__name__)

DB_PATH = pathlib.Path("data/connections/db")


@dataclasses.dataclass()
class DBItem:
    wamp_config: Optional[libwampli.ConnectionConfig]
    subscriptions: Dict[str, int]

    @classmethod
    def unmarshal_json(cls, data: str):
        data = json.loads(data)
        config = libwampli.ConnectionConfig(**data.pop("wamp_config"))
        return cls(config, **data)

    def as_dict(self) -> Dict[str, Any]:
        data = {"subscriptions": self.subscriptions}

        config = self.wamp_config
        if config:
            data["wamp_config"] = {
                "realm": config.realm,
                "transports": config.transports,
            }

        return data

    def marshal_json(self) -> str:
        return json.dumps(self.as_dict())


class WampliusCog(commands.Cog, name="Wamplius"):
    bot: commands.Bot

    _connections: Dict[int, libwampli.Connection]
    _subscription_channels: Dict[int, Dict[str, discord.TextChannel]]
    _db: MutableMapping[str, str]

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        self._connections = {}
        self._subscription_channels = {}

        DB_PATH.parent.mkdir(parents=True, exist_ok=True)

        self._db = dbm.open(str(DB_PATH), flag="c")
        # noinspection PyUnresolvedReferences
        # because it's not truly a MutableMapping
        atexit.register(self._db.close)

        try:
            self.__load_from_db()
        except Exception:
            log.exception("couldn't load connections from database")

    def __load_from_db(self) -> None:
        for raw_conn_id, raw_item in self._db.items():
            item = DBItem.unmarshal_json(raw_item)
            conn_id = int(raw_conn_id)

            config = item.wamp_config
            if not config:
                continue

            planned_subscriptions = set(item.subscriptions.keys())
            connection = libwampli.Connection(config, planned_subscriptions=planned_subscriptions)
            self.__ready_connection(conn_id, connection)

            log.debug("loaded %s for id %s from database", connection, conn_id)
            self._connections[conn_id] = connection

    def __ready_connection(self, conn_id: int, connection: libwampli.Connection) -> None:
        def on_event(event):
            return self.on_subscription_event(conn_id, event)

        connection.on(libwampli.SubscriptionEvent, on_event)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for raw_conn_id, raw_item in self._db.items():
            item = DBItem.unmarshal_json(raw_item)
            conn_id = int(raw_conn_id)

            subscriptions = {}
            for topic, channel_id in item.subscriptions.items():
                channel = self.bot.get_channel(channel_id)
                if channel:
                    subscriptions[topic] = channel
                else:
                    log.warning(f"couldn't find channel {channel_id}")

            log.debug(f"loaded %s subscription channel(s) for id %s from database", len(subscriptions), conn_id)
            self._subscription_channels[conn_id] = subscriptions

    @commands.Cog.listener()
    async def on_disconnect(self) -> None:
        coros = (conn.close() for conn in self._connections.values())
        await asyncio.gather(*coros)

    def _remove_connection(self, conn_id: int) -> asyncio.Future:
        # let the KeyError bubble
        connection = self._connections.pop(conn_id)

        try:
            del self._db[str(conn_id)]
        except KeyError:
            pass

        try:
            del self._subscription_channels[conn_id]
        except KeyError:
            pass

        loop = asyncio.get_event_loop()
        return loop.create_task(connection.close())

    @contextlib.contextmanager
    def _with_db_writeback(self, conn_id: int) -> Iterator[DBItem]:
        key = str(conn_id)

        try:
            raw_item = self._db[key]
        except KeyError:
            item = DBItem(None, {})
        else:
            item = DBItem.unmarshal_json(raw_item)

        yield item
        log.debug("writing to %s", key)
        self._db[key] = item.marshal_json()

    def _switch_connection(self, conn_id: int, new_connection: libwampli.Connection) -> None:
        try:
            connection = self._connections[conn_id]
        except KeyError:
            pass
        else:
            # don't do anything if it's the same connection
            if connection is new_connection:
                return

            loop = asyncio.get_event_loop()
            loop.create_task(connection.close())

            # noinspection PyProtectedMember
            # sighs, this should've been better...
            new_connection._planned_subscriptions = connection._planned_subscriptions

        self._connections[conn_id] = new_connection

        with self._with_db_writeback(conn_id) as item:
            item.wamp_config = new_connection.config

        self.__ready_connection(conn_id, new_connection)

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
        embed = discord.Embed()

        try:
            connection = self._connections[get_conn_id(ctx)]
        except KeyError:
            embed.title = "Not connected and not configured"
            embed.colour = discord.Colour.orange()
        else:
            config = connection.config

            embed.title = "Connected" if connection.connected else "Configured"
            embed.colour = discord.Colour.blue() if connection.connected else discord.Colour.gold()

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

    def __get_channel_map(self, conn_id: int) -> Dict[str, discord.TextChannel]:
        try:
            value = self._subscription_channels[conn_id]
        except KeyError:
            value = self._subscription_channels[conn_id] = {}

        return value

    async def on_subscription_event(self, conn_id: int,
                                    event: libwampli.SubscriptionEvent) -> None:
        channels = self.__get_channel_map(conn_id)
        try:
            channel = channels[event.uri]
        except KeyError:
            log.error(f"Couldn't find text channel for event {event}")
            return

        embed = discord.Embed(title=f"Event {event.uri}",
                              colour=discord.Colour.blue())

        args_str = maybe_wrap_yaml(event.format_args())
        if args_str:
            embed.add_field(name="Arguments", value=args_str, inline=False)

        kwargs_str = maybe_wrap_yaml(event.format_kwargs())
        if kwargs_str:
            embed.add_field(name="Keyword Arguments", value=kwargs_str, inline=False)

        await channel.send(embed=embed)

    def __update_db_subscriptions(self, conn_id: int, subscriptions: Dict[str, discord.TextChannel]) -> None:
        with self._with_db_writeback(conn_id) as item:
            item.subscriptions = {topic: channel.id for topic, channel in subscriptions.items()}

    @commands.command("subscribe")
    async def subscribe_cmd(self, ctx: commands.Context, *topics: str) -> None:
        connection = self._cmd_get_connection(ctx)
        conn_id = get_conn_id(ctx)
        subscriptions = self.__get_channel_map(conn_id)

        subscribed = []
        already_subscribed = []
        for topic in topics:
            if connection.has_planned_subscription(topic):
                already_subscribed.append(topic)
                continue

            await connection.add_subscription(topic)
            subscriptions[topic] = ctx.channel
            subscribed.append(topic)

        self.__update_db_subscriptions(conn_id, subscriptions)

        embed = discord.Embed(colour=discord.Colour.green())
        if not subscribed:
            embed.title = "Already subscribed to all topics"
        elif not already_subscribed:
            if len(subscribed) == 1:
                embed.title = f"Subscribed to {topics[0]}"
            else:
                embed.title = "Subscribed to all topics"
        else:
            embed.title = "Subscribed to some topics"
            embed.add_field(name="Subscribed",
                            value="\n".join(subscribed),
                            inline=False)
            embed.add_field(name="Already subscribed",
                            value="\n".join(already_subscribed),
                            inline=False)

        await ctx.send(embed=embed)

    @commands.command("unsubscribe")
    async def unsubscribe_cmd(self, ctx: commands.Context, *topics: str) -> None:
        connection = self._cmd_get_connection(ctx)
        conn_id = get_conn_id(ctx)
        subscriptions = self.__get_channel_map(conn_id)

        unsubscribed = []
        already_unsubscribed = []
        for topic in topics:
            if not connection.has_planned_subscription(topic):
                already_unsubscribed.append(topic)
                continue

            await connection.remove_subscription(topic)
            del subscriptions[topic]
            unsubscribed.append(topic)

        self.__update_db_subscriptions(conn_id, subscriptions)

        embed = discord.Embed(colour=discord.Colour.green())
        if not unsubscribed:
            embed.title = "Not subscribed to any topic"
        elif not already_unsubscribed:
            if len(unsubscribed) == 1:
                embed.title = f"Unsubscribed from {topics[0]}"
            else:
                embed.title = "Unsubscribed from all topics"
        else:
            embed.title = "Unsubscribed from some topics"
            embed.add_field(name="Unsubscribed",
                            value="\n".join(unsubscribed),
                            inline=False)
            embed.add_field(name="Not subscribed",
                            value="\n".join(already_unsubscribed),
                            inline=False)

        await ctx.send(embed=embed)

    @commands.command("subscriptions")
    async def subscriptions_cmd(self, ctx: commands.Context) -> None:
        subscriptions = self.__get_channel_map(get_conn_id(ctx))

        embed = discord.Embed(colour=discord.Colour.blue())

        if not subscriptions:
            embed.title = "No active subscriptions in this guild"
            await ctx.send(embed=embed)
            return

        embed.title = "Subscriptions"

        by_channel = {}
        for topic, channel in subscriptions.items():
            by_channel.setdefault(channel, []).append(topic)

        for channel, topics in by_channel.items():
            topics_str = "\n".join(f"- {topic}" for topic in topics)
            embed.add_field(name=f"#{channel.name}", value=topics_str, inline=False)

        await ctx.send(embed=embed)


def get_conn_id(ctx: commands.Context) -> int:
    guild = ctx.guild

    if guild is not None:
        return guild.id
    else:
        return ctx.author.id


def wrap_yaml(s: str) -> str:
    return f"```yaml\n{s}```"


def maybe_wrap_yaml(s: str) -> str:
    if s.count("\n") > 1:
        return wrap_yaml(s)
    else:
        return s


def discord_format(o: Any) -> str:
    s = libwampli.human_result(o)
    return maybe_wrap_yaml(s)
