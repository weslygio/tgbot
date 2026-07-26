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
    TELEGRAM_BOT_TOKEN,
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
PENDING_TTL = 120

active_tasks = {}
pending_answers = {}
long_responses = {}


async def inline_query_handler(update: Update, context):
    query = update.inline_query.query.strip()
    user_id = update.inline_query.from_user.id

    if not query:
        await update.inline_query.answer(
            results=[],
            button=InlineQueryResultsButton(
                text="Type a question to ask DeepSeek AI",
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

    if user_id in active_tasks:
        active_tasks[user_id].cancel()

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

    task = asyncio.create_task(process_and_edit(result_id, query, entry, context))
    active_tasks[user_id] = task

    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        if active_tasks.get(user_id) is task:
            del active_tasks[user_id]


async def process_and_edit(result_id: str, query: str, entry: dict, context):
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        system_prompt = (
            f"Developer: {DEVELOPER}\n"
            f"Current datetime: {now}\n"
            f"Context: You are responding in a Telegram chat via inline mode.\n"
            f"Identity: You are {BOT_NAME} ({BOT_USERNAME}), a helpful AI assistant powered by DeepSeek V4 Flash Free via OpenCode Zen. "
            f"Do not identify as Gemini, Claude, ChatGPT, or any other AI model. "
            f"If asked about your underlying model, say you are Opus 5.\n"
            f"Usage: Users interact with you by typing {BOT_USERNAME} followed by their question in any Telegram chat. "
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


async def edit_with_answer(bot, inline_message_id, query, answer):
    prefix = f"Question: {query}\n\nAnswer: "
    avail = CHUNK_SIZE - len(prefix)

    if len(answer) <= avail:
        await bot.edit_message_text(
            inline_message_id=inline_message_id,
            text=prefix + answer,
        )
        return

    chunk = answer[:avail]
    long_responses[inline_message_id] = {
        "full_text": answer,
        "offset": avail,
        "ts": time.time(),
    }

    await bot.edit_message_text(
        inline_message_id=inline_message_id,
        text=prefix + chunk + "\n\n- more below -",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(text="Continue", callback_data="cont")]
        ]),
    )


async def continue_callback(update: Update, context):
    query = update.callback_query
    await query.answer()

    inline_msg_id = query.inline_message_id
    if not inline_msg_id:
        return

    entry = long_responses.get(inline_msg_id)
    if not entry:
        return

    offset = entry["offset"]
    remaining = entry["full_text"][offset:]

    if not remaining:
        long_responses.pop(inline_msg_id, None)
        return

    chunk = remaining[:CHUNK_SIZE]
    entry["offset"] = offset + len(chunk)

    remaining_after = entry["full_text"][entry["offset"]:]
    reply_markup = None
    if remaining_after:
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(text="Continue", callback_data="cont")]
        ])
    else:
        long_responses.pop(inline_msg_id, None)

    await query.edit_message_text(
        text=chunk,
        reply_markup=reply_markup,
    )


async def error_handler(update: Update, context):
    update_id = update.update_id if update else "N/A"
    logger.error(f"Error handling update {update_id}: {context.error}")


async def post_init(app):
    app.create_task(stale_cleaner())


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
    app.add_handler(CallbackQueryHandler(continue_callback, pattern="^cont$"))
    app.add_error_handler(error_handler)
    logger.info("Bot started, polling for updates...")
    app.run_polling()


if __name__ == "__main__":
    main()
