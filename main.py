import os, json, asyncio, threading
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# — Configurações via ENV no Railway —
API_ID       = int(os.environ['TELEGRAM_API_ID'])
API_HASH     = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN    = os.environ['BOT_TOKEN']      # token do BotFather
DEST_CHAT_ID = int(os.environ['DEST_CHAT_ID'])
ADMIN_ID     = int(os.environ['ADMIN_ID'])

# — Arquivos de persistência —
SESS_FILE = 'sessions.json'       # { user_id: session_str, ... }
SUBS_FILE = 'subscriptions.json'  # { user_id: [group_id,...], ... }

def load(fname):
    try:
        return json.load(open(fname, 'r'))
    except:
        return {}

def save(fname, data):
    json.dump(data, open(fname, 'w'), indent=2)

sessions      = load(SESS_FILE)
subscriptions = load(SUBS_FILE)

# — Keep-alive HTTP para Railway —
app = Flask('keep_alive')
@app.route('/')
def home():
    return 'OK'

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

# — Gerenciamento de TelethonClients por usuário —
user_clients = {}

async def ensure_user_client(user_id):
    """Garante que exista e retorne o client Telethon do usuário."""
    key = str(user_id)
    if key in user_clients:
        return user_clients[key]

    sess = sessions.get(key)
    if not sess:
        return None

    client = TelegramClient(StringSession(sess), API_ID, API_HASH)
    await client.start()
    user_clients[key] = client

    # quando chegar mensagem nos grupos subscritos, reenviar via Bot
    @client.on(events.NewMessage)
    async def _(ev):
        for gid in subscriptions.get(key, []):
            if ev.chat_id == gid:
                # envia via BOT para o grupo destino
                await bot_client.send_message(DEST_CHAT_ID, ev.message)

    asyncio.create_task(client.run_until_disconnected())
    return client

# — Bot principal (via BotFather token) —
bot_client = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

@bot_client.on(events.NewMessage(func=lambda e: e.is_private))
async def handler(ev):
    user_id = ev.sender_id
    text    = ev.raw_text.strip()
    reply   = ev.reply

    # 1) /start ou /help
    if text in ('/start','/help'):
        await reply(
            "**🔰 Fluxo de configuração**\n\n"
            "1️⃣ `/login +55SEUNUMERO` — autentica sua conta.\n"
            "2️⃣ `/code SEUCODIGO`    — insere o código SMS.\n"
            "3️⃣ `/listgroups`        — lista nome e ID dos seus grupos.\n"
            "4️⃣ `/subscribe ID`      — começa a encaminhar do grupo.\n"
            "5️⃣ `/unsubscribe ID`    — para de encaminhar.\n\n"
            "🔧 Admin: `/admin_unsub USER_ID GROUP_ID`",
            parse_mode='Markdown'
        )
        return

    # 2) /login +55...
    if text.startswith('/login '):
        phone = text.split(' ',1)[1]
        try:
            await bot_client.send_code_request(phone)
            sessions[str(user_id)] = {'_phone': phone}
            save(SESS_FILE, sessions)
            return await reply("📱 Código enviado! Agora envie `/code SEUCODIGO`.")
        except Exception as e:
            return await reply(f"❌ Erro ao enviar código: {e}")

    # 3) /code 12345
    if text.startswith('/code '):
        info = sessions.get(str(user_id), {})
        phone = info.get('_phone')
        if not phone:
            return await reply("❌ Primeiro faça `/login +55...`.")
        code = text.split(' ',1)[1]
        try:
            temp = TelegramClient(StringSession(), API_ID, API_HASH)
            await temp.connect()
            await temp.sign_in(phone, code)
            sess_str = temp.session.save()
            sessions[str(user_id)] = sess_str
            save(SESS_FILE, sessions)
            await reply("✅ Autenticado! Use `/listgroups` para ver seus grupos.")
            await temp.disconnect()
            await ensure_user_client(user_id)
        except Exception as e:
            return await reply(f"❌ Falha no login: {e}")
        return

    # precisa estar autenticado
    client = await ensure_user_client(user_id)
    if not client:
        return await reply("❌ Use `/login` e `/code` antes.")

    # 4) /listgroups
    if text == '/listgroups':
        dialogs = await client.get_dialogs()
        lines = [f"{d.title} — `{d.id}`" 
                 for d in dialogs 
                 if (d.is_group or d.is_channel)]
        chunk = "\n".join(lines[:50])
        return await reply("📋 *Seus grupos:*\n" + chunk, parse_mode='Markdown')

    # 5) /subscribe ID
    if text.startswith('/subscribe '):
        try:
            gid = int(text.split(' ',1)[1])
        except:
            return await reply("❌ ID inválido.")
        lst = subscriptions.setdefault(str(user_id), [])
        if gid in lst:
            return await reply("⚠️ Já inscrito neste grupo.")
        lst.append(gid)
        save(SUBS_FILE, subscriptions)
        return await reply(f"✅ Inscrito no `{gid}`. Agora tudo vai pra paterra Tips.")

    # 6) /unsubscribe ID
    if text.startswith('/unsubscribe '):
        try:
            gid = int(text.split(' ',1)[1])
        except:
            return await reply("❌ ID inválido.")
        lst = subscriptions.get(str(user_id), [])
        if gid not in lst:
            return await reply("❌ Você não estava inscrito neste grupo.")
        lst.remove(gid)
        save(SUBS_FILE, subscriptions)
        return await reply(f"🗑️ Desinscrito do `{gid}`.")

    # 7) Admin remove forçado
    if text.startswith('/admin_unsub ') and user_id == ADMIN_ID:
        parts = text.split()
        if len(parts)==3:
            uid, gid = parts[1], int(parts[2])
            lst = subscriptions.get(uid, [])
            if gid in lst:
                lst.remove(gid)
                save(SUBS_FILE, subscriptions)
                return await reply(f"🔒 Usuário {uid} removido de `{gid}`.")
        return await reply("❌ Uso: /admin_unsub USER_ID GROUP_ID")

    # fallback
    await reply("❓ Comando não reconhecido. Use /help.", parse_mode='Markdown')

# — Inicia tudo —
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    print("🔄 BotFather-bot rodando (DM config)...")
    bot_client.run_until_disconnected()

if __name__ == '__main__':
    main()
