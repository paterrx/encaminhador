# main.py
import os
import json
import asyncio
import threading
import logging
from datetime import datetime

from flask import Flask, jsonify
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession

# ── CONFIG VIA ENV VARS ──────────────────────────────────────────────────────
API_ID         = int(os.environ['TELEGRAM_API_ID'])
API_HASH       = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN      = os.environ['BOT_TOKEN']
DEST_CHAT_ID   = int(os.environ['DEST_CHAT_ID'])
SESSION_STRING = os.environ['SESSION_STRING']
# lista de chat_ids fixos (JSON array)
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS', '[]'))

# ADMIN_IDS (pode ser um número ou lista JSON)
_raw = os.environ.get('ADMIN_IDS', '[]')
try:
    tmp = json.loads(_raw)
    if isinstance(tmp, int):
        ADMIN_IDS = {tmp}
    elif isinstance(tmp, list):
        ADMIN_IDS = set(tmp)
    else:
        ADMIN_IDS = set()
except:
    ADMIN_IDS = set()

# persistência
DATA_DIR    = '/data'
SESS_FILE   = os.path.join(DATA_DIR, 'sessions.json')
SUBS_FILE   = os.path.join(DATA_DIR, 'subscriptions.json')
AUDIT_FILE  = os.path.join(DATA_DIR, 'audit.json')

# ── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('encaminhador')

# ── I/O JSON ─────────────────────────────────────────────────────────────────
def load_file(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return default

def save_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

sessions      = load_file(SESS_FILE, {})
subscriptions = load_file(SUBS_FILE, {})
audit_trail   = load_file(AUDIT_FILE, [])

def log_audit(event):
    audit_trail.append(event)
    # manter apenas últimos 200 registros
    del audit_trail[:-200]
    save_file(AUDIT_FILE, audit_trail)

# ── FLASK KEEP-ALIVE + DUMP ───────────────────────────────────────────────────
app = Flask('keep_alive')

@app.route('/')
def home():
    return 'OK'

@app.route('/dump_subs')
def dump_subs():
    return jsonify(subscriptions)

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)

# ── INSTÂNCIA DO BOTFATHER (interface admin/usuário) ─────────────────────────
bot = TelegramClient('bot_session', API_ID, API_HASH)

@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def ui_handler(ev):
    uid    = ev.sender_id
    txt    = ev.raw_text.strip()
    reply  = ev.reply

    # helper flood
    async def handle_flood(e):
        if isinstance(e, errors.FloodWaitError):
            await asyncio.sleep(e.seconds + 1)
            return True
        return False

    # 1) ADMIN: /admin_set_session USER_ID SESSION
    if txt.startswith('/admin_set_session '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Você não é admin.')
        try:
            _, user_id, sess = txt.split(' ',2)
            sessions[user_id] = sess
            save_file(SESS_FILE, sessions)
            return await reply(f'✅ Session de `{user_id}` registrada.', parse_mode='Markdown')
        except:
            return await reply('❌ Uso: `/admin_set_session USER_ID SESSION`')

    # 2) ADMIN: /admin_subscribe USER_ID GROUP_ID
    if txt.startswith('/admin_subscribe '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Você não é admin.')
        try:
            _, user_id, gid_s = txt.split(' ',2)
            gid = int(gid_s)
            lst = subscriptions.setdefault(user_id, [])
            if gid in lst:
                return await reply('⚠️ Já inscrito.')
            lst.append(gid)
            save_file(SUBS_FILE, subscriptions)
            return await reply(f'✅ `{user_id}` inscrito em `{gid}`.', parse_mode='Markdown')
        except:
            return await reply('❌ Uso: `/admin_subscribe USER_ID GROUP_ID`')

    # 3) ADMIN: /admin_unsubscribe USER_ID GROUP_ID
    if txt.startswith('/admin_unsubscribe '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Você não é admin.')
        try:
            _, user_id, gid_s = txt.split(' ',2)
            gid = int(gid_s)
            lst = subscriptions.get(user_id, [])
            if gid not in lst:
                return await reply('❌ Não estava inscrito.')
            lst.remove(gid)
            save_file(SUBS_FILE, subscriptions)
            return await reply(f'🗑️ `{user_id}` desinscrito de `{gid}`.', parse_mode='Markdown')
        except:
            return await reply('❌ Uso: `/admin_unsubscribe USER_ID GROUP_ID`')

    # 4) PÚBLICO: /start ou /help
    if txt in ('/start','/help'):
        return await reply(
            "**👋 Bem-vindo ao Encaminhador!**\n\n"
            "1️⃣ `/myid`\n"
            "2️⃣ `/setsession SUA_SESSION`\n"
            "3️⃣ `/listgroups`\n"
            "4️⃣ `/subscribe GROUP_ID`\n"
            "5️⃣ `/unsubscribe GROUP_ID`\n\n"
            "⚙️ Admin: `/admin_set_session`, `/admin_subscribe`, `/admin_unsubscribe`",
            parse_mode='Markdown'
        )

    # 5) /myid
    if txt == '/myid':
        return await reply(f'🆔 Seu ID: `{uid}`', parse_mode='Markdown')

    # 6) /setsession
    if txt.startswith('/setsession '):
        sess = txt.split(' ',1)[1].strip()
        sessions[str(uid)] = sess
        save_file(SESS_FILE, sessions)
        await reply('✅ Session salva! Agora use `/listgroups`.', parse_mode='Markdown')
        await ensure_client(uid)
        return

    # garante o client do usuário
    client = await ensure_client(uid)
    if not client:
        return await reply('❌ Use `/setsession SUA_SESSION` primeiro.')

    # 7) /listgroups
    if txt == '/listgroups':
        dialogs = await client.get_dialogs()
        lines = []
        for d in dialogs:
            if getattr(d.entity, 'megagroup', False) or getattr(d.entity, 'broadcast', False):
                lines.append(f"- {d.title or '<sem título>'} — `{d.id}`")
        payload = "📋 *Seus grupos:*\n" + "\n".join(lines[:50])
        return await reply(payload, parse_mode='Markdown')

    # 8) /subscribe
    if txt.startswith('/subscribe '):
        try:
            gid = int(txt.split(' ',1)[1])
        except:
            return await reply('❌ Grupo inválido.')
        uid_s = str(uid)
        lst = subscriptions.setdefault(uid_s, [])
        if gid in lst:
            return await reply('⚠️ Já inscrito.')
        lst.append(gid)
        save_file(SUBS_FILE, subscriptions)
        return await reply(f'✅ Inscrito em `{gid}`.', parse_mode='Markdown')

    # 9) /unsubscribe
    if txt.startswith('/unsubscribe '):
        try:
            gid = int(txt.split(' ',1)[1])
        except:
            return await reply('❌ Grupo inválido.')
        uid_s = str(uid)
        lst = subscriptions.get(uid_s, [])
        if gid not in lst:
            return await reply('❌ Não inscrito.')
        lst.remove(gid)
        save_file(SUBS_FILE, subscriptions)
        return await reply(f'🗑️ Desinscrito de `{gid}`.', parse_mode='Markdown')

    # comando desconhecido
    return await reply('❓ Comando não reconhecido. Use `/help`.', parse_mode='Markdown')

# ── CLIENTE “ADMIN” PARA CANAIS FIXOS ─────────────────────────────────────────
admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

@admin_client.on(events.NewMessage)
async def forward_fixed(ev):
    cid = ev.chat_id
    if cid not in SOURCE_CHAT_IDS:
        return
    m     = ev.message
    chat  = await admin_client.get_entity(cid)
    title = getattr(chat, 'title', None) or str(cid)

    # cabeçalho
    header = f"📣 *{title}* (`{cid}`)"
    await admin_client.send_message(DEST_CHAT_ID, header, parse_mode='Markdown')

    # tentativa 1: forward
    try:
        await m.forward_to(DEST_CHAT_ID)
        log_audit({'time':datetime.utcnow().isoformat(),'type':'fixed','chat':cid,'status':'forwarded'})
        return
    except Exception as e:
        await handle_flood_wait(e)

    # tentativa 2: download + reenvio
    try:
        if m.media:
            path = await m.download_media()
            await admin_client.send_file(DEST_CHAT_ID, path, caption=m.text or '')
        else:
            await admin_client.send_message(DEST_CHAT_ID, m.text or '')
        log_audit({'time':datetime.utcnow().isoformat(),'type':'fixed','chat':cid,'status':'downloaded'})
        return
    except Exception as e:
        await handle_flood_wait(e)

    # tudo falhou → alerta
    msg = f"🚨 Falha ao encaminhar de *{title}* (`{cid}`) em {datetime.now().strftime('%H:%M:%S')}"
    await admin_client.send_message(DEST_CHAT_ID, msg, parse_mode='Markdown')
    log_audit({'time':datetime.utcnow().isoformat(),'type':'fixed','chat':cid,'status':'failed'})

# ── CLIENTES “USER” PARA CANAIS DINÂMICOS ──────────────────────────────────
user_clients = {}

async def ensure_client(uid):
    key = str(uid)
    if key in user_clients:
        return user_clients[key]

    # busca session
    sess = sessions.get(key)
    if not sess:
        return None

    try:
        cli = TelegramClient(StringSession(sess), API_ID, API_HASH)
    except ValueError:
        sessions.pop(key,None)
        save_file(SESS_FILE, sessions)
        await bot.send_message(uid, '🚫 Session inválida. Use `/setsession`.')
        return None

    await cli.start()
    user_clients[key] = cli

    @cli.on(events.NewMessage)
    async def forward_dynamic(ev):
        if ev.chat_id not in subscriptions.get(key, []):
            return
        m     = ev.message
        chat  = await cli.get_entity(ev.chat_id)
        title = getattr(chat, 'title', None) or str(ev.chat_id)

        # cabeçalho
        header = f"📢 *{title}* (`{ev.chat_id}`)"
        await bot.send_message(DEST_CHAT_ID, header, parse_mode='Markdown')

        # tentativa 1: forward
        try:
            await m.forward_to(DEST_CHAT_ID)
            log_audit({'time':datetime.utcnow().isoformat(),'type':'dynamic','chat':ev.chat_id,'status':'forwarded'})
            return
        except Exception as e:
            await handle_flood_wait(e)

        # tentativa 2: download + reenvio
        try:
            if m.media:
                path = await m.download_media()
                await bot.send_file(DEST_CHAT_ID, path, caption=m.text or '')
            else:
                await bot.send_message(DEST_CHAT_ID, m.text or '')
            log_audit({'time':datetime.utcnow().isoformat(),'type':'dynamic','chat':ev.chat_id,'status':'downloaded'})
            return
        except Exception as e:
            await handle_flood_wait(e)

        # tudo falhou → alerta
        fail_msg = (f"🚨 Falha ao encaminhar de *{title}* "
                    f"(`{ev.chat_id}`) em {datetime.now().strftime('%H:%M:%S')}")
        await bot.send_message(DEST_CHAT_ID, fail_msg, parse_mode='Markdown')
        log_audit({'time':datetime.utcnow().isoformat(),'type':'dynamic','chat':ev.chat_id,'status':'failed'})

    # mantém rodando no background
    asyncio.create_task(cli.run_until_disconnected())
    return cli

async def handle_flood_wait(e):
    if isinstance(e, errors.FloodWaitError):
        await asyncio.sleep(e.seconds + 1)

# ── ENTRYPOINT ─────────────────────────────────────────────────────────────
async def main():
    # 1) Flask em thread
    threading.Thread(target=run_flask, daemon=True).start()
    # 2) inicia admin_client e bot
    await asyncio.gather(
        admin_client.start(),
        bot.start(bot_token=BOT_TOKEN)
    )
    log.info('🤖 Bots rodando...')
    # 3) mantém ambos vivos
    await asyncio.gather(
        admin_client.run_until_disconnected(),
        bot.run_until_disconnected()
    )

if __name__ == '__main__':
    asyncio.run(main())
