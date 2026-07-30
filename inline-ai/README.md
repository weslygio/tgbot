# inline-ai

Inline AI assistant for Telegram. Works via inline mode (`@bot_username <question>`) and commands (`/ask_fast`, `/ask_think`) in both groups and private chats. Every query is stateless — no conversation history is stored.

## Modes

### Inline mode (any chat)
Type `@bot_username <question>` in any chat, tap the result, and the answer appears inline.

1. Type `@bot_username <question>` in any Telegram chat
2. Choose **Answer quickly** (1 tool turn) or **Think before answer** (3 tool turns)
3. A "Thinking..." placeholder appears
4. Tap the result to send it — the answer replaces the placeholder
5. Long answers are split into pages with Previous/Next navigation

### Commands (groups and private chats)

- `/ask_fast <question>` — quick answer (1 tool turn), with optional media
- `/ask_think <question>` — thought-out answer (3 tool turns), with optional media

### Reply context
When your message **replies to another message**, the AI sees that message's content (text, caption, and media) as background context. Only the immediate replied-to message is available — Telegram does not support walking the reply chain or retrieving thread history.

## Input modalities

Configure which media types the bot sends to the AI via `OPENROUTER_MODEL_INPUT_MODALITY` (comma-separated):

| Value   | Accepted Telegram message types                        | Sent to AI as                                     |
| ------- | ------------------------------------------------------ | ------------------------------------------------- |
| `text`  | Text messages, captions                                | Plain text                                        |
| `image` | Photos, image documents, stickers, animations (GIF)    | `image_url` with base64 data                      |
| `file`  | Documents (PDF, DOCX, XLSX, TXT, etc.)                 | `input_file` with base64 data                     |
| `audio` | Audio files, voice messages                            | `input_audio` with base64 data                    |
| `video` | Video files, video notes, video documents              | `video_url` with base64 data                      |

Default: `text,image,file`. Files larger than 18 MB are skipped. The underlying AI model must support the given modality.

## How it works

- All queries are stateless — no conversation history is stored or retrieved
- The AI has access to tools: web search, calculation, and string operations
- Media from the current message and the immediate replied-to message is sent to the AI
- All user content is sanitized before being sent to the AI

## Configuration

All settings go in a `.env` file (copy from `.env.example`). See `config.py` for the full list of variables.
