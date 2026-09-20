# Meditations Bot

A Discord bot that posts a random Meditations entry daily and on demand through **`/give-quote`**, including its summary and modern practical application. The default daily schedule is **8:00 AM America/New_York**, including daylight saving time.

## Setup

Use Python 3.12 or newer. From the project directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell instead. The `tzdata` dependency provides timezone data on systems that do not include it.

If you do not already have a `.env`, copy `.env.example` to `.env`. Fill in:

```dotenv
DISCORD_TOKEN=your_discord_bot_token
CHANNEL_ID=your_channel_id
POST_TIME=08:00
POST_TIMEZONE=America/New_York
```

Use the bot token from your application in the [Discord Developer Portal](https://discord.com/developers/applications), without a `Bot ` prefix. Enable Developer Mode in Discord, then right-click the destination channel and copy its channel ID. `POST_TIME` and `POST_TIMEZONE` are optional; deployment environment variables override `.env` values. The bot locates its `.env` and data relative to `bot.py`, even when launched from another directory.

In the application's OAuth2 URL Generator, select the **bot** and **applications.commands** scopes and **View Channels** and **Send Messages** permissions. Open the generated URL and add the bot to your server. Confirm that the destination channel's permission overrides also allow those permissions. Use a regular text or announcement channel for scheduled posts. The slash command is available in server channels where the app is permitted, not in direct messages. Administrator permission and privileged gateway intents are unnecessary.

## Verify and run

```sh
# Test without using credentials or making network requests.
python -m unittest discover -s tests -v

# Print a sample locally; does not require credentials.
python bot.py --preview

# Validate configuration and all 507 entries without connecting.
python bot.py --check

# Check authentication, server membership, and channel permissions.
# This performs read-only Discord requests and sends no messages.
python bot.py --check-discord

# Connect and start the daily schedule.
python bot.py
```

After starting or restarting the bot, type **`/give-quote`** in a server channel and select the command. It replies publicly in that channel with a fresh random selection, independently of the daily dispatch. It does not consume, postpone, or reset the 8:00 AM post. Repeated requests can randomly select the same entry.

The bot syncs the slash command with Discord once at startup. If it does not appear, allow time for Discord to update its command list and check that the app is installed with `applications.commands` and that your channel permits **Use Application Commands**. The read-only `--check-discord` command does not register commands or start the bot.

Both daily posts and command responses use exactly one message of at most 2,000 characters, including the title, date, field labels, and line breaks. The formatter reserves space for the complete summary and application, then truncates the quote with `...` if needed. All 507 current entries retain their full summaries and applications. If future annotations alone exceed the limit, those are also shortened with `...` as a fallback. Source data is unchanged, and messages are never split. All mentions are disabled. The displayed date is computed at delivery time in the posting timezone. Importing either Python module does not connect or start a task.

Keep one bot process running on a host that stays online. Pushing the files to GitHub does not host the bot, and there is no code upload to Discord: the running Python process connects using the bot token. A service manager or hosting provider should restart the process after crashes or host restarts.

The first scheduled post occurs at the next scheduled time; startup does not immediately post or backfill missed days. Gateway reconnections do not create additional scheduled tasks or resync commands. After Discord's internal retries are exhausted, daily delivery failures are logged and the next day's task remains scheduled. The same process attempts a day's scheduled entry only once; multiple instances or unusual restart timing can still produce duplicates. Random selection may repeat quotes across days.

A `403` with Discord error code `50001` means Missing Access: check that the bot has joined the intended server and can view the destination channel. A successful token check alone does not establish channel access. Voice-support warnings about PyNaCl or davey do not affect this text-only bot.

## Publishing to GitHub

`.gitignore` excludes `.env`, other local environment files, virtual environments, keys, bytecode, and logs. Commit `.env.example`, which contains placeholders. Before pushing, inspect the files staged by Git:

```sh
git status --short
git diff --cached --name-only
git ls-files .env '.env.*'
```

The final command should list at most `.env.example`. Ignore rules do not remove secrets already tracked or present in old commits. If a real token has ever been committed or shared, reset it in the Developer Portal and remove it from the repository history before publishing. Supply deployment secrets through the host's environment or a private `.env`.

The GitHub Actions workflow runs the tests and audits dependencies for known vulnerabilities on pushes and pull requests without Discord credentials or posting messages. Dependabot checks weekly for Python dependency and GitHub Actions updates.

To run the same dependency audit locally:

```sh
python -m pip install pip-audit==2.10.1
python -m pip_audit -r requirements.txt --strict --progress-spinner off
```

## Data

The quotes were pulled from jacobkap's [meditations](https://jacobkap.github.io/meditations/) repository. Summaries and practical modern applications were pre-generated by GPT-6 Astra. Original IDs and quote text are preserved in `data/meditations.json`.

The scheduling and delivery behavior follow the [discord.py task documentation](https://discordpy.readthedocs.io/en/stable/ext/tasks/index.html), [application command documentation](https://discordpy.readthedocs.io/en/stable/interactions/api.html#discord.app_commands.CommandTree.sync), and [Discord's message API](https://docs.discord.com/developers/resources/message).
