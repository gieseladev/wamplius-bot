"""Cog for discord.py's commands framework.


This cog doesn't depend on wamplius, it can be extracted and work by itself.
It requires `libwampli` to work.
"""

import asyncio
import atexit
import contextlib
import dataclasses
import dbm
import json
import logging
import pathlib
import re
from typing import Any, Awaitable, Callable, Dict, Iterable, Iterator, List, Mapping, MutableMapping, Optional, Pattern, \
    Set, Tuple, Type, TypeVar, Union, cast

import aiowamp
import discord
import libwampli
from discord.ext import commands

__all__ = ["WampliusCog"]

log = logging.getLogger(__name__)

DB_PATH = pathlib.Path("data/connections/db")


@dataclasses.dataclass()
class DBItem:
    """Stored data for a connection id.

    Attributes:
        wamp_config (Optional[libwampli.ConnectionConfig]): Config for the
            connection.
        subscriptions (Dict[str, int]): Subscriptions for the connection.
            Mapping topic to the channel id.

        aliases (Dict[str, str]): Mapping from alias to URI.
        macros (Dict[str, Tuple[str, Tuple[str, ...]]]): Macro calls. Mapping
            from macro name to a tuple containing the action and the arguments.
    """
    wamp_config: Optional[libwampli.ConnectionConfig]
    subscriptions: Dict[str, int] = dataclasses.field(default_factory=dict)

    aliases: Dict[str, str] = dataclasses.field(default_factory=dict)
    macros: Dict[str, Tuple[str, Tuple[str, ...]]] = dataclasses.field(default_factory=dict)

    @classmethod
    def unmarshal_json(cls, data: str):
        """Load a `DBItem` from the raw json data."""
        data = json.loads(data)
        try:
            wamp_config = data.pop("wamp_config")
        except KeyError:
            config = None
        else:
            config = libwampli.ConnectionConfig(**wamp_config)

        return cls(config, **data)

    def as_dict(self) -> Dict[str, Any]:
        """Convert the item to a dictionary."""
        data = {"subscriptions": self.subscriptions,
                "aliases": self.aliases,
                "macros": self.macros}

        config = self.wamp_config
        if config:
            data["wamp_config"] = {
                "realm": config.realm,
                "transports": config.transports,
            }

        return data

    def marshal_json(self) -> str:
        """Encode the item using JSON and return the resulting string."""
        return json.dumps(self.as_dict())


EventHandler = Callable


class LazyClient(Awaitable[aiowamp.ClientABC]):
    subscriptions: Set[str]
    config: libwampli.ConnectionConfig

    __client_task: Optional[asyncio.Task]

    __on_event: EventHandler

    def __init__(self, config: libwampli.ConnectionConfig, on_event: EventHandler) -> None:
        self.subscriptions = set()
        self.config = config

        self.__client_task = None

        self.__on_event = on_event

    def __str__(self) -> str:
        client = self.client
        if client:
            return str(self.client)

        return str(self.config)

    @property
    def client(self) -> Optional[aiowamp.ClientABC]:
        try:
            return self.__client_task.result()
        except Exception:
            return None

    @property
    def connected(self) -> bool:
        return self.client is not None

    async def __connect(self) -> aiowamp.ClientABC:
        config = self.config
        client = await aiowamp.connect(config.endpoint, realm=config.realm)

        await asyncio.gather(*(client.subscribe(topic, self.__on_event)
                               for topic in self.subscriptions))

        return client

    def __await__(self):
        if self.__client_task is None:
            loop = asyncio.get_running_loop()
            self.__client_task = loop.create_task(self.__connect())

        return self.__client_task.__await__()

    async def close(self) -> None:
        client = self.client
        if client:
            await client.close()
        else:
            self.__client_task.cancel()

    async def sub(self, topic: str) -> None:
        if topic in self.subscriptions:
            return

        client = self.client
        if client:
            await client.subscribe(topic, self.__on_event)

        self.subscriptions.add(topic)

    async def sub_topics(self, topics: Iterable[str]) -> None:
        await asyncio.gather(*map(self.sub, topics))

    async def unsub(self, topic: str) -> None:
        self.subscriptions.discard(topic)

        client = self.client
        if not client:
            return

        with contextlib.suppress(KeyError):
            await client.unsubscribe(topic)


class WampliusCog(commands.Cog, name="Wamplius"):
    bot: commands.Bot

    _clients: Dict[int, LazyClient]
    _subscription_channels: Dict[int, Dict[str, discord.TextChannel]]
    _db: MutableMapping[str, str]

    def __init__(self, bot: commands.Bot, *,
                 db_path: Union[str, pathlib.Path] = DB_PATH) -> None:
        self.bot = bot

        self._clients = {}
        self._subscription_channels = {}

        db_path = pathlib.Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db = dbm.open(str(db_path), flag="c")
        # noinspection PyUnresolvedReferences
        # because it's not truly a MutableMapping
        atexit.register(self._db.close)

        try:
            self.__load_from_db()
        except Exception:
            log.exception("couldn't load connections from database")

    def __load_from_db(self) -> None:
        for raw_conn_id, raw_item in iter_items(self._db):
            item = DBItem.unmarshal_json(raw_item)
            conn_id = int(raw_conn_id)

            config = item.wamp_config
            if not config:
                continue

            client = LazyClient(config, self.on_subscription_event)
            client.subscriptions = set(item.subscriptions.keys())

            log.debug("loaded %s for id %s from database", client, conn_id)
            self._clients[conn_id] = client

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Handler for when the bot is ready.

        Loads the channels for the subscriptions.
        """
        for raw_conn_id, raw_item in iter_items(self._db):
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
        """Handler for when the bot disconnects.

        Closes all connections.
        """
        self._subscription_channels.clear()
        coros = (conn.close() for conn in self._clients.values())
        await asyncio.gather(*coros)

    def _get_db_item(self, conn_id: Union[int, str]) -> DBItem:
        key = str(conn_id)

        try:
            raw_item = self._db[key]
        except KeyError:
            item = DBItem(None)
        else:
            item = DBItem.unmarshal_json(raw_item)

        return item

    def _get_aliases(self, conn_id: int) -> Mapping[str, str]:
        return self._get_db_item(conn_id).aliases

    @contextlib.contextmanager
    def _with_db_writeback(self, conn_id: int) -> Iterator[DBItem]:
        key = str(conn_id)

        item = self._get_db_item(key)

        yield item
        log.debug("writing to %s", key)
        self._db[key] = item.marshal_json()

    async def _switch_client(self, conn_id: int, new_client: LazyClient) -> None:
        try:
            client = self._clients[conn_id]
        except KeyError:
            pass
        else:
            # don't do anything if it's the same client
            if client is new_client:
                return

            await new_client.sub_topics(client.subscriptions)
            await client.close()

        self._clients[conn_id] = new_client

        with self._with_db_writeback(conn_id) as item:
            item.wamp_config = new_client.config

        log.debug("switched client %s to %s", conn_id, new_client)

    def _cmd_get_lazy_client(self, ctx: commands.Context) -> LazyClient:
        try:
            return self._clients[get_conn_id(ctx)]
        except KeyError:
            raise commands.CommandError("Not configured to a router") from None

    async def _cmd_get_client(self, ctx: commands.Context) -> aiowamp.ClientABC:
        return await self._cmd_get_lazy_client(ctx)

    @commands.command("status")
    async def status_cmd(self, ctx: commands.Context) -> None:
        """Get the status of the connection."""
        embed = discord.Embed()

        try:
            connection = self._clients[get_conn_id(ctx)]
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

    @commands.command("connect", usage="[<url> <realm>]")
    async def connect_cmd(self, ctx: commands.Context, url: str = None, realm: str = None) -> None:
        """Connect to the router.

        If a config exists this can be called without providing the details.
        To establish a new connection, provide the url and realm.
        """
        if bool(url) != bool(realm):
            raise commands.UserInputError("if url is specified realm cannot be omitted")

        if url:
            client = LazyClient(libwampli.ConnectionConfig(realm, url), self.on_subscription_event)
        else:
            client = self._cmd_get_lazy_client(ctx)

        try:
            await client
        except OSError:
            raise commands.CommandError("Couldn't connect") from None

        await self._switch_client(get_conn_id(ctx), client)

        embed = discord.Embed(title="Joined session", colour=discord.Colour.green())
        await ctx.send(embed=embed)

    @commands.command("disconnect")
    async def disconnect_cmd(self, ctx: commands.Context) -> None:
        """Disonnect from the router."""
        client = self._clients.get(get_conn_id(ctx))

        if not (client and client.connected):
            raise commands.CommandError("not connected")

        await client.close()

        embed = discord.Embed(title="disconnected", colour=discord.Colour.green())
        await ctx.send(embed=embed)

    async def perform_call(self, ctx: commands.Context, args: Iterable[str]) -> Any:
        client = await self._cmd_get_client(ctx)

        args = await substitute_variables(ctx, args)
        args, kwargs = libwampli.parse_args(args)
        libwampli.ready_uri(args, aliases=self._get_aliases(get_conn_id(ctx)))

        try:
            return await client.call(*args, kwargs=kwargs)
        except aiowamp.Error as e:
            raise commands.CommandError(str(e)) from None

    @commands.command("call", usage="<procedure> [arg]...")
    async def call_cmd(self, ctx: commands.Context, *, args: str) -> None:
        """Call a procedure.

        You can use the function-style syntax:
        call wamp.session.get($GUILD_ID, key="value")
        """
        args = libwampli.split_arg_string(args)

        result = await self.perform_call(ctx, args)

        embed = discord.Embed(description=discord_format(result), colour=discord.Colour.green())
        await ctx.send(embed=embed)

    async def perform_publish(self, ctx: commands.Context, args: Iterable[str]) -> None:
        client = await self._cmd_get_client(ctx)

        args = await substitute_variables(ctx, args)
        args, kwargs = libwampli.parse_args(args)
        libwampli.ready_uri(args, aliases=self._get_aliases(get_conn_id(ctx)))

        try:
            await client.publish(*args, kwargs=kwargs, acknowledge=True)
        except aiowamp.Error as e:
            raise commands.CommandError(str(e)) from None

    @commands.command("publish", usage="<topic> [arg]...")
    async def publish_cmd(self, ctx: commands.Context, *, args: str) -> None:
        """Publish an event to a topic."""
        args = libwampli.split_arg_string(args)
        await self.perform_publish(ctx, args)

        embed = discord.Embed(title="Done", colour=discord.Colour.green())
        await ctx.send(embed=embed)

    def __get_channel_map(self, conn_id: int) -> Dict[str, discord.TextChannel]:
        try:
            value = self._subscription_channels[conn_id]
        except KeyError:
            value = self._subscription_channels[conn_id] = {}

        return value

    # FIXME: needs new version of aiowamp
    async def on_subscription_event(self, conn_id: int,
                                    event: libwampli.SubscriptionEvent) -> None:
        """Handler for events received for subscribed topics."""
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
        """Subscribe to a topic.

        You can pass multiple topics to subscribe to.
        """
        client = self._cmd_get_lazy_client(ctx)
        conn_id = get_conn_id(ctx)
        subscriptions = self.__get_channel_map(conn_id)

        subscribed = []
        already_subscribed = []
        for topic in topics:
            if topic in client.subscriptions:
                already_subscribed.append(topic)
                continue

            await client.sub(topic)
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
        """Unsubscribe from a topic.

        You can also pass multiple topics to unsubscribe from.
        """
        client = self._cmd_get_lazy_client(ctx)
        conn_id = get_conn_id(ctx)
        subscriptions = self.__get_channel_map(conn_id)

        unsubscribed = []
        already_unsubscribed = []
        for topic in topics:
            if topic not in client.subscriptions:
                already_unsubscribed.append(topic)
                continue

            await client.unsub(topic)
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
        """See the subscriptions."""
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

    @commands.group("alias", invoke_without_command=True)
    async def alias_group(self, ctx: commands.Context) -> None:
        """Manage URI aliases.

        Registered aliases can be used instead of the fully qualified URI.
        Instead of having to type "com.wamp.session.list" every time you can
        add an alias "alias add sesslist com.wamp.session.list" and use
        "sesslist" whenever a URI is applicable.
        """
        await ctx.send_help(self.alias_group)

    @alias_group.command("list", aliases=("ls",))
    async def alias_list_cmd(self, ctx: commands.Context) -> None:
        """List all aliases."""
        item = self._get_db_item(get_conn_id(ctx))
        aliases: List[Tuple[str, str]] = []
        max_alias_len = 0

        for alias, uri in item.aliases.items():
            aliases.append((alias, uri))
            alen = len(aliases)
            if alen > max_alias_len:
                max_alias_len = alen

        if not aliases:
            await ctx.send(embed=discord.Embed(
                title="No aliases set",
                colour=discord.Colour.blue(),
            ))
            return

        alias_str_gen = (f"`{alias:{max_alias_len}}` {uri}" for alias, uri in aliases)
        aliases_str = "\n".join(sorted(alias_str_gen))

        await ctx.send(embed=discord.Embed(
            title="Aliases",
            description=aliases_str,
            colour=discord.Colour.blue(),
        ))

    @alias_group.command("add")
    async def alias_add_cmd(self, ctx: commands.Context, alias: str, uri: str) -> None:
        """Add an alias for a uri."""
        with self._with_db_writeback(get_conn_id(ctx)) as item:
            item = cast(DBItem, item)
            previous_uri = item.aliases.get(alias)

            item.aliases[alias] = uri

        if previous_uri:
            text = f"Changed alias {alias} from {previous_uri} to {uri}"
        else:
            text = f"Added alias {alias} for {uri}"

        await ctx.send(embed=discord.Embed(
            title=text,
            colour=discord.Colour.green(),
        ))

    @alias_group.command("remove", aliases=("rm",))
    async def alias_remove_cmd(self, ctx: commands.Context, alias: str) -> None:
        """Remove an alias."""
        try:
            with self._with_db_writeback(get_conn_id(ctx)) as item:
                item = cast(DBItem, item)
                uri = item.aliases.pop(alias)
        except KeyError:
            raise commands.UserInputError(f"No alias {alias} exists") from None

        await ctx.send(embed=discord.Embed(
            title=f"Removed alias {alias} for {uri}",
            colour=discord.Colour.green(),
        ))

    @commands.group("macro", usage="<macro>", invoke_without_command=True)
    async def macro_group(self, ctx: commands.Context, macro: str = None) -> None:
        """Manage macros.

        Macros assign a name to a pre-defined operation. Macros can be created
        for publishing topics and calling procedures.

        To run a macro use "macro <name>".

        Variables are evaluated when the macro is called, not when created.
        """
        if not macro:
            await ctx.send_help(self.macro_group)
            return

        item = self._get_db_item(get_conn_id(ctx))
        try:
            op, args = item.macros[macro]
        except KeyError:
            raise commands.UserInputError(f"Macro {macro} not found") from None

        if op == "call":
            await self.perform_call(ctx, args)
        elif op == "publish":
            await self.perform_publish(ctx, args)

    @macro_group.command("list", aliases=("ls",))
    async def macro_list_cmd(self, ctx: commands.Context) -> None:
        """List all macros."""
        item = self._get_db_item(get_conn_id(ctx))

        embed = discord.Embed(title="Macros", colour=discord.Colour.blue())
        if not item.macros:
            embed.title = "No macros"
            await ctx.send(embed=embed)
            return

        for name, (cmd, args) in item.macros.items():
            f = libwampli.format_function_style(args)
            embed.add_field(name=f"{name} ({cmd})",
                            value=f,
                            inline=False)

        await ctx.send(embed=embed)

    @macro_group.command("add", usage="<name> <operation> <uri> [arg]...")
    async def macro_add_cmd(self, ctx: commands.Context, name: str, operation: str, *, args: str) -> None:
        """Define a new macro.

        You can use the function-style syntax to define a macro:
        macro add call wamp.session.get($GUILD_ID)
        """
        if operation not in ("call", "publish"):
            raise commands.UserInputError(f"Unknown operation {operation}, expected call or publish")

        args = libwampli.split_arg_string(args)

        try:
            sub_args = await substitute_variables(ctx, args)
            libwampli.parse_args(sub_args)
        except Exception as e:
            raise commands.CommandError(f"Couldn't parse arguments: {e}")

        with self._with_db_writeback(get_conn_id(ctx)) as item:
            item = cast(DBItem, item)
            item.macros[name] = (operation, tuple(args))

        await ctx.send(embed=discord.Embed(
            title=f"Added macro {name}",
            colour=discord.Colour.green(),
        ))

    @macro_group.command("remove", aliases=("rm",))
    async def macro_remove_cmd(self, ctx: commands.Context, name: str) -> None:
        """Remove a macro."""
        try:
            with self._with_db_writeback(get_conn_id(ctx)) as item:
                item = cast(DBItem, item)
                item.macros.pop(name)
        except KeyError:
            raise commands.UserInputError(f"Macro {name} doesn't exist")

        await ctx.send(embed=discord.Embed(
            title=f"Removed macro {name}",
            colour=discord.Colour.green(),
        ))


def get_conn_id(ctx: commands.Context) -> int:
    """Get the id used as a key for the connection.

    This is the guild id unless the context is a direct message, in
    which case the user id is returned.
    """
    guild = ctx.guild

    if guild is not None:
        return guild.id
    else:
        return ctx.author.id


def wrap_yaml(s: str) -> str:
    """Wrap the given string in a yaml block."""
    return f"```yaml\n{s}```"


def maybe_wrap_yaml(s: str) -> str:
    """Wrap the given string in a yaml block if it spans multiple lines."""
    if s.count("\n") > 1:
        return wrap_yaml(s)
    else:
        return s


K = TypeVar("K")
V = TypeVar("V")


def iter_items(mapping: Mapping[K, V]) -> Iterator[Tuple[K, V]]:
    """Iterate over (key, value) pairs of a mapping.

    Iterates over the mapping's keys and yields it together with the
    corresponding value.

    Can be used to iterate over objects which don't provide the items
    iterator, but have a keys iterator. Looking at you, dbm!
    """
    for key in mapping.keys():
        yield (key, mapping[key])


def discord_format(o: Any) -> str:
    """Format an object to a discord readable format.

    Uses `libwampli.human_result` and passes it to `maybe_wrap_yaml`.
    """
    s = libwampli.human_result(o)
    return maybe_wrap_yaml(s)


async def call_converter(converter: Type[commands.Converter], ctx: commands.Context, arg: str) -> Any:
    """Call a converter and return the result."""
    return await converter().convert(ctx, arg)


# match mentions and capture snowflake in a group
RE_SNOWFLAKE_MATCH: Pattern = re.compile(r"<[@#](\d+)>")

# match (x) as y conversions capturing x and y in groups
RE_CONVERSION_MATCH: Pattern = re.compile(r"\((.+?)\) as (\w{2,})")

# Mapping of converter to aliases
CONVERTER_ALIASES: Dict[Type[commands.Converter], Tuple[str, ...]] = {
    commands.TextChannelConverter: ("tc", "channel", "TextChannel"),
    commands.VoiceChannelConverter: ("vc", "VoiceChannel"),
}

# Mapping of alias to converter
CONVERTERS: Dict[str, Type[commands.Converter]] = {
    key: converter
    for converter, keys in CONVERTER_ALIASES.items()
    for key in keys
}

# match $VARIABLE capturing the variable name (VARIABLE) in a group
RE_VARIABLE_MATCH: Pattern = re.compile(r"\$(\w{3,})")


async def substitute_variable(ctx: commands.Context, arg: str) -> str:
    """Perform a substitution for a single argument."""
    match = RE_SNOWFLAKE_MATCH.match(arg)
    if match:
        return match.group(1)

    match = RE_VARIABLE_MATCH.match(arg)
    if match:
        var = match.group(1).lower()

        if var == "guild_id":
            try:
                return str(ctx.guild.id)
            except AttributeError:
                raise commands.UserInputError("no guild id available") from None

    match = RE_CONVERSION_MATCH.match(arg)
    if match:
        value, typ = match.groups()
        try:
            converter = CONVERTERS[typ]
        except KeyError:
            pass
        else:
            repl = await call_converter(converter, ctx, value)
            return str(repl.id)

    return arg


async def substitute_variables(ctx: commands.Context, args: Iterable[str]) -> List[str]:
    """Substitute the variables / mentions and perform conversions."""
    return await asyncio.gather(*(substitute_variable(ctx, arg) for arg in args))
