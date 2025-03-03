import os
import json
import logging
import aiohttp
import sqlite3
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

# Load API Keys from Environment Variables
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
TELEGRAM_BOT_API = os.getenv('TELEGRAM_BOT_API')

TIMEOUT_SECONDS = 60

if not GEMINI_API_KEY or not TELEGRAM_BOT_API:
    raise ValueError("API keys are missing! Set GEMINI_API_KEY and TELEGRAM_BOT_API.")

URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
HEADERS = {"Content-Type": "application/json"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# SQLite Database Connection
conn = sqlite3.connect("memory.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS memory (
        user_id INTEGER PRIMARY KEY,
        history TEXT,
        facts TEXT,
        pending_thought TEXT
    )
""")
conn.commit()

USER_MEMORY = {}

def load_memory():
    """Load chat history from database"""
    global USER_MEMORY
    USER_MEMORY = {}
    for user_id, history, facts, pending_thought in cursor.execute("SELECT user_id, history, facts, pending_thought FROM memory"):
        USER_MEMORY[user_id] = {
            "history": json.loads(history),
            "facts": json.loads(facts) if facts else {},
            "pending_thought": pending_thought
        }
load_memory()

def save_memory(user_id):
    """Save chat history to database"""
    history_json = json.dumps(USER_MEMORY[user_id]["history"])
    facts_json = json.dumps(USER_MEMORY[user_id]["facts"])
    pending_thought = USER_MEMORY[user_id].get("pending_thought", None)
    cursor.execute("REPLACE INTO memory (user_id, history, facts, pending_thought) VALUES (?, ?, ?, ?)",
                   (user_id, history_json, facts_json, pending_thought))
    conn.commit()

async def fetch_ai_response(user_name: str, user_prompt: str, context_data: list) -> str:
    """Fetch AI-generated response, making it behave like a normal person."""
    dialogue = [{"role": "user" if i % 2 == 0 else "model", "parts": [{"text": msg}]} for i, msg in enumerate(context_data[-50:])]

    user_prompt_with_name = f"{user_name}: {user_prompt}"
    dialogue.append({"role": "user", "parts": [{"text": user_prompt_with_name}]})

    data = {"contents": dialogue}

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)) as session:
        async with session.post(f"{URL}?key={GEMINI_API_KEY}", json=data, headers=HEADERS) as response:
            if response.status == 200:
                response_data = await response.json()
                try:
                    return response_data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError):
                    logging.error("Unexpected AI response format: %s", response_data)
                    return "Huh? That didn’t make sense."
            else:
                logging.error("Gemini API error %d: %s", response.status, await response.text())
                return "Uh-oh, I messed up. Try again!"

async def chat(update: Update, context: CallbackContext) -> None:
    """Handles chat messages intelligently."""
    user = update.message.from_user
    user_id = user.id
    chat_type = update.message.chat.type
    bot_username = context.bot.username
    bot_name = context.bot.first_name.lower()
    user_message = update.message.text.strip()

    logging.info(f"Received: '{user_message}' from {user.first_name} ({chat_type})")

    if user_id not in USER_MEMORY:
        USER_MEMORY[user_id] = {"history": [], "facts": {}, "pending_thought": None}

    user_memory = USER_MEMORY[user_id]

    # Handle pending thought
    if user_memory["pending_thought"]:
        user_memory["history"].append(f"{user_memory['pending_thought']} {user_message}")
        user_memory["pending_thought"] = None
        save_memory(user_id)
    else:
        user_memory["history"].append(user_message)

    user_memory["history"] = user_memory["history"][-50:]

    # If message looks like an unfinished equation, store it and wait
    if re.search(r"[\+\-\*/]", user_message) and re.search(r"[a-zA-Z]+", user_message):
        user_memory["pending_thought"] = user_message
        save_memory(user_id)
        await update.message.reply_text("Oh! What are the missing values? Let me know! 😃")
        return

    ai_reply = await fetch_ai_response(user.first_name, user_message, user_memory["history"])
    user_memory["history"].append(f"Bot: {ai_reply}")
    save_memory(user_id)

    await update.message.reply_text(ai_reply)

def main():
    """Start the bot"""
    app = Application.builder().token(TELEGRAM_BOT_API).build()

    app.add_handler(CommandHandler("start", lambda update, context: update.message.reply_text("Hey! Just talk to me like a friend.")))
    app.add_handler(CommandHandler("sigma", chat))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    logging.info("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
