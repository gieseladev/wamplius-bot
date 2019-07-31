# WAMPlius

A Discord bot version of [WAMPli](https://github.com/gieseladev/wampli).
Of course unless you actually know about WAMPli this does't really help,
does it? So what exactly is it?

WAMPlius is a [Discord](https://discordapp.com/) bot which makes it
possible to interact with the [WAMP protocol](https://wamp-proto.org/)
from the comfort of your Discord server. You can call procedures,
publish events to a topic, and subscribe to topics.

[TOC]: # "Contents"

# Contents
- [Running](#running)
    - [Using Docker](#using-docker)
    - [Manually](#manually)
    - [Command-line interface](#command-line-interface)
    - [Configuration](#configuration)
- [Commands](#commands)
    - [WAMP](#wamp)
    - [General](#general)




## Running

There are two ways you can run the bot yourself. In both cases you need
a bot token which you can get by creating an
[Application](https://discordapp.com/developers/applications/) and then
enabling the bot.

Once you have a token you can then start the bot:

### Using Docker

The recommended way to run the bot is using
[Docker](https://www.docker.com/). You can use the image
[`gieseladev/wamplius`](https://hub.docker.com/r/gieseladev/wamplius).

```console
$ docker run --env BOT_DISCORDTOKEN="<your token>" gieseladev/wamplius
```

Arguments are passed to the CLI (see
[Command-line interface](#command-line-interface)).

Unless the "config" argument is provided, the bot tries to read
`/config.toml` as its configuration file. If you want to configure the
bot using a config file instead of environment variable you can mount it
there.

For more information regarding configuration see
[Configuration](#configuration).


### Manually

Instead of using Docker you can of course run the bot manually. The bot
is written for [python 3.7](https://www.python.org/) and as such you
need to have it installed to run it.

You also need to install the dependencies which can be done using
[Pipenv](https://docs.pipenv.org/). Unless you already have it
installed, run the following command to install it:

```console
$ python -m pip install -U pipenv
```

To then install the dependencies run:

```console
$ pipenv install
```

Once installed you can start the bot using:

```console
python wamplius
```

See the next section for instructions on how to use the command-line
interface.


### Command-line interface

```console
usage: wamplius [-h] [-c CONFIG]

optional arguments:
  -h, --help            show this help message and exit
  -c CONFIG, --config CONFIG
                        specify config file
```

Running the command starts the bot and waits until the bot is shutdown.
You can pass the optional `config` argument to specify the location of
the config file.

By default the bot reads the config from "config.toml" in the current
working directory if the file exists. Note that you don't have to use a
configuration file. Refer to the [Configuration](#configuration) section
for more details.

### Configuration

The bot uses [konfi](https://github.com/gieseladev/konfi) for
configuration, specifically the
[FileLoader](https://konfi.giesela.dev/en/latest/api.html#konfi.FileLoader)
and [Env](https://konfi.giesela.dev/en/latest/api.html#konfi.Env)
sources. If you're not familiar with konfi you can safely ignore what
you just read.

The bot can be configured using a file config ([YAML](https://yaml.org/)
or [TOML](https://github.com/toml-lang/toml)) or using environment
variables. Of course these methods aren't exclusive, just know that an
environment variable takes precedence over and thus overwrites the same
value specified in a config file.

So what values can and do you actually have to configure?

#### Required values

| Key             | Environment        | Description                                  |
|:----------------|:-------------------|:---------------------------------------------|
| `discord_token` | `BOT_DISCORDTOKEN` | Discord bot token of your Application's bot. |

#### Optional values

| Key              | Environment         | Description                                          | Default |
|:-----------------|:--------------------|:-----------------------------------------------------|:--------|
| `command_prefix` | `BOT_COMMANDPREFIX` | Specify the command prefix the bot should listen to. | ">"     |


## Commands

The following is a list of commands supported by the bot.

### WAMP

Commands provided by the WAMPlius cog.

| Command                     | Description                                                                                                                       |
|:----------------------------|:----------------------------------------------------------------------------------------------------------------------------------|
| `status`                    | Display the status of the connection for the context (guild or DM).                                                               |
| `connect [<url> <realm>]`   | Connect to a WAMP router. The url and realm arguments can be omitted if WAMPlius has a connection configured for the context.     |
| `disconnect`                | Disconnect from the WAMP router. The previous configuration is preserved, so calling `connect` without arguments will re-connect. |
| `call <procedure> [arg]...` | Call a procedure.                                                                                                                 |
| `publish <topic> [arg]...`  | Publish an event to a topic.                                                                                                      |
| `subscriptions`             | Display all subscribed topics and their channel.                                                                                  |
| `subscribe <topic>...`      | Subscribe to one or more topics. Events from the topics will be displayed in the channel the command is executed in.              |
| `unsubscribe <topic>...`    | Unsubscribe from one or more topics.                                                                                              |


### General

| Command          | Description                                                                                     |
|:-----------------|:------------------------------------------------------------------------------------------------|
| `help [command]` | Display usage and description of the command. If no command is given, display help for the bot. |
| `shutdown`       | Perform a clean shutdown.                                                                       |
