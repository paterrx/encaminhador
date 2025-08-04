# main.py
# Bot de encaminhamento com fallback para grupos que desativaram forward

import os
import json
import asyncio
import threading
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetFullChannelRequest

# ── Configuração via variáveis de ambiente ───────────────────────────────────
API_ID          = int(os.environ['TELEGRAM_API_ID'])
API_HASH        = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN       = os.environ['BOT_TOKEN']
DEST_CHAT_ID    = int(os.environ['DEST_CHAT_ID'])
SESSION_STRING  = os.environ['SESSION_STRING']
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS', '[]'))
# Exemplo: SOURCE_CHAT_IDS='[-1002460735067,-1002455542600,-1002794084735]'

# ── Arquivos de persistência ─────────────────────────────────────────────────
SESS_FILE = 'sessions.json'       # { user_id: session_str, ... }
SUBS_FILE = 'subscriptions.json'  # { user_id: [group_id, ...], ... }

def load(fname):
    try:
        with open(fname, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save(fname, data):
    with open(fname, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

sessions      = load(SESS_FILE)
subscriptions = load(SUBS_FILE)

# ── Flask keep-alive para Railway ─────────────────────────────────────────────
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

    # /start & /help
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

    # /setsession
    if text.startswith('/setsession '):
        sess = text.split(' ', 1)[1].strip()
        sessions[str(uid)] = sess
        save(SESS_FILE, sessions)
        await reply("✅ Session salva! Agora use `/listgroups`.")
        await ensure_client(uid)
        return

    # Verifica se usuário autenticado
    client = await ensure_client(uid)
    if not client:
        return await reply("❌ Primeiro use `/setsession SUA_SESSION`.")

    # /listgroups
    if text == '/listgroups':
        dialogs = await client.get_dialogs()
        lines = [
            f"{d.title or 'Sem título'} — `{d.id}`"
            for d in dialogs if d.is_group or d.is_channel
        ]
        msg = "📋 *Seus grupos:*\n" + "\n".join(lines[:50])
        await reply(msg, parse_mode='Markdown')
        return

    # /subscribe
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

    # /unsubscribe
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

    # Comando não reconhecido
    await reply("❓ Comando não reconhecido. Use `/help`.", parse_mode='Markdown')

# ── Admin client para canais iniciais ─────────────────────────────────────────
admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

@admin_client.on(events.NewMessage(chats=SOURCE_CHAT_IDS))
async def forward_initial(ev):
    # Cabeçalho
    chat = await admin_client.get_entity(ev.chat_id)
    title = getattr(chat, 'title', None) or str(ev.chat_id)
    header = f"📢 *{title}* (`{ev.chat_id}`)"
    await admin_client.send_message(DEST_CHAT_ID, header, parse_mode='Markdown')

    # Tenta forward_to, senão fallback manual
    msg = ev.message
    try:
        await msg.forward_to(DEST_CHAT_ID)
    except:
        if msg.media:
            await admin_client.send_file(DEST_CHAT_ID, msg.media, caption=msg.text or '')
        else:
            await admin_client.send_message(DEST_CHAT_ID, msg.text or '')

# ── Gerencia um client por usuário ───────────────────────────────────────────
user_clients = {}

async def ensure_client(uid):
    key = str(uid)
    if key in user_clients:
        return user_clients[key]
    sess = sessions.get(key)
    if not sess:
        return None
    client = TelegramClient(StringSession(sess), API_ID, API_HASH)
    await client.start()
    user_clients[key] = client

    @client.on(events.NewMessage)
    async def forward_user(ev):
        if ev.chat_id in subscriptions.get(key, []):
            chat = await client.get_entity(ev.chat_id)
            title = getattr(chat, 'title', None) or str(ev.chat_id)
            header = f"📢 *{title}* (`{ev.chat_id}`)"
            await bot.send_message(DEST_CHAT_ID, header, parse_mode='Markdown')
            # forward ou fallback
            msg = ev.message
            try:
                await msg.forward_to(DEST_CHAT_ID)
            except:
                if msg.media:
                    await bot.send_file(DEST_CHAT_ID, msg.media, caption=msg.text or '')
                else:
                    await bot.send_message(DEST_CHAT_ID, msg.text or '')
            # clona comentários da thread
            try:
                full = await client(GetFullChannelRequest(channel=ev.chat_id))
                linked = getattr(full.full_chat, 'linked_chat_id', None)
                if linked:
                    comments = await client.get_messages(linked, limit=20)
                    for cm in comments:
                        cm_header = f"💬 Comentário de {title} (`{linked}`)"
                        await bot.send_message(DEST_CHAT_ID, cm_header, parse_mode='Markdown')
                        if cm.media:
                            await bot.send_file(DEST_CHAT_ID, cm.media, caption=cm.text or '')
                        else:
                            await bot.send_message(DEST_CHAT_ID, cm.text or '')
            except:
                pass

    asyncio.create_task(client.run_until_disconnected())
    return client

# ── Entrada principal ─────────────────────────────────────────────────────────
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
