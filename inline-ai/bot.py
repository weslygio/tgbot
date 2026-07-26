import asyncio
import logging
from datetime import datetime
import time

from openai import AsyncOpenAI
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
    DEEPSEEK_MODEL,
    DEVELOPER,
    OPENCODE_ZEN_API_KEY,
    OPENCODE_ZEN_BASE_URL,
    PORT,
    TELEGRAM_BOT_TOKEN,
    WEBHOOK_URL,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

client = AsyncOpenAI(
    base_url=OPENCODE_ZEN_BASE_URL,
    api_key=OPENCODE_ZEN_API_KEY,
)

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
    user_id = update.inline_query.from_user.id

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

    result_id = f"think_{user_id}_{int(time.time() * 1000)}"

    entry = {
        "query": query,
        "answer": None,
        "inline_message_id": None,
        "user_id": user_id,
        "ts": time.time(),
    }
    pending_answers[result_id] = entry

    results = [
        InlineQueryResultArticle(
            id=result_id,
            title=BOT_NAME,
            description=query[:100],
            input_message_content=InputTextMessageContent(
                f"{BOT_NAME} - {query}\n\nThinking..."
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(text="Processing...", callback_data="think")]
            ]),
        )
    ]

    await update.inline_query.answer(results, cache_time=0)


async def process_and_edit(result_id: str, query: str, entry: dict, context):
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        system_prompt = (
            f"Developer: {DEVELOPER}\n"
            f"Current datetime: {now}\n"
            f"Context: You are responding in a Telegram chat via inline mode.\n"
            f"Identity: You are {BOT_NAME} (@{BOT_USERNAME}), a helpful AI assistant powered by DeepSeek V4 Flash Free via OpenCode Zen. "
            f"Do not identify as Gemini, Claude, ChatGPT, or any other AI model. "
            f"If asked about your underlying model, say you are Opus 5.\n"
            f"Usage: Users interact with you by typing @{BOT_USERNAME} followed by their question in any Telegram chat. "
            f"If asked how to use this bot, explain this inline mode usage.\n"
            f"Available formatting (Telegram HTML-style): <b>bold</b>, <i>italic</i>, <code>code</code>, <pre>pre</pre>, <a href='URL'>link</a>.\n"
            f"Rules: Do NOT use any Markdown or HTML formatting unless the user explicitly asks for it. "
            f"Answer concisely and accurately in plain text."
        )
        response = await client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
            temperature=0.7,
        )
        message = response.choices[0].message
        answer = message.content
        if not answer:
            answer = getattr(message, "reasoning_content", None)
        if answer:
            answer = answer.strip()
    except asyncio.CancelledError:
        pending_answers.pop(result_id, None)
        return
    except Exception as e:
        logger.error(f"DeepSeek API error: {e}")
        answer = None

    if not answer:
        answer = "The AI returned an empty response."

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
    prefix = f"Question: {query}\n\nAnswer: "
    avail = CHUNK_SIZE - len(prefix)

    if len(answer) <= avail:
        await bot.edit_message_text(
            inline_message_id=inline_message_id,
            text=prefix + answer,
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
            text=f"This answer is no longer available. Please type @{BOT_USERNAME} to ask again."
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


def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(InlineQueryHandler(inline_query_handler))
    app.add_handler(ChosenInlineResultHandler(chosen_inline_result_handler))
    app.add_handler(CallbackQueryHandler(page_callback, pattern="^(prev|next)$"))
    app.add_error_handler(error_handler)

    if WEBHOOK_URL:
        logger.info(f"Bot starting via webhook on {WEBHOOK_URL}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}",
            drop_pending_updates=True,
        )
    else:
        logger.info("Bot started, polling for updates...")
        app.run_polling()


if __name__ == "__main__":
    main()
