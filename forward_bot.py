# forward_bot.py

import os, ast, asyncio, threading
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from flask import Flask

# ——— HTTP keep-alive ———————————————————————————————————————————————————————
app = Flask('keep_alive')

@app.route('/')
def home():
    return 'OK'

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

# ——— Bot Telegram —————————————————————————————————————————————————————————
async def start_bot():
    api_id       = int(os.environ['TELEGRAM_API_ID'])
    api_hash     = os.environ['TELEGRAM_API_HASH']
    session_str  = os.environ['SESSION_STRING']
    dest_chat_id = int(os.environ['DEST_CHAT_ID'])
    source_ids   = ast.literal_eval(os.environ['SOURCE_CHAT_IDS'])

    client = TelegramClient(StringSession(session_str), api_id, api_hash)
    await client.start()

    # Mapeia (origem_chat, origem_msg_id) → destino_msg_id
    forwarded = {}

    async def make_header(ev):
        sender = await ev.get_sender()
        if hasattr(sender, 'first_name'):
            name = sender.first_name
        elif hasattr(sender, 'title'):
            name = sender.title
        else:
            name = str(ev.message.sender_id or '')
        return f"🚀 {ev.chat.title} — {name}:\n"

    @client.on(events.NewMessage(chats=source_ids))
    async def on_new(ev):
        msg    = ev.message
        header = await make_header(ev)
        sent   = None

        # Tenta enviar mídia primeiro
        if msg.media:
            path = await msg.download_media()
            if path:
                try:
                    sent = await client.send_file(
                        dest_chat_id,
                        path,
                        caption=header + (msg.text or '')
                    )
                except Exception:
                    sent = None
                finally:
                    try: os.remove(path)
                    except: pass

        # Se não enviou mídia, tenta texto
        if not sent and msg.text:
            sent = await client.send_message(
                dest_chat_id,
                header + msg.text
            )

        # Guarda só se realmente enviou
        if sent:
            forwarded[(ev.chat_id, msg.id)] = sent.id

    @client.on(events.MessageEdited(chats=source_ids))
    async def on_edit(ev):
        msg = ev.message
        key = (ev.chat_id, msg.id)
        if key not in forwarded:
            return
        dest_msg_id = forwarded[key]
        header      = await make_header(ev)

        # Se agora há mídia, reenvia e atualiza o mapa
        if msg.media:
            path = await msg.download_media()
            if path:
                try:
                    await client.delete_messages(dest_chat_id, dest_msg_id)
                except:
                    pass
                try:
                    sent = await client.send_file(
                        dest_chat_id,
                        path,
                        caption=header + (msg.text or '')
                    )
                    forwarded[key] = sent.id
                except:
                    pass
                finally:
                    try: os.remove(path)
                    except: pass

        # Se não há mídia, tenta editar o texto/caption
        else:
            new_text = header + (msg.text or '')
            try:
                await client.edit_message(
                    dest_chat_id,
                    dest_msg_id,
                    new_text
                )
            except:
                pass

    print("🤖 Bot rodando e escutando edições.")
    await client.run_until_disconnected()

# ——— Entrada principal ————————————————————————————————————————————————————
if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(start_bot())
