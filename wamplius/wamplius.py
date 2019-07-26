import asyncio
import dataclasses
from typing import Any, Awaitable, List, Optional, Union

from autobahn import wamp
from autobahn.asyncio.component import Component
from discord.ext import commands

__all__ = ["WampliusCog",
           "ComponentConfig", "create_component"]


class WAMPConnection:
    loop: asyncio.AbstractEventLoop

    _component: Component
    _session_future: asyncio.Future

    def __init__(self, component: Component, *,
                 loop: asyncio.AbstractEventLoop = None) -> None:
        self.loop = loop or asyncio.get_event_loop()

        self._component = component
        self.__add_component_listeners(component)

        self.__reset_session()

    @property
    def component(self) -> Component:
        return self._component

    def __add_component_listeners(self, component: Component) -> None:
        component.on("join", self.on_session_join)
        component.on("connectfailure", self.on_session_connect_failure)
        component.on("leave", self.on_session_leave)

    def __reset_session(self) -> None:
        self._session_future = self.loop.create_future()

    async def on_session_join(self, session: wamp.ISession, _) -> None:
        self._session_future.set_result(session)

    async def on_session_connect_failure(self, e: Exception) -> None:
        self._session_future.set_exception(e)

    async def on_session_leave(self, _) -> None:
        self.__reset_session()

    @property
    def connected(self) -> bool:
        return self._session_future.done() and not self._session_future.exception()

    @property
    def session(self) -> Awaitable[wamp.ISession]:
        return self._session_future

    async def wait_joined(self) -> None:
        await self._session_future

    async def open(self) -> None:
        _ = self._component.start(loop=self.loop)
        await self.wait_joined()

    async def close(self) -> None:
        await self._component.stop()


class WampliusCog(commands.Cog, name="Wamplius"):
    loop: Optional[asyncio.AbstractEventLoop]
    bot: commands.Bot
    _connection: Optional[WAMPConnection]

    def __init__(self, bot: commands.Bot, component: Optional[Component], *,
                 loop: asyncio.AbstractEventLoop = None) -> None:
        self.loop = loop
        self.bot = bot

        self._connection = WAMPConnection(component, loop=self.loop) if component else None

    @commands.Cog.listener()
    async def on_connect(self) -> None:
        if self._connection:
            await self._connection.open()

    @commands.Cog.listener()
    async def on_disconnect(self) -> None:
        if self._connection:
            await self._connection.close()

    def _cmd_get_connection(self) -> WAMPConnection:
        if self._connection:
            return self._connection

        raise commands.CommandError("Not in a session")

    async def _cmd_get_session(self) -> wamp.ISession:
        connection = self._cmd_get_connection()

        try:
            return await asyncio.wait_for(connection.session, timeout=5, loop=self.loop)
        except asyncio.TimeoutError:
            self.loop.create_task(connection.close())
            raise commands.CommandError("Joining Session timed out!") from None

    @commands.command("call")
    async def call_cmd(self, ctx: commands.Context, procedure: str) -> None:
        session = await self._cmd_get_session()

        try:
            result = await session.call(procedure)
        except wamp.ApplicationError:
            raise

        await ctx.send(format_discord(result))

    @commands.command("publish")
    async def publish_cmd(self, ctx: commands.Context, topic: str) -> None:
        session = await self._cmd_get_session()

        try:
            # TODO options set acknowledge to True
            session.publish(topic)
        except wamp.ApplicationError:
            raise

        await ctx.send(f"published: {topic}")


@dataclasses.dataclass()
class ComponentConfig:
    realm: str
    transports: Union[str, List[dict]]


def create_component(config: ComponentConfig) -> Component:
    return Component(
        realm=config.realm,
        transports=config.transports,
    )


def format_discord(value: Any) -> str:
    return str(value)
