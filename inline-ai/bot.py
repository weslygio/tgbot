from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
from datetime import datetime
from html.parser import HTMLParser
import html
from pathlib import Path
import time

import aiohttp
from aiohttp import web
from exa_py import AsyncExa
from telegram import (
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultsButton,
    InputTextMessageContent,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChosenInlineResultHandler,
    CommandHandler,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from config import (
    ALLOWED_GROUP_IDS,
    ALLOWED_USER_IDS,
    BOT_NAME,
    BOT_USERNAME,
    DEVELOPER,
    EXA_API_KEY,
    FALLBACK_INPUT_MODALITIES,
    INPUT_MODALITIES,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_FALLBACK_MODEL_ID,
    OPENROUTER_MODEL_ID,
    PORT,
    REASONING_EFFORT,
    TELEGRAM_BOT_TOKEN,
    WEBHOOK_URL,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

exa = AsyncExa(api_key=EXA_API_KEY) if EXA_API_KEY else None

ALLOWED_TAGS = {
    "b", "strong", "i", "em", "u", "s", "strike", "del",
    "code", "pre", "a", "span",
}


class _Sanitizer(HTMLParser):
    def __init__(self, allowed_tags: set):
        super().__init__(convert_charrefs=False)
        self._allowed = allowed_tags
        self._result = []
        self._tag_stack = []

    def handle_starttag(self, tag, attrs):
        if tag not in self._allowed:
            return
        if tag == "span":
            classes = dict(attrs).get("class", "")
            if "tg-spoiler" not in classes:
                return
        parts = [tag]
        for name, val in attrs:
            if val:
                parts.append(f'{name}="{html.escape(val, quote=True)}"')
            else:
                parts.append(name)
        self._result.append("<" + " ".join(parts) + ">")
        self._tag_stack.append(tag)

    def handle_endtag(self, tag):
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._result.append(f"</{tag}>")
            self._tag_stack.pop()

    def handle_data(self, data):
        self._result.append(html.escape(data))

    def handle_entityref(self, name):
        self._result.append(f"&{name};")

    def handle_charref(self, name):
        self._result.append(f"&#{name};")

    def close(self):
        for tag in reversed(self._tag_stack):
            self._result.append(f"</{tag}>")
        super().close()

    def get_result(self) -> str:
        return "".join(self._result)


def sanitize_html(text: str) -> str:
    text = re.sub(
        r'<[｜\|]\s*[dD][sS][mM][lL]\s*[｜\|][^>]*>', '', text
    )
    text = re.sub(
        r'</[｜\|]\s*[dD][sS][mM][lL]\s*[｜\|][^>]*>', '', text
    )
    text = re.sub(
        r'[｜\|]+\s*[dD][sS][mM][lL]\s*[｜\|]+', '', text
    )

    parser = _Sanitizer(ALLOWED_TAGS)
    parser.feed(text)
    parser.close()
    return parser.get_result()

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current or recent information. Use when you need up-to-date facts, news, or anything you're uncertain about.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query"
                }
            },
            "required": ["query"]
        }
    }
}

CALCULATE_TOOL = {
    "type": "function",
    "function": {
        "name": "calculate",
        "description": "Evaluate a mathematical expression. Supports +, -, *, /, **, %, //, parentheses, integers, floats, and built-in functions: abs, round, min, max, pow, sum, int, float.",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The mathematical expression to evaluate (e.g. '2 + 2 * 3', '(15 + 5) / 4', 'abs(-10)')"
                }
            },
            "required": ["expression"]
        }
    }
}

STRING_TOOL = {
    "type": "function",
    "function": {
        "name": "run_string_operation",
        "description": "Perform a string operation. Operations: length, upper, lower, title, capitalize, strip, replace, split, join, find, count, reverse, slice, concat.",
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["length", "upper", "lower", "title", "capitalize", "strip", "replace", "split", "join", "find", "count", "reverse", "slice", "concat"],
                    "description": "The string operation to perform"
                },
                "text": {
                    "type": "string",
                    "description": "The input string to operate on"
                },
                "arg1": {
                    "type": "string",
                    "description": "Optional first argument: substring to find, separator, old text (for replace), start index (for slice), or string to concat"
                },
                "arg2": {
                    "type": "string",
                    "description": "Optional second argument: new text (for replace) or end index (for slice)"
                }
            },
            "required": ["operation", "text"]
        }
    }
}

import ast
import operator as op_mod


def safe_eval(expression: str) -> str:
    allowed_ops = {
        ast.Add: op_mod.add, ast.Sub: op_mod.sub, ast.Mult: op_mod.mul,
        ast.Div: op_mod.truediv, ast.Pow: op_mod.pow, ast.Mod: op_mod.mod,
        ast.USub: op_mod.neg, ast.UAdd: op_mod.pos, ast.FloorDiv: op_mod.floordiv,
    }
    allowed_funcs = {
        "abs": abs, "round": round, "min": min, "max": max,
        "pow": pow, "sum": sum, "int": int, "float": float,
    }

    tree = ast.parse(expression, mode="eval")

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.BinOp):
            if type(node.op) not in allowed_ops:
                raise ValueError("Operator not allowed")
            return allowed_ops[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            if type(node.op) not in allowed_ops:
                raise ValueError("Operator not allowed")
            return allowed_ops[type(node.op)](_eval(node.operand))
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("Unsupported constant")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in allowed_funcs:
                raise ValueError("Function not allowed")
            return allowed_funcs[node.func.id](*[_eval(a) for a in node.args])
        if isinstance(node, ast.List):
            return [_eval(el) for el in node.elts]
        raise ValueError("Expression type not supported")

    result = _eval(tree)
    if isinstance(result, float):
        return f"{result:.10g}"
    return str(result)


def run_string_operation(operation: str, text: str, arg1: str = "", arg2: str = "") -> str:
    if operation == "length":
        return str(len(text))
    if operation == "upper":
        return text.upper()
    if operation == "lower":
        return text.lower()
    if operation == "title":
        return text.title()
    if operation == "capitalize":
        return text.capitalize()
    if operation == "strip":
        return text.strip()
    if operation == "replace":
        return text.replace(arg1, arg2)
    if operation == "split":
        return str(text.split(arg1) if arg1 else text.split())
    if operation == "join":
        sep = arg1 or " "
        items = [x.strip() for x in text.split(",") if x.strip()]
        return sep.join(items)
    if operation == "find":
        return str(text.find(arg1))
    if operation == "count":
        return str(text.count(arg1))
    if operation == "reverse":
        return text[::-1]
    if operation == "slice":
        try:
            start = int(arg1) if arg1 else 0
            end = int(arg2) if arg2 else len(text)
            return text[start:end]
        except ValueError:
            return "Error: slice indices must be integers"
    if operation == "concat":
        return text + arg1
    return f"Error: unknown operation '{operation}'"


TOOLS_LIST = [WEB_SEARCH_TOOL, CALCULATE_TOOL, STRING_TOOL]

CHUNK_SIZE = 3800
PAGE_INDICATOR_OVERHEAD = 20
PENDING_TTL = 120
SESSION_TTL = 8 * 60 * 60

pending_answers = {}
long_responses = {}
sessions: dict[int, dict] = {}


def find_good_break(text, target, lookback=200):
    if len(text) <= target:
        return len(text)

    search_end = min(target + lookback, len(text))
    lower_bound = max(0, target - lookback)

    for sep, offset in [('\n\n', 0), ('\n', 1), ('. ', 2), ('! ', 2), ('? ', 2)]:
        idx = text.rfind(sep, 0, search_end)
        if idx >= lower_bound:
            return idx + offset

    idx = text.rfind(' ', 0, search_end)
    if idx >= lower_bound:
        return idx + 1

    return target


def split_answer(text: str, max_len: int) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        cut = find_good_break(remaining, max_len)
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return chunks


def extract_reply_context(message) -> dict | None:
    if not message.reply_to_message:
        return None
    reply = message.reply_to_message
    sender = reply.from_user
    sender_name = sender.full_name if sender else "Unknown"
    sender_username = f"@{sender.username}" if sender and sender.username else "\u2014"

    text = reply.text or reply.caption or ""
    media_type = None
    if reply.photo:
        media_type = "a photo"
    elif reply.document:
        media_type = f"a document ({reply.document.mime_type or 'unknown type'})"
    elif reply.video:
        media_type = "a video"
    elif reply.audio:
        media_type = "an audio file"
    elif reply.voice:
        media_type = "a voice message"
    elif reply.sticker:
        media_type = "a sticker"
    elif reply.animation:
        media_type = "an animation (GIF)"

    if not text and media_type:
        text = f"[{media_type}]"
    elif text and media_type:
        text = f"[{media_type}] {text}"

    if len(text) > 2000:
        text = text[:2000] + "..."

    return {
        "sender_name": sender_name,
        "sender_username": sender_username,
        "text": text,
        "media_type": media_type,
        "thread_id": message.message_thread_id,
    }


MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".webm": "video/webm",
}

MAX_MEDIA_SIZE = 18 * 1024 * 1024


async def download_and_encode(bot, file_id: str) -> tuple[str, str, str] | None:
    try:
        tg_file = await bot.get_file(file_id)
        if tg_file.file_size and tg_file.file_size > MAX_MEDIA_SIZE:
            logger.warning(f"File too large: {tg_file.file_size} bytes (max {MAX_MEDIA_SIZE})")
            return None
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        data = buf.getvalue()
        ext = Path(tg_file.file_path or "").suffix.lower() if tg_file.file_path else ""
        mime = MIME_MAP.get(ext, "application/octet-stream")
        b64 = base64.b64encode(data).decode("utf-8")
        filename = Path(tg_file.file_path or "file").name if tg_file.file_path else "file"
        return b64, mime, filename
    except Exception as e:
        logger.error(f"Failed to download file {file_id}: {e}")
        return None


def guess_media_type(message) -> tuple[str, str] | None:
    if message.photo:
        file_id = message.photo[-1].file_id
        return "image", file_id
    if message.document:
        mime = (message.document.mime_type or "").lower()
        if mime.startswith("image/"):
            return "image", message.document.file_id
        if mime.startswith("video/"):
            return "video", message.document.file_id
        if mime.startswith("audio/"):
            return "audio", message.document.file_id
        return "file", message.document.file_id
    if message.sticker:
        return "image", message.sticker.file_id
    if message.video:
        return "video", message.video.file_id
    if message.audio:
        return "audio", message.audio.file_id
    if message.voice:
        return "audio", message.voice.file_id
    if message.animation:
        return "video", message.animation.file_id
    if message.video_note:
        return "video", message.video_note.file_id
    return None, None


AUDIO_FORMAT_MAP = {
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/ogg": "ogg",
    "audio/aac": "aac",
    "audio/flac": "flac",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aiff": "aiff",
    "audio/x-aiff": "aiff",
}


async def make_content_parts(message, bot) -> list[dict]:
    parts = []
    modality, file_id = guess_media_type(message)
    if not modality or modality not in INPUT_MODALITIES:
        return parts

    encoded = await download_and_encode(bot, file_id)
    if encoded is None:
        return parts

    b64, mime, filename = encoded

    if modality == "image":
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })
    elif modality == "file":
        parts.append({
            "type": "file",
            "file": {
                "file_data": f"data:{mime};base64,{b64}",
                "filename": filename,
            },
        })
    elif modality == "video":
        parts.append({
            "type": "video_url",
            "video_url": {"url": f"data:{mime};base64,{b64}"},
        })
    elif modality == "audio":
        fmt = AUDIO_FORMAT_MAP.get(mime, "mp3")
        parts.append({
            "type": "input_audio",
            "input_audio": {
                "data": b64,
                "format": fmt,
            },
        })
    return parts


async def inline_query_handler(update: Update, context):
    query = update.inline_query.query.strip()
    from_user = update.inline_query.from_user
    user_id = from_user.id
    display_name = from_user.full_name
    username = f"@{from_user.username}" if from_user.username else "—"

    if not query:
        await update.inline_query.answer(
            results=[],
            button=InlineQueryResultsButton(
                text=f"Type a question to ask {BOT_NAME}",
                start_parameter="start",
            ),
        )
        return

    if not ALLOWED_GROUP_IDS and user_id not in ALLOWED_USER_IDS:
        logger.warning(f"Unauthorized access attempt by user {user_id}")
        await update.inline_query.answer(
            results=[],
            button=InlineQueryResultsButton(
                text="You are not authorized to use this bot",
                start_parameter="start",
            ),
        )
        return

    ts = int(time.time() * 1000)
    result_id_quick = f"quick_{user_id}_{ts}"
    result_id_deep = f"deep_{user_id}_{ts}"

    entry_base = {
        "query": query,
        "answer": None,
        "inline_message_id": None,
        "user_id": user_id,
        "display_name": display_name,
        "username": username,
        "ts": time.time(),
    }
    pending_answers[result_id_quick] = {**entry_base}
    pending_answers[result_id_deep] = {**entry_base}

    results = [
        InlineQueryResultArticle(
            id=result_id_quick,
            title="Answer quickly",
            description=query[:100],
            input_message_content=InputTextMessageContent(
                f"{BOT_NAME} - {query}\n\nThinking..."
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(text="Processing...", callback_data="think")]
            ]),
        ),
        InlineQueryResultArticle(
            id=result_id_deep,
            title="Think before answer",
            description=query[:100],
            input_message_content=InputTextMessageContent(
                f"{BOT_NAME} - {query}\n\nThinking..."
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(text="Processing...", callback_data="think")]
            ]),
        ),
    ]

    await update.inline_query.answer(results, cache_time=0)


async def openrouter_request(
    model: str,
    messages: list,
    tools: list | None = None,
    tool_choice: str | dict | None = None,
    reasoning_effort: str | None = None,
    user: str | None = None,
) -> dict:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    body = {"model": model, "messages": messages}
    if tools:
        body["tools"] = tools
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    if user:
        body["user"] = user

    url = f"{OPENROUTER_BASE_URL.rstrip('/')}/chat/completions"

    for attempt in range(2):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=body, timeout=aiohttp.ClientTimeout(total=180)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()

                text = await resp.text()
                error_data = {}
                try:
                    error_data = json.loads(text)
                except json.JSONDecodeError:
                    pass

                err_obj = error_data.get("error", {})
                err_type = err_obj.get("metadata", {}).get("error_type", "unknown")
                err_msg = err_obj.get("message", text[:500])

                if resp.status in (429, 503) and attempt == 0:
                    retry_after = resp.headers.get("Retry-After")
                    delay = int(retry_after) if retry_after and retry_after.isdigit() else 5
                    logger.warning(f"OpenRouter {resp.status} ({err_type}), retrying in {delay}s")
                    await asyncio.sleep(delay)
                    continue

                logger.error(f"OpenRouter API error {resp.status} ({err_type}): {err_msg}")
                raise Exception(f"OpenRouter API error {resp.status} ({err_type}): {err_msg}")

    raise Exception("OpenRouter API error: max retries exceeded")


async def process_ai_query(
    query: str,
    entry: dict,
    mode: str = "quick",
    reply_context: dict | None = None,
    media_parts: list[dict] | None = None,
    history_messages: list[dict] | None = None,
) -> str:
    answer = None
    try:
        max_tool_turns = 1 if mode == "quick" else 3

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

        reply_section = ""
        if reply_context:
            reply_section = (
                f"\nThe user is replying to a message from {reply_context['sender_name']} "
                f"({reply_context['sender_username']}):\n"
                f"---\n"
                f"{html.escape(reply_context['text'])}\n"
                f"---\n"
            )
            if reply_context.get("thread_id"):
                reply_section += f"Thread topic ID: {reply_context['thread_id']}.\n"

        BASE_SYSTEM = (
            f"Developer: {DEVELOPER}\n"
            f"Current datetime: {now}\n"
            f"Context: You are responding in a Telegram chat.\n"
            f"User: {entry['display_name']} ({entry['username']}, ID: {entry['user_id']})\n"
            f"Identity: You are {BOT_NAME} (@{BOT_USERNAME}), a helpful AI assistant. "
            f"Do not identify as Gemini, Claude, ChatGPT, or any other AI model. "
            f"If asked about your underlying model, say it is a free model chosen by the developer.\n"
            f"Usage: Users interact with you by typing @{BOT_USERNAME} followed by their question in any Telegram chat. "
            f"If asked how to use this bot, explain this inline mode usage.\n"
            f"Your input modalities: {', '.join(sorted(INPUT_MODALITIES)) if INPUT_MODALITIES else 'text'}.\n"
            f"CRITICAL \u2014 Formatting constraints (Telegram-only): You must ONLY use Telegram-compatible formatting. "
            f"Supported: <b>bold</b>, <i>italic</i>, <code>code</code>, <pre>pre</pre>, <a href='URL'>link</a>. "
            f"Do NOT use Markdown, tables, headings, blockquotes, horizontal rules, or any other formatting \u2014 Telegram does not support them. "
            f"Plain text is always safe if you are unsure.\n"
            f"You must provide a real answer \u2014 never give a placeholder or generic response. "
            f"If you do not know the answer, say so honestly. "
            f"Do not ask follow-up questions \u2014 this is a single-turn interaction, not a continuous conversation.\n"
            f"Do not mention search queries, URLs, or tool calls. Just provide the answer directly.\n"
            f"{reply_section}"
        )

        tools_desc = "\n".join(
            f"- {t['function']['name']}: {t['function']['description']}"
            for t in TOOLS_LIST
        )

        tool_turns = 0

        def make_prompt(turn_num: int) -> str:
            p = (
                BASE_SYSTEM
                + f"You have {max_tool_turns} tool call turn(s) to use the following tools:\n"
                f"{tools_desc}\n"
                f"Current turn: {turn_num + 1} of {max_tool_turns}.\n"
            )
            if turn_num + 1 >= max_tool_turns:
                p += (
                    "CRITICAL \u2014 This is your FINAL turn. You MUST provide a complete, "
                    "direct answer in this response. Do NOT use any tools or say you "
                    "will search later \u2014 answer now with what you already know."
                )
            else:
                p += "You may use tools if needed, but save enough turns for a final answer."
            return p

        user_content = query
        if media_parts:
            user_content = [{"type": "text", "text": query or "Analyze this media."}] + media_parts
        messages = [{"role": "system", "content": make_prompt(0)}]
        if history_messages:
            messages.extend(history_messages)
        messages.append({"role": "user", "content": user_content})

        async def _req(**kw):
            try:
                return await openrouter_request(model=OPENROUTER_MODEL_ID, **kw)
            except Exception:
                if not OPENROUTER_FALLBACK_MODEL_ID:
                    raise
                logger.warning("Primary model failed, trying fallback")
                return await openrouter_request(model=OPENROUTER_FALLBACK_MODEL_ID, **kw)

        response = await _req(
            messages=messages,
            tools=TOOLS_LIST,
            tool_choice="auto",
            reasoning_effort=REASONING_EFFORT,
            user=str(entry["user_id"]),
        )

        while response["choices"][0]["finish_reason"] == "tool_calls" and tool_turns < max_tool_turns:
            tool_turns += 1
            msg = response["choices"][0]["message"]
            if msg.get("tool_calls"):
                tool_entry = {"role": "assistant", "content": None, "tool_calls": msg["tool_calls"]}
                rc = msg.get("reasoning_content")
                if rc:
                    tool_entry["reasoning_content"] = rc
                messages.append(tool_entry)
            else:
                messages.append(msg)

            for tc in msg["tool_calls"]:
                args = json.loads(tc["function"]["arguments"])
                name = tc["function"]["name"]

                if name == "web_search":
                    search_query = args["query"]
                    if exa:
                        search_results = await exa.search(
                            search_query,
                            num_results=5,
                            contents={"text": True},
                        )
                        snippets = []
                        for r in search_results.results:
                            title = getattr(r, "title", "Untitled")
                            snippets.append(
                                f"Title: {title}\nURL: {r.url}\n{r.text[:1500]}"
                            )
                        tool_content = "\n\n---\n\n".join(snippets)
                    else:
                        tool_content = "Web search is not available (no API key configured)."

                elif name == "calculate":
                    try:
                        tool_content = safe_eval(args["expression"])
                    except Exception as e:
                        tool_content = f"Error: {e}"

                elif name == "run_string_operation":
                    try:
                        tool_content = run_string_operation(
                            args["operation"],
                            args.get("text", ""),
                            args.get("arg1", ""),
                            args.get("arg2", ""),
                        )
                    except Exception as e:
                        tool_content = f"Error: {e}"

                else:
                    tool_content = f"Error: unknown tool '{name}'"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_content,
                })

            messages[0] = {"role": "system", "content": make_prompt(tool_turns)}

            choice = "none" if tool_turns + 1 >= max_tool_turns else "auto"

            response = await _req(
                messages=messages,
                tools=TOOLS_LIST,
                tool_choice=choice,
                reasoning_effort=REASONING_EFFORT,
                user=str(entry["user_id"]),
            )

        answer = response["choices"][0]["message"].get("content")
        if not answer:
            answer = response["choices"][0]["message"].get("reasoning_content")
        if answer:
            answer = answer.strip()

    except asyncio.CancelledError:
        logger.warning("AI request cancelled")
    except Exception as e:
        err = str(e)
        if "rate_limit" in err or "429" in err or "503" in err:
            logger.warning(f"OpenRouter rate limited or unavailable: {e}")
        elif "401" in err or "unauthorized" in err.lower() or "auth" in err.lower():
            logger.error(f"OpenRouter auth error: {e}")
        elif "402" in err or "payment_required" in err or "insufficient credits" in err.lower():
            logger.error(f"OpenRouter credits exhausted: {e}")
        elif "403" in err or "forbidden" in err.lower() or "blocked" in err.lower():
            logger.warning(f"OpenRouter request blocked: {e}")
        else:
            logger.error(f"AI API error: {e}")

    if not answer:
        answer = f"{BOT_NAME} is currently unavailable. Please try again."

    return sanitize_html(answer)


async def process_and_edit(result_id: str, query: str, entry: dict, context):
    parts = result_id.split("_")
    mode = parts[0] if parts and parts[0] in ("quick", "deep") else "quick"

    answer = await process_ai_query(query, entry, mode=mode)
    entry["answer"] = answer

    inline_msg_id = entry.get("inline_message_id")
    if inline_msg_id:
        try:
            await edit_with_answer(context.bot, inline_msg_id, query, answer)
        except Exception as e:
            logger.error(f"Failed to edit message: {e}")
            logger.error(f"Query: {query}")
            m = re.search(r"byte offset (\d+)", str(e))
            if m:
                offset = int(m.group(1))
                start = max(0, offset - 80)
                raw_bytes = answer.encode("utf-8")
                end = min(len(raw_bytes), offset + 80)
                snippet = raw_bytes[start:end].decode("utf-8", errors="replace")
                logger.error(f"Context around byte {offset}: ...{snippet}...")
            logger.debug(f"Full answer ({len(answer)} chars): {answer}")
        finally:
            pending_answers.pop(result_id, None)


async def chosen_inline_result_handler(update: Update, context):
    result_id = update.chosen_inline_result.result_id
    inline_message_id = update.chosen_inline_result.inline_message_id

    if not inline_message_id:
        return

    entry = pending_answers.get(result_id)
    if not entry:
        return

    entry["inline_message_id"] = inline_message_id

    answer = entry.get("answer")
    if answer:
        await edit_with_answer(context.bot, inline_message_id, entry["query"], answer)
        pending_answers.pop(result_id, None)
    else:
        asyncio.create_task(process_and_edit(result_id, entry["query"], entry, context))


async def _dm_respond(
    message,
    context,
    query: str,
    mode: str,
    media_parts: list[dict] | None,
    reply_context: dict | None,
):
    user_id = message.from_user.id
    display_name = message.from_user.full_name
    username = f"@{message.from_user.username}" if message.from_user.username else "\u2014"

    entry = {
        "user_id": user_id,
        "display_name": display_name,
        "username": username,
    }

    session = sessions.get(user_id)
    if session:
        if time.time() - session["last_activity"] > SESSION_TTL:
            session["messages"] = []
        history = session["messages"]
    else:
        session = {"messages": [], "last_activity": time.time()}
        sessions[user_id] = session
        history = None

    thinking = await message.reply_text(
        f"<b>{BOT_NAME}</b> is thinking\u2026",
        parse_mode="HTML",
    )

    answer = await process_ai_query(
        query,
        entry,
        mode=mode,
        reply_context=reply_context,
        media_parts=media_parts or None,
        history_messages=history,
    )

    session["messages"].append({"role": "user", "content": query})
    session["messages"].append({"role": "assistant", "content": answer})
    session["last_activity"] = time.time()

    chunks = split_answer(answer, CHUNK_SIZE - 100)
    first = True
    for chunk in chunks:
        if first:
            await thinking.edit_text(text=chunk, parse_mode="HTML")
            first = False
        else:
            await message.reply_text(text=chunk, parse_mode="HTML")


async def handle_message(update: Update, context):
    message = update.message
    if not message or message.chat.type != Chat.PRIVATE:
        return

    query = (message.text or message.caption or "").strip()
    if not query:
        return

    has_disallowed_media = any([
        message.document, message.voice, message.animation,
        message.video_note, message.sticker,
    ])
    if has_disallowed_media:
        return

    if message.from_user.id not in ALLOWED_USER_IDS:
        logger.warning(f"Unauthorized DM from user {message.from_user.id}")
        return

    reply_context = extract_reply_context(message)
    media_parts = await make_content_parts(message, context.bot)

    await _dm_respond(message, context, query, "deep", media_parts, reply_context)


async def ask_command(update: Update, context):
    message = update.message
    if not message:
        return

    query = " ".join(context.args) if context.args else ""
    has_media = any([
        message.photo, message.document, message.video,
        message.audio, message.voice, message.animation,
        message.video_note, message.sticker,
    ])

    if not query and not has_media:
        await message.reply_text(
            "Usage: /ask_fast your question here\n"
            "       /ask_think your question here"
        )
        return

    user_id = message.from_user.id
    if user_id not in ALLOWED_USER_IDS:
        return

    command = message.text.split(maxsplit=1)[0].lower() if message.text else ""
    mode = "quick" if "fast" in command else "deep"

    reply_context = extract_reply_context(message)
    media_parts = await make_content_parts(message, context.bot)

    if message.chat.type == Chat.PRIVATE:
        await message.reply_text(
            "Just send your message directly in this chat, or use /refresh_session."
        )
        return

    display_name = message.from_user.full_name
    username = f"@{message.from_user.username}" if message.from_user.username else "\u2014"

    entry = {
        "user_id": user_id,
        "display_name": display_name,
        "username": username,
    }

    thinking = await message.reply_text(
        f"<b>{BOT_NAME}</b> is thinking\u2026",
        parse_mode="HTML",
    )

    answer = await process_ai_query(
        query,
        entry,
        mode=mode,
        reply_context=reply_context,
        media_parts=media_parts or None,
    )

    try:
        if len(answer) <= CHUNK_SIZE - 100:
            await thinking.edit_text(text=answer, parse_mode="HTML")
        else:
            cut = find_good_break(answer, CHUNK_SIZE - 100)
            truncated = answer[:cut].rstrip()
            truncated += "\n\n\u2014 Answer truncated due to length \u2014"
            await thinking.edit_text(text=truncated, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to edit answer message: {e}")


async def refresh_session(update: Update, context):
    if update.message.chat.type != Chat.PRIVATE:
        await update.message.reply_text("This command only works in private chat.")
        return
    sessions.pop(update.message.from_user.id, None)
    await update.message.reply_text("Session cleared. Start a new conversation.")


async def edit_with_answer(bot, inline_message_id, query, answer):
    prefix = f"<b>Question:</b> {html.escape(query)}\n\n<b>Answer:</b> "
    avail = CHUNK_SIZE - len(prefix)

    if len(answer) <= avail:
        await bot.edit_message_text(
            inline_message_id=inline_message_id,
            text=prefix + answer,
            parse_mode="HTML",
        )
        return

    first_max = CHUNK_SIZE - len(prefix) - PAGE_INDICATOR_OVERHEAD
    rest_max = CHUNK_SIZE - PAGE_INDICATOR_OVERHEAD

    chunks = []
    remaining = answer.strip()

    cut = find_good_break(remaining, first_max)
    chunks.append(remaining[:cut].strip())
    remaining = remaining[cut:].strip()

    while remaining:
        if len(remaining) <= rest_max:
            chunks.append(remaining)
            break
        cut = find_good_break(remaining, rest_max)
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()

    long_responses[inline_message_id] = {
        "prefix": prefix,
        "chunks": chunks,
        "current": 0,
        "ts": time.time(),
    }

    total = len(chunks)
    text = prefix + chunks[0]
    if total > 1:
        text += f"\n\n\u2014 Page 1/{total} \u2014"
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(text="Next Page", callback_data="next")]
        ])
    else:
        reply_markup = None

    await bot.edit_message_text(
        inline_message_id=inline_message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def page_callback(update: Update, context):
    query = update.callback_query
    await query.answer()

    inline_msg_id = query.inline_message_id
    if not inline_msg_id:
        return

    entry = long_responses.get(inline_msg_id)
    if not entry:
        await query.edit_message_text(
            text=f"This answer is no longer available. Please type @{BOT_USERNAME} to ask again.",
            parse_mode="HTML",
        )
        return

    data = query.data
    if data == "next":
        entry["current"] += 1
    elif data == "prev":
        entry["current"] -= 1

    page = entry["current"]
    chunks = entry["chunks"]
    total = len(chunks)

    if page == 0:
        text = entry["prefix"] + chunks[0]
    else:
        text = chunks[page]

    row = []
    if total > 1:
        text += f"\n\n\u2014 Page {page + 1}/{total} \u2014"
        if page > 0:
            row.append(InlineKeyboardButton("Previous Page", callback_data="prev"))
        if page < total - 1:
            row.append(InlineKeyboardButton("Next Page", callback_data="next"))

    reply_markup = InlineKeyboardMarkup([row]) if row else None

    await query.edit_message_text(
        text=text,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def think_callback(update: Update, context):
    await update.callback_query.answer()


async def error_handler(update: Update, context):
    update_id = update.update_id if update else "N/A"
    logger.error(f"Error handling update {update_id}: {context.error}")


async def post_init(app):
    asyncio.create_task(stale_cleaner())


async def stale_cleaner():
    while True:
        await asyncio.sleep(30)
        now = time.time()
        stale_answers = [
            rid for rid, e in pending_answers.items()
            if now - e["ts"] > PENDING_TTL
            and not (e.get("inline_message_id") and e["answer"] is None)
        ]
        for rid in stale_answers:
            pending_answers.pop(rid, None)
        stale_long = [
            imid for imid, e in long_responses.items()
            if now - e["ts"] > PENDING_TTL
        ]
        for imid in stale_long:
            long_responses.pop(imid, None)


async def health_handler(request):
    return web.json_response({"status": "ok"})


async def setup_custom_webhook(app):
    async with app:
        await app.start()

        async def webhook_handler(request):
            update = Update.de_json(await request.json(), app.bot)
            asyncio.create_task(app.process_update(update))
            return web.Response(status=200)

        await app.bot.set_webhook(
            url=f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}",
            drop_pending_updates=True,
        )

        web_app = web.Application()
        web_app.router.add_get("/health", health_handler)
        web_app.router.add_post(f"/{TELEGRAM_BOT_TOKEN}", webhook_handler)

        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()

        logger.info(f"Custom webhook server running on {WEBHOOK_URL}")

        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
            await app.stop()


def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).updater(None).post_init(post_init).build()
    app.add_handler(InlineQueryHandler(inline_query_handler))
    app.add_handler(ChosenInlineResultHandler(chosen_inline_result_handler))
    app.add_handler(CallbackQueryHandler(page_callback, pattern="^(prev|next)$"))
    app.add_handler(CallbackQueryHandler(think_callback, pattern="^think$"))
    app.add_handler(MessageHandler(
        ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_message,
    ))
    app.add_handler(CommandHandler(["ask_fast", "ask_think"], ask_command))
    app.add_handler(CommandHandler("refresh_session", refresh_session))
    app.add_error_handler(error_handler)

    if WEBHOOK_URL:
        logger.info(f"Bot starting via custom webhook on {WEBHOOK_URL}")
        asyncio.run(setup_custom_webhook(app))
    else:
        logger.info("Bot started, polling for updates...")
        app.run_polling()


if __name__ == "__main__":
    main()
