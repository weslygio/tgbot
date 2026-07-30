# inline-ai

Inline AI assistant for Telegram. Works via inline mode (`@bot_username <question>`), direct messages, and commands (`/ask_fast`, `/ask_think`). Supports reply/thread context — when you reply to a message or ask in a thread, the AI sees the surrounding context.

## Modes

### Inline mode (any chat)
Type `@bot_username <question>` in any chat, tap the result, and the answer appears inline.

1. Type `@bot_username <question>` in any Telegram chat
2. Choose **Answer quickly** (1 tool turn) or **Think before answer** (3 tool turns)
3. A "Thinking..." placeholder appears
4. Tap the result to send it — the answer replaces the placeholder
5. Long answers are split into pages with Previous/Next navigation

### Direct message (private chat)
Send a message directly to the bot. The bot remembers conversation history per user. Sessions auto-reset after 8 hours of inactivity.

Only these message types trigger a response:
- Plain text
- Photo + caption
- Video + caption
- Audio + caption

All trigger **deep** mode (3 tool turns).
- `/refresh_session` → clear history and start fresh

Long answers are delivered as multiple messages instead of paginated pages.

### Group commands
Add the bot to a group and use commands:

- `/ask_fast <question>` — quick answer
- `/ask_think <question>` — thought-out answer

### Reply context
When using any mode, if your message **replies to another message**, the AI sees that message's content as background context. This works for text, photos with captions, documents, videos, and other media.

### Thread context
Messages sent inside a Telegram forum topic (thread) include the thread ID as context, helping the AI understand the conversation scope.

## Input modalities

The bot can receive and send different media types to the AI when the model supports it. Configure via `OPENROUTER_MODEL_INPUT_MODALITY` (comma-separated):

| Value   | Accepted Telegram message types                        | Sent to AI as                                     |
| ------- | ------------------------------------------------------ | ------------------------------------------------- |
| `text`  | Text messages, captions                                | Plain text                                        |
| `image` | Photos, image documents, stickers, animations (GIF)    | `image_url` with base64 data                      |
| `file`  | Documents (PDF, DOCX, XLSX, TXT, etc.)                 | `input_file` with base64 data                     |
| `audio` | Audio files, voice messages                            | `input_audio` with base64 data                    |
| `video` | Video files, video notes, video documents              | `video_url` with base64 data — natively supported by OpenRouter. |

Default: `text,image,file`. Files larger than 18 MB are skipped. The underlying AI model must support the given modality.

## How it works

- Inline queries are stateless — every query is a single-turn interaction
- Direct messages maintain a conversation session per user (auto-resets after 8h)
- The AI has access to tools: web search, calculation, and string operations
- All user content is sanitized before being sent to the AI

## Configuration

All settings go in a `.env` file (copy from `.env.example`). See `config.py` for the full list of variables.
