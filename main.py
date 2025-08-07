import os
import json
import asyncio
import threading
import logging

from flask import Flask, jsonify
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession

# ── CONFIGURAÇÃO VIA ENV ─────────────────────────────────────────────────────
API_ID          = int(os.environ['TELEGRAM_API_ID'])
API_HASH        = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN       = os.environ['BOT_TOKEN']
DEST_CHAT_ID    = int(os.environ['DEST_CHAT_ID'])
SESSION_STRING  = os.environ['SESSION_STRING']
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS','[]'))

_raw_admins = os.environ.get('ADMIN_IDS','[]')
try:
    parsed = json.loads(_raw_admins)
    ADMIN_IDS = {parsed} if isinstance(parsed,int) else set(parsed)
except:
    ADMIN_IDS = set()

# ── ARQUIVOS EM /data ─────────────────────────────────────────────────────────
DATA_DIR      = '/data'
SESS_FILE     = os.path.join(DATA_DIR, 'sessions.json')
SUBS_FILE     = os.path.join(DATA_DIR, 'subscriptions.json')

# ── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('encaminhador')

# ── I/O JSON ─────────────────────────────────────────────────────────────────
def load_file(path, default):
    try:
        with open(path,'r',encoding='utf-8') as f:
            return json.load(f)
    except:
        return default

def save_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path,'w',encoding='utf-8') as f:
        json.dump(data, f, indent=2)

sessions      = load_file(SESS_FILE, {})
subscriptions = load_file(SUBS_FILE, {})

# ── FLASK KEEP-ALIVE + DUMP ──────────────────────────────────────────────────
app = Flask('keep_alive')
@app.route('/')
def home(): return 'OK'
@app.route('/dump_subs')
def dump_subs(): return jsonify(subscriptions)

def run_flask():
    app.run(host='0.0.0.0',
            port=int(os.environ.get('PORT',5000)),
            debug=False)

# ── BOTFATHER BOT (UI + ADMIN) ───────────────────────────────────────────────
bot = TelegramClient('bot_session', API_ID, API_HASH)

@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def ui_handler(ev):
    uid, txt, reply = ev.sender_id, ev.raw_text.strip(), ev.reply

    # flood-wait helper
    async def handle_flood(e):
        if isinstance(e, errors.FloodWaitError):
            await asyncio.sleep(e.seconds+1)
            return True
        return False

    # ---- ADMIN_SET_SESSION ----
    if txt.startswith('/admin_set_session '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, sess = txt.split(' ',2)
            sessions[user_id] = sess
            save_file(SESS_FILE, sessions)
            await reply(f'✅ Session de `{user_id}` registrada.')
            # sobe o listener imediatamente
            await ensure_client(int(user_id))
        except:
            await reply('❌ Uso: `/admin_set_session USER_ID SESSION`')
        return

    # ---- ADMIN_SUBSCRIBE ----
    if txt.startswith('/admin_subscribe '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, gid_s = txt.split(' ',2)
            gid = int(gid_s)
            lst = subscriptions.setdefault(user_id, [])
            if gid in lst:
                await reply('⚠️ Já inscrito.')
            else:
                lst.append(gid)
                save_file(SUBS_FILE, subscriptions)
                await reply(f'✅ `{user_id}` inscrito em `{gid}`.')
                # sobe o listener imediatamente
                await ensure_client(int(user_id))
        except:
            await reply('❌ Uso: `/admin_subscribe USER_ID GROUP_ID`')
        return

    # ---- ADMIN_UNSUBSCRIBE ----
    if txt.startswith('/admin_unsubscribe '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, gid_s = txt.split(' ',2)
            gid = int(gid_s)
            lst = subscriptions.get(user_id, [])
            if gid not in lst:
                await reply('❌ Não inscrito.')
            else:
                lst.remove(gid)
                save_file(SUBS_FILE, subscriptions)
                await reply(f'🗑️ `{user_id}` desinscrito de `{gid}`.')
        except:
            await reply('❌ Uso: `/admin_unsubscribe USER_ID GROUP_ID`')
        return

    # ---- HELP / START ----
    if txt in ('/start','/help'):
        return await reply(
            "👋 *Bem-vindo ao Encaminhador!*\n\n"
            "1️⃣ `/myid`\n"
            "2️⃣ `/setsession SUA_SESSION`\n"
            "3️⃣ `/listgroups`\n"
            "4️⃣ `/subscribe GROUP_ID`\n"
            "5️⃣ `/unsubscribe GROUP_ID`\n\n"
            "⚙️ Admin: `/admin_set_session`, `/admin_subscribe`, "
            "`/admin_unsubscribe`",
            parse_mode='Markdown'
        )

    # ---- MYID ----
    if txt == '/myid':
        return await reply(f'🆔 Seu ID: `{uid}`', parse_mode='Markdown')

    # ---- SETSESSION (usuário) ----
    if txt.startswith('/setsession '):
        sess = txt.split(' ',1)[1].strip()
        sessions[str(uid)] = sess
        save_file(SESS_FILE, sessions)
        await reply('✅ Session salva! Agora use `/listgroups`.')
        await ensure_client(uid)
        return

    # garantir client do usuário
    client = await ensure_client(uid)
    if not client:
        return await reply('❌ Use `/setsession SUA_SESSION` antes.')

    # ---- LISTGROUPS ----
    if txt == '/listgroups':
        diags = await client.get_dialogs()
        lines = []
        for d in diags:
            if getattr(d.entity, 'megagroup', False) or getattr(d.entity, 'broadcast', False):
                lines.append(f"- `{d.id}` — {d.title or 'Sem título'}")
        payload = "📋 *Seus grupos:*\n" + "\n".join(lines[:50])
        return await reply(payload, parse_mode='Markdown')

    # ---- SUBSCRIBE (usuário) ----
    if txt.startswith('/subscribe '):
        try:
            gid = int(txt.split(' ',1)[1])
        except:
            return await reply('❌ ID inválido.')
        key = str(uid)
        lst = subscriptions.setdefault(key, [])
        if gid in lst:
            return await reply('⚠️ Já inscrito.')
        lst.append(gid)
        save_file(SUBS_FILE, subscriptions)
        await reply(f'✅ Inscrito em `{gid}`.')
        await ensure_client(uid)
        return

    # ---- UNSUBSCRIBE (usuário) ----
    if txt.startswith('/unsubscribe '):
        try:
            gid = int(txt.split(' ',1)[1])
        except:
            return await reply('❌ ID inválido.')
        key = str(uid)
        lst = subscriptions.get(key, [])
        if gid not in lst:
            return await reply('❌ Não inscrito.')
        lst.remove(gid)
        save_file(SUBS_FILE, subscriptions)
        return await reply(f'🗑️ Desinscrito de `{gid}`.')

    # ---- UNKNOWN ----
    return await reply('❓ Comando não reconhecido. `/help`.', parse_mode='Markdown')


# ── ADMIN_CLIENT (fixos) ─────────────────────────────────────────────────────
admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

@admin_client.on(events.NewMessage)
async def forward_initial(ev):
    cid = ev.chat_id
    log.info(f"🔍 [fixed] got message in fixed chat={cid}")
    if cid not in SOURCE_CHAT_IDS:
        return

    m     = ev.message
    chat  = await admin_client.get_entity(cid)
    title = getattr(chat, 'title', None) or str(cid)

    # cabeçalho
    await admin_client.send_message(
        DEST_CHAT_ID,
        f"🏷️ *{title}* (`{cid}`)",
        parse_mode='Markdown'
    )

    # 1) forward
    try:
        return await m.forward_to(DEST_CHAT_ID)
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds+1)
    except Exception as e:
        log.exception(e)

    # 2) fallback download+send
    try:
        if m.media:
            path = await m.download_media()
            await admin_client.send_file(DEST_CHAT_ID, path, caption=m.text or '')
        else:
            await admin_client.send_message(DEST_CHAT_ID, m.text or '')
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds+1)
    except Exception as e:
        log.exception(e)


# ── DYNAMIC USER_CLIENTS ────────────────────────────────────────────────────
user_clients = {}

async def ensure_client(uid:int):
    key = str(uid)
    sess = sessions.get(key)
    if not sess:
        return None

    if key in user_clients:
        return user_clients[key]

    # cria novo
    cli = TelegramClient(StringSession(sess), API_ID, API_HASH)
    await cli.start()
    user_clients[key] = cli

    @cli.on(events.NewMessage)
    async def forward_user(ev):
        log.info(f"🔍 [dynamic] user={key} got message from chat={ev.chat_id}")
        if ev.chat_id not in subscriptions.get(key, []):
            return

        m     = ev.message
        chat  = await cli.get_entity(ev.chat_id)
        title = getattr(chat, 'title', None) or str(ev.chat_id)

        # cabeçalho
        await bot.send_message(
            DEST_CHAT_ID,
            f"📢 *{title}* (`{ev.chat_id}`)",
            parse_mode='Markdown'
        )

        # 1) forward
        try:
            return await m.forward_to(DEST_CHAT_ID)
        except errors.FloodWaitError as e:
            await asyncio.sleep(e.seconds+1)
        except Exception as e:
            log.exception(e)

        # 2) fallback
        try:
            if m.media:
                path = await m.download_media()
                await bot.send_file(DEST_CHAT_ID, path, caption=m.text or '')
            else:
                await bot.send_message(DEST_CHAT_ID, m.text or '')
        except errors.FloodWaitError as e:
            await asyncio.sleep(e.seconds+1)
        except Exception as e:
            log.exception(e)

    asyncio.create_task(cli.run_until_disconnected())
    return cli


# ── ENTRYPOINT ───────────────────────────────────────────────────────────────
async def main():
    # 1) inicia Flask
    threading.Thread(target=run_flask, daemon=True).start()

    # 2) inicia clients
    await asyncio.gather(
        admin_client.start(),
        bot.start(bot_token=BOT_TOKEN)
    )

    # 3) no startup, sobe listeners para **todos**:
    #    - cada user_id em sessions
    #    - cada user_id em subscriptions
    for uid_str in set(list(sessions.keys()) + list(subscriptions.keys())):
        try:
            await ensure_client(int(uid_str))
        except Exception:
            log.exception(f"falha ao iniciar listener {uid_str}")

    log.info('🤖 Bots rodando...')
    # 4) mantém vivo
    await asyncio.gather(
        admin_client.run_until_disconnected(),
        bot.run_until_disconnected()
    )

if __name__ == '__main__':
    asyncio.run(main())
