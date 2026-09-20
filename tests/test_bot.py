import contextlib
from datetime import datetime, time
import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import aiohttp
import discord

import bot
from quote_service import QuoteService


def settings():
    return bot.Settings("test-only-token", 123, time(8, tzinfo=ZoneInfo("America/New_York")))


class SettingsTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"DISCORD_TOKEN": "test-only-token", "CHANNEL_ID": "123"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        dotenv = patch("bot.load_dotenv")
        self.load_dotenv = dotenv.start()
        self.addCleanup(dotenv.stop)

    def test_defaults_and_secret_repr(self):
        result = bot.Settings.from_env()
        self.assertEqual(result.channel_id, 123)
        self.assertEqual(result.post_time.hour, 8)
        self.assertEqual(str(result.post_time.tzinfo), "America/New_York")
        self.assertNotIn(result.token, repr(result))
        self.load_dotenv.assert_called_once_with(bot.BASE_DIR / ".env", override=False)

    def test_custom_schedule(self):
        os.environ.update(POST_TIME="17:45", POST_TIMEZONE="America/Chicago")
        result = bot.Settings.from_env()
        self.assertEqual((result.post_time.hour, result.post_time.minute), (17, 45))
        self.assertEqual(str(result.post_time.tzinfo), "America/Chicago")

    def test_invalid_configuration(self):
        cases = {
            "DISCORD_TOKEN": ("", " ", "your_discord_bot_token", "replace_me"),
            "CHANNEL_ID": ("", "0", "-1", "1.5", "abc", str(2**64)),
            "POST_TIME": ("8:00", "24:00", "12:60", "12:00:00"),
            "POST_TIMEZONE": ("", "invalid/timezone", "/etc/passwd", "../private"),
        }
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                    with self.assertRaises(ValueError) as error:
                        bot.Settings.from_env()
                    self.assertNotIn("test-only-token", str(error.exception))


class FormattingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 19, 8, tzinfo=ZoneInfo("America/New_York"))

    def test_every_bundled_quote_fits_and_preserves_annotations(self):
        quotes = QuoteService(bot.QUOTES_FILE)
        for quote in quotes.quotes:
            for daily in (True, False):
                with self.subTest(id=quote["id"], daily=daily):
                    message = bot.format_quote(quote, self.now, daily=daily)
                    self.assertLessEqual(bot.message_length(message), 2000)
                    self.assertIn("Date: 19/09/2026", message)
                    self.assertIn(quote["summary"], message)
                    self.assertIn(quote["modern_practical_application"], message)
                    self.assertTrue(message.startswith("**Daily Meditations Quote**" if daily else "**Meditations Quote**"))

    def test_oversized_annotations_and_emoji_fit(self):
        quote = {key: "😀" * 3000 for key in ("quote", "summary", "modern_practical_application")}
        message = bot.format_quote(quote, self.now)
        self.assertLessEqual(bot.message_length(message), 2000)
        self.assertIn("**Practical Application**", message)
        self.assertNotIn("\ufffd", message)
        self.assertTrue(message.endswith("..."))

    def test_truncation_boundary(self):
        self.assertEqual(bot.truncate_text("😀abc", 5), "😀abc")
        self.assertEqual(bot.truncate_text("😀abcd", 5), "😀...")
        self.assertEqual(bot.truncate_text("😀abcd", 4), "...")
        with self.assertRaises(ValueError):
            bot.truncate_text("long", 2)


class DiscordTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.quotes = QuoteService(bot.QUOTES_FILE)
        self.client = bot.MeditationsBot(settings(), self.quotes)
        self.addAsyncCleanup(self.client.close)

    async def test_minimal_intents_and_no_mentions(self):
        self.assertEqual(self.client.intents.value, discord.Intents(guilds=True).value)
        self.assertEqual(self.client.allowed_mentions.to_dict(), {"parse": []})
        self.assertTrue(self.client.tree.get_command("give-quote").guild_only)

    async def test_slash_reply_does_not_change_daily_state(self):
        interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
        await self.client.give_quote(interaction)
        call = interaction.response.send_message.call_args
        self.assertTrue(call.args[0].startswith("**Meditations Quote**"))
        self.assertEqual(call.kwargs["allowed_mentions"].to_dict(), {"parse": []})
        self.assertIsNone(self.client._last_attempt_date)

    async def test_startup_checks_access_before_sync_and_schedule(self):
        with patch("bot.resolve_channel", new_callable=AsyncMock, side_effect=ValueError("no access")), \
                patch.object(self.client.tree, "sync", new_callable=AsyncMock) as sync, \
                patch.object(self.client.post_quote, "start") as start:
            with self.assertRaises(ValueError):
                await self.client.setup_hook()
            sync.assert_not_awaited()
            start.assert_not_called()

    async def test_successful_startup_and_reconnect(self):
        with patch("bot.resolve_channel", new_callable=AsyncMock), \
                patch.object(self.client.tree, "sync", new_callable=AsyncMock) as sync, \
                patch.object(self.client.post_quote, "start") as start:
            await self.client.setup_hook()
            await self.client.on_ready()
            await self.client.on_ready()
            sync.assert_awaited_once()
            start.assert_called_once()

    async def test_daily_post_only_attempts_once_and_disables_mentions(self):
        channel = SimpleNamespace(send=AsyncMock())
        with patch.object(self.client, "wait_until_ready", new_callable=AsyncMock), \
                patch("bot.resolve_channel", new_callable=AsyncMock, return_value=channel):
            await self.client.post_quote()
            await self.client.post_quote()
        channel.send.assert_awaited_once()
        call = channel.send.call_args
        self.assertTrue(call.args[0].startswith("**Daily Meditations Quote**"))
        self.assertEqual(call.kwargs["allowed_mentions"].to_dict(), {"parse": []})

    async def test_delivery_failure_is_safe_and_tomorrow_can_run(self):
        now = datetime(2026, 9, 19, 8, tzinfo=settings().post_time.tzinfo)
        tomorrow = now.replace(day=20)
        channel = SimpleNamespace(send=AsyncMock(side_effect=aiohttp.ClientError("sensitive-response")))
        with patch.object(self.client, "wait_until_ready", new_callable=AsyncMock), \
                patch("bot.resolve_channel", new_callable=AsyncMock, return_value=channel), \
                patch("bot.datetime") as clock, self.assertLogs("bot", level="ERROR") as logs:
            clock.now.side_effect = [now, now, tomorrow]
            await self.client.post_quote()
            await self.client.post_quote()
            await self.client.post_quote()
        self.assertEqual(channel.send.await_count, 2)
        self.assertNotIn("sensitive-response", "\n".join(logs.output))

    async def test_read_only_check_does_not_start_production_bot(self):
        client = AsyncMock()
        client.__aenter__.return_value = client
        with patch("bot.discord.Client", return_value=client), \
                patch("bot.MeditationsBot") as production, \
                patch("bot.resolve_channel", new_callable=AsyncMock) as resolve:
            await bot.check_discord(settings())
        client.login.assert_awaited_once_with("test-only-token")
        resolve.assert_awaited_once_with(client, 123)
        production.assert_not_called()

    async def test_resolve_channel_fetches_fresh_permissions(self):
        client = SimpleNamespace(user=SimpleNamespace(id=456), fetch_channel=AsyncMock(), fetch_guild=AsyncMock())
        channel = MagicMock(spec=discord.TextChannel)
        channel.guild = SimpleNamespace(id=789)
        client.fetch_channel.return_value = channel
        guild = SimpleNamespace(fetch_channel=AsyncMock(return_value=channel), fetch_member=AsyncMock())
        client.fetch_guild.return_value = guild
        for view, send in ((True, True), (False, True), (True, False)):
            with self.subTest(view=view, send=send):
                channel.permissions_for.return_value = SimpleNamespace(view_channel=view, send_messages=send)
                if view and send:
                    self.assertIs(await bot.resolve_channel(client, 123), channel)
                else:
                    with self.assertRaises(ValueError):
                        await bot.resolve_channel(client, 123)
        guild.fetch_member.assert_awaited_with(456)
        client.fetch_guild.assert_awaited_with(789)
        guild.fetch_channel.assert_awaited_with(123)
        client.fetch_channel.return_value = SimpleNamespace()
        with self.assertRaisesRegex(ValueError, "server text"):
            await bot.resolve_channel(client, 123)


class OfflineCommandTests(unittest.TestCase):
    def test_preview_and_check_do_not_connect(self):
        for mode in ("--preview", "--check"):
            with self.subTest(mode=mode), patch("sys.argv", ["bot.py", mode]), \
                    patch("bot.Settings.from_env", return_value=settings()) as config, \
                    patch("bot.discord.Client.login", side_effect=AssertionError("Network access")), \
                    patch("bot.MeditationsBot.run", side_effect=AssertionError("Bot started")), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(bot.main(), 0)
                self.assertTrue(output.getvalue())
                if mode == "--preview":
                    config.assert_not_called()


if __name__ == "__main__":
    unittest.main()
