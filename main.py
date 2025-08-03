# main.py
# BotFather userbot para encaminhar mensagens de grupos e channels,
# agora com fallback para grupos que desativaram o forward.

import os
import json
import asyncio
import threading
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetFullChannelRequest

# ── Configuração via ENV vars ────────────────────────────────────────────────
API_ID          = int(os.environ['TELEGRAM_API_ID'])
API_HASH        = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN       = os.environ['BOT_TOKEN']
DEST_CHAT_ID    = int(os.environ['DEST_CHAT_ID'])
SESSION_STRING  = os.environ['SESSION_STRING']
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS', '[]'))
# Ex: SOURCE_CHAT_IDS='[-1002460735067,-1002455542600,-1002794084735]'

# ── Persistência ─────────────────────────────────────────────────────────────
SESS_FILE = 'sessions.json'
SUBS_FILE = 'subscriptions.json'

def load(fname):
    try:
        return json.load(open(fname, 'r', encoding='utf-8'))
    except:
        return {}

def save(fname, data):
    with open(fname, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

sessions      = load(SESS_FILE)
subscriptions = load(SUBS_FILE)

# ── Keep-alive HTTP para Railway ──────────────────────────────────────────────
app = Flask('keep_alive')
@app.route('/')
def home():
    return 'OK'

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

# ── BotFather userbot ─────────────────────────────────────────────────────────
bot = TelegramClient('bot_session', API_ID, API_HASH)

@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def handler(ev):
    uid   = ev.sender_id
    text  = ev.raw_text.strip()
    reply = ev.reply

    if text in ('/start', '/help'):
        await reply(
            "**👋 Bem-vindo ao Encaminhador!**\n\n"
            "🔗 Gere sua Session online (Colab):\n"
            "[Clique aqui](https://colab.research.google.com/drive/1H3vHoNr_8CGW0rLEV-fFKKINo8mHWr5U?usp=sharing)\n\n"
            "**Fluxo:**\n"
            "1️⃣ `/setsession SUA_SESSION`\n"
            "2️⃣ `/listgroups`\n"
            "3️⃣ `/subscribe GROUP_ID`\n"
            "4️⃣ `/unsubscribe GROUP_ID`",
            parse_mode='Markdown'
        )
        return

    if text.startswith('/setsession '):
        sess = text.split(' ', 1)[1].strip()
        sessions[str(uid)] = sess
        save(SESS_FILE, sessions)
        await reply("✅ Session salva! Agora use `/listgroups`.")
        await ensure_client(uid)
        return

    client = await ensure_client(uid)
    if not client:
        return await reply("❌ Primeiro use `/setsession SUA_SESSION`.")

    if text == '/listgroups':
        dialogs = await client.get_dialogs()
        lines = [f"{d.title or 'Sem título'} — `{d.id}`" for d in dialogs if d.is_group or d.is_channel]
        await reply("📋 *Seus grupos:*
" + "\n".join(lines[:50]), parse_mode='Markdown')
        return

    if text.startswith('/subscribe '):
        try:
            gid = int(text.split(' ', 1)[1])
        except:
            return await reply("❌ ID inválido.")
        lst = subscriptions.setdefault(str(uid), [])
        if gid in lst:
            return await reply("⚠️ Já inscrito nesse grupo.")
        lst.append(gid)
        save(SUBS_FILE, subscriptions)
        return await reply(f"✅ Inscrito no `{gid}`.")

    if text.startswith('/unsubscribe '):
        try:
            gid = int(text.split(' ', 1)[1])
        except:
            return await reply("❌ ID inválido.")
        lst = subscriptions.get(str(uid), [])
        if gid not in lst:
            return await reply("❌ Você não está inscrito nesse grupo.")
        lst.remove(gid)
        save(SUBS_FILE, subscriptions)
        return await reply(f"🗑️ Desinscrito do `{gid}`.")

    await reply("❓ Comando não reconhecido. Use `/help`.", parse_mode='Markdown')

# ── Admin client para canais iniciais ─────────────────────────────────────────
admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

@admin_client.on(events.NewMessage(chats=SOURCE_CHAT_IDS))
async def forward_initial(ev):
    chat = await admin_client.get_entity(ev.chat_id)
    title = getattr(chat, 'title', None) or str(ev.chat_id)
    header = f"📢 *{title}* (`{ev.chat_id}`)"
    await admin_client.send_message(DEST_CHAT_ID, header, parse_mode='Markdown')

    # tenta forward_to e cai no fallback
    try:
        await ev.message.forward_to(DEST_CHAT_ID)
    except Exception:
        # fallback manual: texto + mídia
        msg = ev.message
        if msg.media:
            await admin_client.send_file(
                DEST_CHAT_ID,
                file=msg.media,
                caption=msg.text or ''
            )
        else:
            await admin_client.send_message(
                DEST_CHAT_ID,
                msg.text or ''
            )

# ── Gerencia TelethonClient de cada usuário ───────────────────────────────────
user_clients = {}

async def ensure_client(uid):
    key = str(uid)
    if key in user_clients:
        return user_clients[key]
    sess = sessions.get(key)
    if not isinstance(sess, str):
        return None
    client = TelegramClient(StringSession(sess), API_ID, API_HASH)
    await client.start()
    user_clients[key] = client

    @client.on(events.NewMessage)
    async def _(ev):
        if ev.chat_id in subscriptions.get(key, []):
            chat = await client.get_entity(ev.chat_id)
            title = getattr(chat, 'title', None) or str(ev.chat_id)
            header = f"📢 *{title}* (`{ev.chat_id}`)"
            await bot.send_message(DEST_CHAT_ID, header, parse_mode='Markdown')

            # fallback similar para dynamic
            try:
                await ev.message.forward_to(DEST_CHAT_ID)
            except Exception:
                msg = ev.message
                if msg.media:
                    await bot.send_file(
                        DEST_CHAT_ID,
                        file=msg.media,
                        caption=msg.text or ''
                    )
                else:
                    await bot.send_message(DEST_CHAT_ID, msg.text or '')

            # clonar comentários da thread
            try:
                full = await client(GetFullChannelRequest(channel=ev.chat_id))
                linked = getattr(full.full_chat, 'linked_chat_id', None)
                if linked:
                    comments = await client.get_messages(linked, limit=20)
                    for cm in comments:
                        cm_header = f"💬 Comentário de {title} (`{linked}`)"
                        await bot.send_message(DEST_CHAT_ID, cm_header, parse_mode='Markdown')
                        await bot.send_file(
                            DEST_CHAT_ID,
                            file=cm.media or b'',
                            caption=cm.text or ''
                        )
            except:
                pass

    asyncio.create_task(client.run_until_disconnected())
    return client

# ── Execução principal ─────────────────────────────────────────────────────────
async def main():
    threading.Thread(target=run_flask, daemon=True).start()
    await asyncio.gather(
        admin_client.start(),
        bot.start(bot_token=BOT_TOKEN)
    )
    print("🤖 Bots rodando...")
    await asyncio.gather(
        admin_client.run_until_disconnected(),
        bot.run_until_disconnected()
    )

if __name__ == '__main__':
    asyncio.run(main())
