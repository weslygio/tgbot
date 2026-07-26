# inline-ai

The bot source code. Works entirely through Telegram's inline mode — no need to open a chat with the bot. Just type `@bot_username <question>` in any conversation, tap the result, and the answer appears inline.

## How it works

1. You type `@bot_username what is the capital of France?` in any Telegram chat
2. A "Thinking..." placeholder appears
3. The bot calls the AI API in the background
4. You tap the result to send it — the answer replaces the placeholder
5. If the answer is long, a "Continue" button lets you read more

## Configuration

All settings go in a `.env` file (copy from `.env.example`). See `config.py` for the full list of variables.
