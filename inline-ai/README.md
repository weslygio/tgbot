# inline-ai

Inline AI assistant for Telegram. Works entirely through inline mode — no need to open a chat with the bot. Just type `@bot_username <question>` in any conversation, tap the result, and the answer appears inline.

## How it works

1. Type `@bot_username <question>` in any Telegram chat
2. A "Thinking..." placeholder appears
3. The bot calls the AI API in the background
4. Tap the result to send it — the answer replaces the placeholder
5. Long answers are split into pages with Previous/Next navigation

## Configuration

All settings go in a `.env` file (copy from `.env.example`). See `config.py` for the full list of variables.
