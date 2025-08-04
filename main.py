# main.py
# Encaminhador 100% resiliente: múltiplos fallbacks + persistência em volume + flood-wait handling

import os
import json
import asyncio
import threading
import logging

from flask import Flask, jsonify
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetFullChannelRequest

# ── CONFIGURAÇÃO VIA ENV VARS ────────────────────────────────────────────────
API_ID          = int(os.environ['TELEGRAM_API_ID'])
API_HASH        = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN       = os.environ['BOT_TOKEN']
DEST_CHAT_ID    = int(os.environ['DEST_CHAT_ID'])
SESSION_STRING  = os.environ['SESSION_STRING']
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS', '[]'))

# ADMIN_IDS (pode ser JSON list ou single int)
raw_admins = os.environ.get('ADMIN_IDS', '[]')
try:
    parsed = json.loads(raw_admins)
    if isinstance(parsed, int):
        ADMIN_IDS = {parsed}
    elif isinstance(parsed, list):
        ADMIN_IDS = set(parsed)
    else:
        ADMIN_IDS = set()
except:
    ADMIN_IDS = set()

# Volume persistente (Railway Volume montado em /data)
DATA_DIR   = '/data'
SESS_FILE  = os.path.join(DATA_DIR, 'sessions.json')
SUBS_FILE  = os.path.join(DATA_DIR, 'subscriptions.json')

# ── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── I/O JSON ─────────────────────────────────────────────────────────────────
def load_file(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

sessions      = load_file(SESS_FILE)
subscriptions = load_file(SUBS_FILE)

# ── FLASK KEEP-ALIVE + DUMP SUBSCRIPTIONS ────────────────────────────────────
app = Flask('keep_alive')

@app.route('/')
def home():
    return 'OK'

@app.route('/dump_subs')
def dump_subs():
    return jsonify(subscriptions)

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)

# ── BOTFATHER BOT (admin UI) ─────────────────────────────────────────────────
bot = TelegramClient('bot_session', API_ID, API_HASH)

@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def handler(ev):
    uid, text, reply = ev.sender_id, ev.raw_text.strip(), ev.reply

    # Helper: sleep on FloodWait
    async def handle_flood(exc):
        if isinstance(exc, errors.FloodWaitError):
            wait = exc.seconds + 1
            log.warning(f"FloodWait: sleeping {wait}s")
            await asyncio.sleep(wait)
            return True
        return False

    # — Admin: set_session
    if text.startswith('/admin_set_session '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, sess = text.split(' ', 2)
            sessions[user_id] = sess
            save_file(SESS_FILE, sessions)
            return await reply(f'✅ Session de `{user_id}` registrada.')
        except:
            return await reply('❌ Uso: `/admin_set_session USER_ID SESSION`')

    # — Admin: subscribe
    if text.startswith('/admin_subscribe '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, gid_str = text.split(' ', 2)
            gid = int(gid_str)
            lst = subscriptions.setdefault(user_id, [])
            if gid in lst:
                return await reply('⚠️ Já inscrito.')
            lst.append(gid)
            save_file(SUBS_FILE, subscriptions)
            return await reply(f'✅ `{user_id}` inscrito em `{gid}`.')
        except:
            return await reply('❌ Uso: `/admin_subscribe USER_ID GROUP_ID`')

    # — Admin: unsubscribe
    if text.startswith('/admin_unsubscribe '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, gid_str = text.split(' ', 2)
            gid = int(gid_str)
            lst = subscriptions.get(user_id, [])
            if gid not in lst:
                return await reply('❌ Não inscrito.')
            lst.remove(gid)
            save_file(SUBS_FILE, subscriptions)
            return await reply(f'🗑️ `{user_id}` desinscrito de `{gid}`.')
        except:
            return await reply('❌ Uso: `/admin_unsubscribe USER_ID GROUP_ID`')

    # — Público: /start ou /help
    if text in ('/start', '/help'):
        return await reply(
            "**👋 Bem-vindo ao Encaminhador!**\n\n"
            "1️⃣ `/myid`\n"
            "2️⃣ `/setsession SUA_SESSION`\n"
            "3️⃣ `/listgroups`\n"
            "4️⃣ `/subscribe GROUP_ID`\n"
            "5️⃣ `/unsubscribe GROUP_ID`",
            parse_mode='Markdown'
        )

    # — Público: /myid
    if text == '/myid':
        return await reply(f'🆔 Seu ID: `{uid}`', parse_mode='Markdown')

    # — Público: /setsession
    if text.startswith('/setsession '):
        sess = text.split(' ', 1)[1].strip()
        sessions[str(uid)] = sess
        save_file(SESS_FILE, sessions)
        await reply('✅ Session salva! Agora use `/listgroups`.')
        await ensure_client(uid)
        return

    # Garante que temos um client para esse usuário
    client = await ensure_client(uid)
    if not client:
        return await reply('❌ Use `/setsession SUA_SESSION` antes.')

    # — Público: /listgroups
    if text == '/listgroups':
        dlg = await client.get_dialogs()
        lines = [
            f"{d.title or 'Sem título'} — `{d.id}`"
            for d in dlg if d.is_group or d.is_channel
        ]
        return await reply('📋 *Seus grupos:* \n' + "\n".join(lines[:50]), parse_mode='Markdown')

    # — Público: /subscribe
    if text.startswith('/subscribe '):
        try:
            gid = int(text.split(' ', 1)[1])
        except:
            return await reply('❌ ID inválido.')
        lst = subscriptions.setdefault(str(uid), [])
        if gid in lst:
            return await reply('⚠️ Já inscrito.')
        lst.append(gid)
        save_file(SUBS_FILE, subscriptions)
        return await reply(f'✅ Inscrito em `{gid}`.')

    # — Público: /unsubscribe
    if text.startswith('/unsubscribe '):
        try:
            gid = int(text.split(' ', 1)[1])
        except:
            return await reply('❌ ID inválido.')
        lst = subscriptions.get(str(uid), [])
        if gid not in lst:
            return await reply('❌ Não inscrito.')
        lst.remove(gid)
        save_file(SUBS_FILE, subscriptions)
        return await reply(f'🗑️ Desinscrito de `{gid}`.')

    # — Comando não reconhecido
    return await reply('❓ Comando não reconhecido. `/help`.', parse_mode='Markdown')


# ── CLIENTE “ADMIN” PARA CANAIS FIXOS ─────────────────────────────────────────
admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

@admin_client.on(events.NewMessage(chats=SOURCE_CHAT_IDS))
async def forward_initial(ev):
    m     = ev.message
    chat  = await admin_client.get_entity(ev.chat_id)
    title = getattr(chat, 'title', None) or str(ev.chat_id)

    # Cabeçalho
    await admin_client.send_message(
        DEST_CHAT_ID,
        f"📢 *{title}* (`{ev.chat_id}`)",
        parse_mode='Markdown'
    )

    # 1) forward normal
    try:
        await m.forward_to(DEST_CHAT_ID)
        return
    except Exception as e:
        if isinstance(e, errors.FloodWaitError):
            await asyncio.sleep(e.seconds + 1)

    # 2) download + reenvio de mídia
    if m.media:
        path = await m.download_media()
        try:
            await admin_client.send_file(
                DEST_CHAT_ID,
                path,
                caption=m.text or ''
            )
        except errors.FloodWaitError as fw:
            await asyncio.sleep(fw.seconds + 1)
            await admin_client.send_file(DEST_CHAT_ID, path, caption=m.text or '')
    else:
        await admin_client.send_message(DEST_CHAT_ID, m.text or '')


# ── CLIENTES “USER” PARA CANAIS DINÂMICOS ──────────────────────────────────
user_clients = {}

async def ensure_client(uid):
    key = str(uid)
    if key in user_clients:
        return user_clients[key]

    sess = sessions.get(key)
    if not sess:
        return None

    try:
        cli = TelegramClient(StringSession(sess), API_ID, API_HASH)
    except ValueError:
        sessions.pop(key, None)
        save_file(SESS_FILE, sessions)
        await bot.send_message(uid, '🚫 Session inválida. Use `/setsession`.')
        return None

    await cli.start()
    user_clients[key] = cli

    @cli.on(events.NewMessage)
    async def forward_user(ev):
        if ev.chat_id not in subscriptions.get(key, []):
            return

        m     = ev.message
        chat  = await cli.get_entity(ev.chat_id)
        title = getattr(chat, 'title', None) or str(ev.chat_id)

        # Cabeçalho via bot
        await bot.send_message(
            DEST_CHAT_ID,
            f"📢 *{title}* (`{ev.chat_id}`)",
            parse_mode='Markdown'
        )

        # 1) forward normal
        try:
            await m.forward_to(DEST_CHAT_ID)
            return
        except Exception as e:
            if isinstance(e, errors.FloodWaitError):
                await asyncio.sleep(e.seconds + 1)

        # 2) download + reenvio de mídia
        if m.media:
            path = await m.download_media()
            try:
                await bot.send_file(DEST_CHAT_ID, path, caption=m.text or '')
            except errors.FloodWaitError as fw:
                await asyncio.sleep(fw.seconds + 1)
                await bot.send_file(DEST_CHAT_ID, path, caption=m.text or '')
        else:
            await bot.send_message(DEST_CHAT_ID, m.text or '')

        # 3) clonar comentários em thread vinculada (se houver)
        try:
            full   = await cli(GetFullChannelRequest(channel=ev.chat_id))
            linked = getattr(full.full_chat, 'linked_chat_id', None)
            if linked:
                cms = await cli.get_messages(linked, limit=20)
                for cm in cms:
                    await bot.send_message(
                        DEST_CHAT_ID,
                        f"💬 Comentário de {title} (`{linked}`)",
                        parse_mode='Markdown'
                    )
                    if cm.media:
                        await bot.send_file(DEST_CHAT_ID, cm.media, caption=cm.text or '')
                    else:
                        await bot.send_message(DEST_CHAT_ID, cm.text or '')
        except:
            pass

    # Roda o listener em background
    asyncio.create_task(cli.run_until_disconnected())
    return cli


# ── PONTO DE ENTRADA ─────────────────────────────────────────────────────────
async def main():
    # Inicia Flask em thread separada
    threading.Thread(target=run_flask, daemon=True).start()

    # Inicia o client admin e o bot
    await asyncio.gather(
        admin_client.start(),
        bot.start(bot_token=BOT_TOKEN)
    )
    log.info('🤖 Bots rodando...')

    # Espera desconexão
    await asyncio.gather(
        admin_client.run_until_disconnected(),
        bot.run_until_disconnected()
    )

if __name__ == '__main__':
    asyncio.run(main())
