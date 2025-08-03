# forward_bot.py

import os, ast, asyncio, threading
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from flask import Flask

# ---------- HTTP server (keep-alive) ----------
app = Flask('keep_alive')

@app.route('/')
def home():
    return 'OK'

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

# ---------- Bot Telegram ----------
async def start_bot():
    api_id       = int(os.environ['TELEGRAM_API_ID'])
    api_hash     = os.environ['TELEGRAM_API_HASH']
    session_str  = os.environ['SESSION_STRING']
    dest_chat_id = int(os.environ['DEST_CHAT_ID'])
    source_ids   = ast.literal_eval(os.environ['SOURCE_CHAT_IDS'])

    client = TelegramClient(StringSession(session_str), api_id, api_hash)
    await client.start()

    @client.on(events.NewMessage(chats=source_ids))
    async def handler(event):
        msg    = event.message
        header = f"🚀 {event.chat.title} — {msg.sender.first_name}:\n"
        if msg.text:
            await client.send_message(dest_chat_id, header + msg.text)
        if msg.media:
            path = await msg.download_media()
            await client.send_file(dest_chat_id, path,
                                   caption=header + (msg.text or ''))
            try: os.remove(path)
            except: pass

    print("🤖 Bot rodando.")
    await client.run_until_disconnected()

# ---------- Entrada principal ----------
if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(start_bot())
