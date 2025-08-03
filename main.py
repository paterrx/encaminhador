# main.py

import os, json, asyncio, threading
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ── Configurações (ENV vars) ───────────────────────────────────────────────────
API_ID          = int(os.environ['TELEGRAM_API_ID'])
API_HASH        = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN       = os.environ['BOT_TOKEN']
DEST_CHAT_ID    = int(os.environ['DEST_CHAT_ID'])
ADMIN_ID        = int(os.environ['ADMIN_ID'])
SESSION_STRING  = os.environ['SESSION_STRING']
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS', '[]'))
# Exemplo: SOURCE_CHAT_IDS='[-1002460735067,-1002455542600,-1002794084735]'

# ── Persistência ────────────────────────────────────────────────────────────────
SESS_FILE = 'sessions.json'       # { user_id: session_str, ... }
SUBS_FILE = 'subscriptions.json'  # { user_id: [group_id,...], ... }

def load(fname):
    try:
        return json.load(open(fname, 'r'))
    except:
        return {}

def save(fname, data):
    with open(fname, 'w') as f:
        json.dump(data, f, indent=2)

sessions      = load(SESS_FILE)
subscriptions = load(SUBS_FILE)

# ── Flask keep-alive (Railway) ──────────────────────────────────────────────────
app = Flask('keep_alive')
@app.route('/')
def home():
    return 'OK'

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

# ── BotFather userbot ───────────────────────────────────────────────────────────
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
            "🔗 Para gerar sua Session (online):\n"
            "<https://colab.research.google.com/drive/1H3vHoNr_8CGW0rLEV-fFKKINo8uHWr5U?usp=sharing>\n\n"
            "**Fluxo:**\n"
            "1️⃣ `/setsession SUA_SESSION`\n"
            "2️⃣ `/listgroups`\n"
            "3️⃣ `/subscribe GROUP_ID`\n"
            "4️⃣ `/unsubscribe GROUP_ID`\n\n"
            "🛠 Admin: `/admin_unsub USER_ID GROUP_ID`",
            parse_mode='Markdown'
        )
        return

    # 1) /setsession
    if text.startswith('/setsession '):
        sess = text.split(' ', 1)[1].strip()
        sessions[str(uid)] = sess
        save(SESS_FILE, sessions)
        await reply("✅ Session salva! Agora use `/listgroups`.")
        await ensure_client(uid)
        return

    # precisa estar autenticado
    client = await ensure_client(uid)
    if not client:
        return await reply("❌ Primeiro use `/setsession SUA_SESSION`.")

    # 2) /listgroups
    if text == '/listgroups':
        dialogs = await client.get_dialogs()
        lines = [
            f"{d.title or 'Sem título'} — `{d.id}`"
            for d in dialogs
            if d.is_group or d.is_channel
        ]
        await reply(
            "📋 *Seus grupos:*\n" + "\n".join(lines[:50]),
            parse_mode='Markdown'
        )
        return

    # 3) /subscribe
    if text.startswith('/subscribe '):
        try:
            gid = int(text.split(' ', 1)[1])
        except:
            return await reply("❌ ID inválido.")
        lst = subscriptions.setdefault(str(uid), [])
        if gid in lst:
            return await reply("⚠️ Já inscrito.")
        lst.append(gid)
        save(SUBS_FILE, subscriptions)
        return await reply(f"✅ Inscrito no `{gid}`.")

    # 4) /unsubscribe
    if text.startswith('/unsubscribe '):
        try:
            gid = int(text.split(' ', 1)[1])
        except:
            return await reply("❌ ID inválido.")
        lst = subscriptions.get(str(uid), [])
        if gid not in lst:
            return await reply("❌ Você não está inscrito.")
        lst.remove(gid)
        save(SUBS_FILE, subscriptions)
        return await reply(f"🗑️ Desinscrito do `{gid}`.")

    # 5) /admin_unsub
    if text.startswith('/admin_unsub ') and uid == ADMIN_ID:
        parts = text.split()
        if len(parts) == 3:
            tuid, gid = parts[1], int(parts[2])
            lst = subscriptions.get(tuid, [])
            if gid in lst:
                lst.remove(gid)
                save(SUBS_FILE, subscriptions)
                return await reply(f"🔒 Usuário {tuid} removido de `{gid}`.")
        return await reply("❌ Uso: /admin_unsub USER_ID GROUP_ID")

    # fallback
    await reply("❓ Comando não reconhecido. Use `/help`.", parse_mode='Markdown')

# ── Cliente admin para os 3 canais iniciais ─────────────────────────────────────
admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

@admin_client.on(events.NewMessage(chats=SOURCE_CHAT_IDS))
async def forward_initial(ev):
    await admin_client.send_message(DEST_CHAT_ID, ev.message)

# ── Gerencia TelethonClient de cada usuário ────────────────────────────────────
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
            await bot.send_message(DEST_CHAT_ID, ev.message)

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
