# Telegram Bot Setup

This guide walks through setting up stash to capture and search your thoughts via Telegram.

## Prerequisites

You'll need:
- A Telegram account
- A local environment variable file (`.env`) in your stash repo directory

## Step 1: Create a Telegram bot

1. Open Telegram and search for `@BotFather`.
2. Send `/start` to begin.
3. Send `/newbot` to create a new bot.
4. Choose a name for your bot (e.g. "My Stash Bot").
5. Choose a username for your bot (must be unique and end with "bot", e.g. "mystash_bot").
6. BotFather will reply with a token that looks like: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`
7. Copy this token. You'll use it in the next step.

## Step 2: Configure your environment

Create or edit `.env` in your stash repository root:

```bash
STASH_TELEGRAM_BOT_TOKEN=<your_token_here>
STASH_ALLOWED_SENDER_IDS=<your_user_id>
```

Replace `<your_token_here>` with the token from Step 1.

## Step 3: Get your Telegram user ID

1. Open Telegram and search for `@userinfobot`.
2. Send any message to this bot.
3. It will reply with your user information, including your numeric user ID (a number like `123456789`).
4. Copy your user ID and add it to your `.env` file as shown above.

If you want multiple people to send notes through this bot, separate their user IDs with commas:

```bash
STASH_ALLOWED_SENDER_IDS=123456789,987654321,555666777
```

## Step 4: Run stash

Start the stash server:

```bash
python -m stash serve
```

The bot will now respond to messages from your Telegram account. It listens for long-poll updates from Telegram's servers. Keep the server running while you want to use the bot. You can run it in a terminal, under tmux, or under systemd for persistent operation.

### Running under systemd (optional)

Create a systemd service file at `/etc/systemd/system/stash-telegram.service`:

```ini
[Unit]
Description=Stash Telegram Bot
After=network.target

[Service]
Type=simple
User=<your_username>
WorkingDirectory=/path/to/your/stash/repo
Environment="PATH=/path/to/venv/bin"
ExecStart=/path/to/venv/bin/python -m stash serve
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then enable and start the service:

```bash
sudo systemctl enable stash-telegram
sudo systemctl start stash-telegram
```

To view logs: `sudo journalctl -u stash-telegram -f`

## Security and Privacy Notes

The bot only responds to private direct messages from allow-listed users. Group messages are ignored. If the token or allow-list is unset, the bot will fail fast with a clear error message.

Text notes pass through Telegram's servers for delivery. Stash stores the note text locally in your SQLite database. Processing and storage always happen on your machine.

## Graceful Shutdown

When you stop the bot with Ctrl+C or send it a SIGTERM signal, it gracefully finishes the current long-poll cycle before exiting. This can take up to about `max(25s long-poll timeout, STASH_SCHEDULER_TICK_SECONDS)`. If you run the bot under a process manager (systemd, supervisor, etc.), set a generous stop timeout so the graceful shutdown completes:

```ini
TimeoutStopSec=60
```

## Using the Bot

Once the server is running, open Telegram and start a private chat with your bot using the username you created (e.g. search for `@mystash_bot`).

### Capturing a thought

Send any text message to the bot. It will capture the text as a note and reply with a receipt.

### Finding notes

Send `/find <query>` to search your notes. The bot will return matching results ranked by relevance.

```
/find python tips
/find meetings with alice
```

### Recent notes

Send `/recent` to see your most recent notes.

### Help

Send `/help` to see the list of available commands.
