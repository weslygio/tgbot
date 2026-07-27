import asyncio
import json
import logging
import re
from datetime import datetime
import time

from aiohttp import web
from exa_py import AsyncExa
from openai import AsyncOpenAI, RateLimitError
from telegram import (
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
    InlineQueryHandler,
)

from config import (
    ALLOWED_GROUP_IDS,
    ALLOWED_USER_IDS,
    BOT_NAME,
    BOT_USERNAME,
    DEVELOPER,
    EXA_API_KEY,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
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

client = AsyncOpenAI(
    base_url=OPENAI_BASE_URL or None,
    api_key=OPENAI_API_KEY,
)

exa = AsyncExa(api_key=EXA_API_KEY) if EXA_API_KEY else None

ALLOWED_TAGS = {
    "b", "strong", "i", "em", "u", "s", "strike", "del",
    "code", "pre", "a", "span",
}


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

    allowed = ALLOWED_TAGS
    def _replacer(m: re.Match) -> str:
        raw = m.group(0)
        tag = m.group(1).lower()
        if tag in allowed:
            if tag == "span" and 'tg-spoiler' not in raw:
                return ""
            return raw
        return ""

    return re.sub(r"</?([^\s>/]+)(\s[^>]*)?>", _replacer, text)

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

pending_answers = {}
long_responses = {}


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


async def process_and_edit(result_id: str, query: str, entry: dict, context):
    try:
        parts = result_id.split("_")
        mode = parts[0] if parts and parts[0] in ("quick", "deep") else "quick"
        max_tool_turns = 1 if mode == "quick" else 3

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        BASE_SYSTEM = (
            f"Developer: {DEVELOPER}\n"
            f"Current datetime: {now}\n"
            f"Context: You are responding in a Telegram chat via inline mode.\n"
            f"User: {entry['display_name']} ({entry['username']}, ID: {entry['user_id']})\n"
            f"Identity: You are {BOT_NAME} (@{BOT_USERNAME}), a helpful AI assistant. "
            f"Do not identify as Gemini, Claude, ChatGPT, or any other AI model. "
            f"If asked about your underlying model, say it is a free model chosen by the developer.\n"
            f"Usage: Users interact with you by typing @{BOT_USERNAME} followed by their question in any Telegram chat. "
            f"If asked how to use this bot, explain this inline mode usage.\n"
            f"CRITICAL — Formatting constraints (Telegram-only): You must ONLY use Telegram-compatible formatting. "
            f"Supported: <b>bold</b>, <i>italic</i>, <code>code</code>, <pre>pre</pre>, <a href='URL'>link</a>. "
            f"Do NOT use Markdown, tables, headings, blockquotes, horizontal rules, or any other formatting — Telegram does not support them. "
            f"Plain text is always safe if you are unsure.\n"
            f"You must provide a real answer — never give a placeholder or generic response. "
            f"If you do not know the answer, say so honestly. "
            f"Do not ask follow-up questions — this is a single-turn interaction, not a continuous conversation.\n"
            f"Do not mention search queries, URLs, or tool calls. Just provide the answer directly.\n"
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
                    "CRITICAL — This is your FINAL turn. You MUST provide a complete, "
                    "direct answer in this response. Do NOT use any tools or say you "
                    "will search later — answer now with what you already know."
                )
            else:
                p += "You may use tools if needed, but save enough turns for a final answer."
            return p

        messages = [
            {"role": "system", "content": make_prompt(0)},
            {"role": "user", "content": query},
        ]

        kwargs = {
            "model": OPENAI_MODEL,
            "messages": messages,
            "tools": TOOLS_LIST,
            "tool_choice": "auto",
            "reasoning_effort": REASONING_EFFORT,
            "extra_body": {"thinking": {"type": "enabled"}},
        }

        response = await client.chat.completions.create(**kwargs)

        while response.choices[0].finish_reason == "tool_calls" and tool_turns < max_tool_turns:
            tool_turns += 1
            msg = response.choices[0].message
            if msg.tool_calls:
                entry = {"role": "assistant", "content": None, "tool_calls": msg.tool_calls}
                rc = getattr(msg, "reasoning_content", None)
                if rc:
                    entry["reasoning_content"] = rc
                messages.append(entry)
            else:
                messages.append(msg)

            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                name = tc.function.name

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
                    "tool_call_id": tc.id,
                    "content": tool_content,
                })

            messages[0] = {"role": "system", "content": make_prompt(tool_turns)}
            kwargs["messages"] = messages

            if tool_turns + 1 >= max_tool_turns:
                kwargs["tool_choice"] = "none"
            else:
                kwargs["tool_choice"] = "auto"

            response = await client.chat.completions.create(**kwargs)

        answer = response.choices[0].message.content
        if not answer:
            answer = getattr(response.choices[0].message, "reasoning_content", None)
        if answer:
            answer = answer.strip()

    except asyncio.CancelledError:
        pending_answers.pop(result_id, None)
        return
    except RateLimitError as e:
        logger.error(f"Rate limit error: {e}")
        answer = f"{BOT_NAME} is currently unavailable."
    except Exception as e:
        logger.error(f"AI API error: {e}")
        answer = None

    if not answer:
        answer = "The AI returned an empty response."

    answer = sanitize_html(answer)
    entry["answer"] = answer

    inline_msg_id = entry.get("inline_message_id")
    if inline_msg_id:
        await edit_with_answer(context.bot, inline_msg_id, query, answer)
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
        await process_and_edit(result_id, entry["query"], entry, context)


async def edit_with_answer(bot, inline_message_id, query, answer):
    answer = sanitize_html(answer)
    prefix = f"<b>Question:</b> {query}\n\n<b>Answer:</b> "
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
            rid for rid, e in pending_answers.items() if now - e["ts"] > PENDING_TTL
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
            await app.process_update(update)
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
    app.add_error_handler(error_handler)

    if WEBHOOK_URL:
        logger.info(f"Bot starting via custom webhook on {WEBHOOK_URL}")
        asyncio.run(setup_custom_webhook(app))
    else:
        logger.info("Bot started, polling for updates...")
        app.run_polling()


if __name__ == "__main__":
    main()
