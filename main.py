import os, json, asyncio, threading
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

# — Configs via ENV —
API_ID       = int(os.environ['TELEGRAM_API_ID'])
API_HASH     = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN    = os.environ['BOT_TOKEN']
DEST_CHAT_ID = int(os.environ['DEST_CHAT_ID'])
ADMIN_ID     = int(os.environ['ADMIN_ID'])

# — Persistência —
SESS_FILE = 'sessions.json'       # { user_id: session_str or dict for temp }
SUBS_FILE = 'subscriptions.json'  # { user_id: [group_id,...] }

def load(fname):
    try:
        return json.load(open(fname, 'r'))
    except:
        return {}

def save(fname, data):
    json.dump(data, open(fname, 'w'), indent=2)

sessions      = load(SESS_FILE)
subscriptions = load(SUBS_FILE)

# — Keep-alive HTTP for Railway —
app = Flask('keep_alive')
@app.route('/')
def home():
    return 'OK'

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

# — Gerenciar TelethonClients dos usuários —
user_clients = {}

async def ensure_user_client(user_id):
    key = str(user_id)
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
            # Encaminha via BOT
            await bot_client.send_message(DEST_CHAT_ID, ev.message)

    asyncio.create_task(client.run_until_disconnected())
    return client

# — BotFather-bot principal —
bot_client = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

@bot_client.on(events.NewMessage(func=lambda e: e.is_private))
async def handler(ev):
    user_id = ev.sender_id
    text    = ev.raw_text.strip()
    reply   = ev.reply

    # /start or /help
    if text in ('/start','/help'):
        return await reply(
            "**🔰 Guia Rápido**\n\n"
            "1️⃣ `/login +55SEUNUMERO`\n"
            "2️⃣ `/code SEUCODIGO`\n"
            "   • Se houver senha, depois use `/password SUA_SENHA`\n"
            "3️⃣ `/listgroups`\n"
            "4️⃣ `/subscribe ID`\n"
            "5️⃣ `/unsubscribe ID`\n\n"
            "Admin: `/admin_unsub USER_ID GROUP_ID`",
            parse_mode='Markdown'
        )

    # 1) /login
    if text.startswith('/login '):
        phone = text.split(' ',1)[1]
        try:
            await bot_client.send_code_request(phone)
            sessions[str(user_id)] = {'_phone': phone}
            save(SESS_FILE, sessions)
            return await reply("📱 Código enviado! Agora `/code SEUCODIGO`.")
        except Exception as e:
            return await reply(f"❌ Erro ao enviar código: {e}")

    # 2) /code
    if text.startswith('/code '):
        part = sessions.get(str(user_id))
        phone = part.get('_phone') if isinstance(part, dict) else None
        if not phone:
            return await reply("❌ Primeiro `/login +55...`.")
        code = text.split(' ',1)[1]
        temp = TelegramClient(StringSession(), API_ID, API_HASH)
        await temp.connect()
        try:
            await temp.sign_in(phone, code)
        except SessionPasswordNeededError:
            # 2FA necessária
            temp_sess = temp.session.save()
            sessions[str(user_id)] = {
                '_need_password': True,
                '_temp_session': temp_sess
            }
            save(SESS_FILE, sessions)
            await reply("🔒 Conta com senha! Envie `/password SUA_SENHA`.")
            return
        except Exception as e:
            return await reply(f"❌ Falha no código: {e}")
        # sem senha, autenticou
        sess_str = temp.session.save()
        sessions[str(user_id)] = sess_str
        save(SESS_FILE, sessions)
        await reply("✅ Autenticado! Use `/listgroups`.")
        await temp.disconnect()
        await ensure_user_client(user_id)
        return

    # 2b) /password
    if text.startswith('/password '):
        part = sessions.get(str(user_id))
        if not isinstance(part, dict) or not part.get('_need_password'):
            return await reply("❌ Sem etapa de senha pendente.")
        pwd = text.split(' ',1)[1]
        sess_str = part.get('_temp_session')
        client = TelegramClient(StringSession(sess_str), API_ID, API_HASH)
        await client.connect()
        try:
            await client.sign_in(password=pwd)
        except Exception as e:
            return await reply(f"❌ Senha incorreta: {e}")
        final = client.session.save()
        sessions[str(user_id)] = final
        save(SESS_FILE, sessions)
        await reply("✅ Autenticado com sucesso!")
        await client.disconnect()
        await ensure_user_client(user_id)
        return

    # precisa estar autenticado
    user_client = await ensure_user_client(user_id)
    if not user_client:
        return await reply("❌ Faça `/login` e `/code` primeiro.")

    # 3) /listgroups
    if text == '/listgroups':
        dialogs = await user_client.get_dialogs()
        lines = [f"{d.title} — `{d.id}`"
                 for d in dialogs if d.is_group or d.is_channel]
        chunk = "\n".join(lines[:50])
        return await reply("📋 *Seus grupos:*\n" + chunk, parse_mode='Markdown')

    # 4) /subscribe
    if text.startswith('/subscribe '):
        try:
            gid = int(text.split(' ',1)[1])
        except:
            return await reply("❌ ID inválido.")
        lst = subscriptions.setdefault(str(user_id), [])
        if gid in lst:
            return await reply("⚠️ Já inscrito.")
        lst.append(gid)
        save(SUBS_FILE, subscriptions)
        return await reply(f"✅ Inscrito: `{gid}`.")

    # 5) /unsubscribe
    if text.startswith('/unsubscribe '):
        try:
            gid = int(text.split(' ',1)[1])
        except:
            return await reply("❌ ID inválido.")
        lst = subscriptions.get(str(user_id), [])
        if gid not in lst:
            return await reply("❌ Não inscrito.")
        lst.remove(gid)
        save(SUBS_FILE, subscriptions)
        return await reply(f"🗑️ Desinscrito: `{gid}`.")

    # 6) admin force
    if text.startswith('/admin_unsub ') and user_id == ADMIN_ID:
        parts = text.split()
        if len(parts)==3:
            uid, gid = parts[1], int(parts[2])
            lst = subscriptions.get(uid, [])
            if gid in lst:
                lst.remove(gid)
                save(SUBS_FILE, subscriptions)
                return await reply(f"🔒 Removido {uid} de `{gid}`.")
        return await reply("❌ Uso: /admin_unsub USER_ID GROUP_ID")

    # fallback
    await reply("❓ Comando inválido. Use /help.", parse_mode='Markdown')

def main():
    threading.Thread(target=run_flask, daemon=True).start()
    print("🤖 BotFather-bot rodando...")
    bot_client.run_until_disconnected()

if __name__ == '__main__':
    main()
