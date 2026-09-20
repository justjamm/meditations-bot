"""Share Meditations daily and through /give-quote; imports never connect to Discord."""

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, time
import logging
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

from quote_service import QuoteService


BASE_DIR = Path(__file__).resolve().parent
QUOTES_FILE = BASE_DIR / "data" / "meditations.json"
LOGGER = logging.getLogger(__name__)
MESSAGE_LIMIT = 2000


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    channel_id: int
    post_time: time

    @classmethod
    def from_env(cls):
        # Explicit deployment environment variables take precedence over .env.
        load_dotenv(BASE_DIR / ".env", override=False)
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token or token.lower() in {"your_discord_bot_token", "replace_me"}:
            raise ValueError("Set DISCORD_TOKEN to your bot token in .env or the environment.")
        raw_channel = os.getenv("CHANNEL_ID", "").strip()
        if not re.fullmatch(r"[0-9]{1,20}", raw_channel):
            raise ValueError("Set CHANNEL_ID to a positive numeric Discord channel ID.")
        channel_id = int(raw_channel)
        if not 0 < channel_id < 2**64:
            raise ValueError("CHANNEL_ID must be a positive Discord snowflake ID.")
        raw_time = os.getenv("POST_TIME", "08:00").strip()
        if not re.fullmatch(r"[0-9]{2}:[0-9]{2}", raw_time):
            raise ValueError("POST_TIME must use 24-hour HH:MM format.")
        try:
            post_time = time.fromisoformat(raw_time)
        except ValueError:
            raise ValueError("POST_TIME must be a valid 24-hour time.") from None
        try:
            timezone = ZoneInfo(os.getenv("POST_TIMEZONE", "America/New_York").strip())
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("POST_TIMEZONE must be a valid IANA timezone, such as America/New_York.") from None
        return cls(token, channel_id, post_time.replace(tzinfo=timezone))


def message_length(text: str) -> int:
    """Count UTF-16 units conservatively, including emoji as two units."""
    return len(text.encode("utf-16-le")) // 2


def truncate_text(text: str, limit: int) -> str:
    """Append '...' only when shortening; never cut a Unicode character in half."""
    if message_length(text) <= limit:
        return text
    if limit < 3:
        raise ValueError("Truncation needs room for '...'.")
    prefix = text.encode("utf-16-le")[: (limit - 3) * 2].decode("utf-16-le", errors="ignore")
    return prefix.rstrip() + "..."


def format_quote(quote: dict, now: datetime, *, daily: bool = True) -> str:
    """Fit an entry into one message, shortening the quote before its annotations."""
    title = "Daily Meditations Quote" if daily else "Meditations Quote"
    header = f"**{title}**\nDate: {now:%d/%m/%Y}\n\n"
    labels = ("**Quote**: \n", "**Summary**: \n", "**Practical Application**: \n")
    values = [quote[key] for key in ("quote", "summary", "modern_practical_application")]
    available = MESSAGE_LIMIT - message_length(header + "\n".join(labels))
    budgets = [message_length(value) for value in values]
    excess = max(0, sum(budgets) - available)
    # Reserve the full date, labels, summary, and application first. If future
    # annotations alone exceed the limit, shorten those too instead of splitting.
    for index, budget in enumerate(budgets):
        reduction = min(excess, max(0, budget - 3))
        budgets[index] -= reduction
        excess -= reduction
    return header + "\n".join(
        label + truncate_text(value, budget)
        for label, value, budget in zip(labels, values, budgets)
    )


async def resolve_channel(client: discord.Client, channel_id: int) -> discord.TextChannel:
    """Fetch fresh role/overwrite data, including when the gateway cache is empty."""
    channel = await client.fetch_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("CHANNEL_ID must identify a server text or announcement channel.")
    guild = await client.fetch_guild(channel.guild.id)
    channel = await guild.fetch_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("CHANNEL_ID must identify a server text or announcement channel.")
    member = await guild.fetch_member(client.user.id)
    permissions = channel.permissions_for(member)
    if not (permissions.view_channel and permissions.send_messages):
        raise ValueError("The bot needs View Channel and Send Messages in the target channel.")
    return channel


async def check_discord(settings: Settings) -> None:
    # Use a plain client: login must not start the production scheduler.
    async with discord.Client(intents=discord.Intents(guilds=True)) as client:
        await client.login(settings.token)
        LOGGER.info("Discord authentication succeeded; checking target channel access.")
        await resolve_channel(client, settings.channel_id)


class MeditationsBot(discord.Client):
    def __init__(self, settings: Settings, quotes: QuoteService):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.quotes = quotes
        self.tree = app_commands.CommandTree(self)
        self.tree.command(name="give-quote", description="Get a random Meditations quote with its summary and application.")(
            self.give_quote
        )
        self._last_attempt_date = None
        self.post_quote.change_interval(time=settings.post_time)

    async def setup_hook(self):
        # Fail before scheduling if the bot cannot post to the configured channel.
        await resolve_channel(self, self.settings.channel_id)
        await self.tree.sync()
        self.post_quote.start()

    @app_commands.guild_only()
    async def give_quote(self, interaction: discord.Interaction):
        """Reply in the invoking channel without changing the daily dispatch state."""
        now = datetime.now(self.settings.post_time.tzinfo)
        content = format_quote(self.quotes.get_random_quote(), now, daily=False)
        await interaction.response.send_message(content, allowed_mentions=discord.AllowedMentions.none())

    async def on_ready(self):
        LOGGER.info("Connected. Daily posting is scheduled for %s (%s).",
                    self.settings.post_time.strftime("%H:%M"), self.settings.post_time.tzinfo)

    @tasks.loop(time=time(hour=8, tzinfo=ZoneInfo("America/New_York")))
    async def post_quote(self):
        await self.wait_until_ready()
        now = datetime.now(self.settings.post_time.tzinfo)
        if self._last_attempt_date == now.date():
            return
        self._last_attempt_date = now.date()
        quote = self.quotes.get_random_quote()
        content = format_quote(quote, now)
        try:
            channel = await resolve_channel(self, self.settings.channel_id)
            await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
        except (discord.HTTPException, aiohttp.ClientError, OSError, ValueError) as error:
            # discord.py handles rate limits/retries internally; a final failure
            # must not kill tomorrow's job.
            LOGGER.error("Daily delivery failed (%s). Check channel access and connectivity. "
                         "The next daily run remains scheduled.", type(error).__name__)
        else:
            LOGGER.info("Posted quote %s.", quote["id"])

    @post_quote.before_loop
    async def before_post_quote(self):
        await self.wait_until_ready()

    async def close(self):
        self.post_quote.cancel()
        await super().close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="Validate local configuration and all quotes without connecting.")
    modes.add_argument("--check-discord", action="store_true", help="Also verify login and channel permissions without sending messages.")
    modes.add_argument("--preview", action="store_true", help="Print a sample entry locally without credentials or a connection.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        quotes = QuoteService(QUOTES_FILE)
        if args.preview:
            now = datetime.now(ZoneInfo("America/New_York"))
            print(format_quote(quotes.get_random_quote(), now))
            return 0
        settings = Settings.from_env()
        if args.check or args.check_discord:
            now = datetime.now(settings.post_time.tzinfo)
            messages = [format_quote(quote, now) for quote in quotes.quotes]
            print(f"Local checks passed: {len(messages)} quotes; one message per entry, "
                  f"at most {max(map(message_length, messages))} characters.")
            print(f"Schedule: {settings.post_time:%H:%M} ({settings.post_time.tzinfo}).")
            if args.check_discord:
                asyncio.run(check_discord(settings))
                print("Discord login and channel permissions verified. No messages sent.")
        else:
            MeditationsBot(settings, quotes).run(settings.token, log_handler=None)
    except discord.LoginFailure:
        LOGGER.error("Discord rejected DISCORD_TOKEN. Set a valid bot token.")
        return 1
    except (discord.Forbidden, discord.NotFound):
        LOGGER.error("Discord denied access or could not find the channel/server. Check CHANNEL_ID and the bot's access.")
        return 1
    except discord.HTTPException as error:
        LOGGER.error("Discord request failed (HTTP %s).", error.status)
        return 1
    except (aiohttp.ClientError, OSError):
        LOGGER.error("Unable to connect to Discord or access required local files. Check connectivity and file access.")
        return 1
    except ValueError as error:
        LOGGER.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
