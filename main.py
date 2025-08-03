# main.py

import os, json, ast, asyncio
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ── Configurações via Variáveis de Ambiente ───────────────────────────────────
API_ID          = int(os.environ['TELEGRAM_API_ID'])
API_HASH        = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN       = os.environ['BOT_TOKEN']
DEST_CHAT_ID    = int(os.environ['DEST_CHAT_ID'])
ADMIN_ID        = int(os.environ['ADMIN_ID'])
SESSION_STRING  = os.environ['SESSION_STRING']
SOURCE_CHAT_IDS = ast.literal_eval(os.environ.get('SOURCE_CHAT_IDS', '[]'))
# ex: SOURCE_CHAT_IDS='[-1002460735067, -1002455542600, -1002794084735]'

# ── Arquivos de Persistência ──────────────────────────────────────────────────
SESS_FILE = 'sessions.json'       # { user_id: session_str, ... }
SUBS_FILE = 'subscriptions.json'  # { user_id: [group_id, ...], ... }

def load(fname):
    try:
        return json.load(open(fname, 'r'))
    except:
        return {}

def save(fname, data):
    json.dump(data, open(fname, 'w'), indent=2)

sessions      = load(SESS_FILE)
subscriptions = load(SUBS_FILE)

# ── HTTP keep-alive para Railway ───────────────────────────────────────────────
app = Flask('keep_alive')
@app.route('/')
def home():
    return 'OK'

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

# ── BotFather “bot” para CMDs em DM ────────────────────────────────────────────
bot = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# ── Cliente Telethon do admin (monitor inicial) ───────────────────────────────
admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# quando admin recebe nova mensagem nos canais iniciais, reenviamos
@admin_client.on(events.NewMessage(chats=SOURCE_CHAT_IDS))
async def _(ev):
    await admin_client.send_message(DEST_CHAT_ID, ev.message)

# ── Função auxiliar para criar/recuperar client de cada usuário ──────────────
user_clients = {}

async def ensure_user_client(uid):
    key = str(uid)
    if key in user_clients:
        return user_clients[key]
    sess_str = sessions.get(key)
    if not isinstance(sess_str, str):
        return None
    client = TelegramClient(StringSession(sess_str), API_ID, API_HASH)
    await client.start()
    user_clients[key] = client

    @client.on(events.NewMessage)
    async def _(ev):
        if ev.chat_id in subscriptions.get(key, []):
            # encaminha via bot (não pelo client do usuário!)
            await bot.send_message(DEST_CHAT_ID, ev.message)

    # roda o client em background
    asyncio.create_task(client.run_until_disconnected())
    return client

# ── Handlers de comandos via DM com o bot ─────────────────────────────────────
@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def handler(ev):
    uid   = ev.sender_id
    text  = ev.raw_text.strip()
    reply = ev.reply

    # /start e /help
    if text in ('/start','/help'):
        await reply(
            "**👋 Bem-vindo ao Encaminhador**\n\n"
            "🔗 Para gerar sua StringSession (sem instalar nada), use este Colab:\n"
            "https://colab.research.google.com/drive/1H3vHoNr_8CGW0rLEV-fFKKINo8mHWr5U?usp=sharing\n\n"
            "⚙️ **Fluxo:**\n"
            "1️⃣ `/setsession SUA_STRINGSESSION`\n"
            "2️⃣ `/listgroups` — vê seus grupos e IDs\n"
            "3️⃣ `/subscribe GROUP_ID`\n"
            "4️⃣ `/unsubscribe GROUP_ID`\n\n"
            "📢 **ADMIN**: `/admin_unsub USER_ID GROUP_ID`",
            parse_mode='Markdown'
        )
        return

    # 1) /setsession
    if text.startswith('/setsession '):
        sess = text.split(' ',1)[1].strip()
        sessions[str(uid)] = sess
        save(SESS_FILE, sessions)
        await reply("✅ Session salva! Agora use `/listgroups`.")
        await ensure_user_client(uid)
        return

    # precisa estar autenticado
    client = await ensure_user_client(uid)
    if not client:
        return await reply("❌ Primeiro use `/setsession SUA_STRINGSESSION`.")

    # 2) /listgroups
    if text == '/listgroups':
        dialogs = await client.get_dialogs()
        lines = [f"{d.title or 'Sem título'} — `{d.id}`"
                 for d in dialogs if d.is_group or d.is_channel]
        await reply("📋 *Seus grupos:*\n" + "\n".join(lines[:50]),
                    parse_mode='Markdown')
        return

    # 3) /subscribe
    if text.startswith('/subscribe '):
        try:
            gid = int(text.split(' ',1)[1])
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
            gid = int(text.split(' ',1)[1])
        except:
            return await reply("❌ ID inválido.")
        lst = subscriptions.get(str(uid), [])
        if gid not in lst:
            return await reply("❌ Você não está inscrito.")
        lst.remove(gid)
        save(SUBS_FILE, subscriptions)
        return await reply(f"🗑️ Desinscrito do `{gid}`.")

    # 5) admin forced remove
    if text.startswith('/admin_unsub ') and uid == ADMIN_ID:
        parts = text.split()
        if len(parts)==3:
            tuid, gid = parts[1], int(parts[2])
            lst = subscriptions.get(tuid, [])
            if gid in lst:
                lst.remove(gid)
                save(SUBS_FILE, subscriptions)
                return await reply(f"🔒 Usuário {tuid} removido de `{gid}`.")
        return await reply("❌ Uso: /admin_unsub USER_ID GROUP_ID")

    # fallback
    await reply("❓ Comando não reconhecido. Use `/help`.", parse_mode='Markdown')

# ── Entrada principal ─────────────────────────────────────────────────────────
async def main():
    # 1) Inicia o Flask keep-alive
    threading.Thread(target=run_flask, daemon=True).start()
    # 2) Inicia o admin_client (monitor inicial)
    await admin_client.start()
    # 3) Inicia o bot do BotFather
    await bot.start()
    # 4) Mantém ambos rodando
    await asyncio.gather(
        admin_client.run_until_disconnected(),
        bot.run_until_disconnected()
    )

if __name__ == '__main__':
    import threading
    asyncio.run(main())
