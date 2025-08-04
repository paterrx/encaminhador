# main.py
import os, json, asyncio, threading, logging
from flask import Flask, jsonify
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession

# ── CONFIG VIA ENV ─────────────────────────────────────────────────────────
API_ID          = int(os.environ['TELEGRAM_API_ID'])
API_HASH        = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN       = os.environ['BOT_TOKEN']
DEST_CHAT_ID    = int(os.environ['DEST_CHAT_ID'])
# IDs puros, sem o -100 prefixo
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS', '[]'))
raw_admins      = os.environ.get('ADMIN_IDS', '[]')
try:
    parsed = json.loads(raw_admins)
    ADMIN_IDS = {parsed} if isinstance(parsed, int) else set(parsed)
except:
    ADMIN_IDS = set()

# Volume persistente
DATA_DIR   = '/data'
SESS_FILE  = os.path.join(DATA_DIR, 'sessions.json')
SUBS_FILE  = os.path.join(DATA_DIR, 'subscriptions.json')

# ── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('encaminhador')

# ── I/O JSON ─────────────────────────────────────────────────────────────────
def load(path):
    try:
        with open(path,'r',encoding='utf-8') as f: return json.load(f)
    except: return {}
def save(path,data):
    os.makedirs(os.path.dirname(path),exist_ok=True)
    with open(path,'w',encoding='utf-8') as f: json.dump(data,f,indent=2)

sessions      = load(SESS_FILE)
subscriptions = load(SUBS_FILE)

# ── FLASK KEEP-ALIVE ─────────────────────────────────────────────────────────
app = Flask('keep_alive')
@app.route('/')
def home(): return 'OK'
@app.route('/dump_subs')
def dump_subs(): return jsonify(subscriptions)
def run_flask():
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)))

# ── BOTFATHER BOT ───────────────────────────────────────────────────────────
bot = TelegramClient('bot_session', API_ID, API_HASH)

@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def ui_handler(ev):
    uid,txt,reply = ev.sender_id, ev.raw_text.strip(), ev.reply

    # helper para flood-wait
    async def handle_flood(e):
        if isinstance(e, errors.FloodWaitError):
            wait=e.seconds+1
            log.warning(f"FloodWait {wait}s")
            await asyncio.sleep(wait)
            return True
        return False

    # Admin: set_session
    if txt.startswith('/admin_set_session '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, sess = txt.split(' ',2)
            sessions[user_id] = sess; save(SESS_FILE,sessions)
            return await reply(f'✅ Sessão de `{user_id}` registrada.')
        except:
            return await reply('❌ Uso: `/admin_set_session USER_ID SESSION`')

    # Admin: subscribe
    if txt.startswith('/admin_subscribe '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, gid = txt.split(' ',2)
            gid=int(gid)
            lst=subscriptions.setdefault(user_id,[])
            if gid in lst: return await reply('⚠️ Já inscrito.')
            lst.append(gid); save(SUBS_FILE,subscriptions)
            return await reply(f'✅ `{user_id}` inscrito em `{gid}`.')
        except:
            return await reply('❌ Uso: `/admin_subscribe USER_ID GROUP_ID`')

    # Admin: unsubscribe
    if txt.startswith('/admin_unsubscribe '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, gid = txt.split(' ',2)
            gid=int(gid)
            lst=subscriptions.get(user_id,[])
            if gid not in lst: return await reply('❌ Não inscrito.')
            lst.remove(gid); save(SUBS_FILE,subscriptions)
            return await reply(f'🗑️ `{user_id}` desinscrito de `{gid}`.')
        except:
            return await reply('❌ Uso: `/admin_unsubscribe USER_ID GROUP_ID`')

    # /start /help
    if txt in ('/start','/help'):
        return await reply(
            "**👋 Bem-vindo!**\n"
            "1️⃣ `/myid`\n"
            "2️⃣ `/setsession SUA_SESSION`\n"
            "3️⃣ `/listgroups`\n"
            "4️⃣ `/subscribe GROUP_ID`\n"
            "5️⃣ `/unsubscribe GROUP_ID`",
            parse_mode='Markdown'
        )

    # /myid
    if txt=='/myid':
        return await reply(f'🆔 `{uid}`',parse_mode='Markdown')

    # /setsession
    if txt.startswith('/setsession '):
        sess=txt.split(' ',1)[1].strip()
        sessions[str(uid)] = sess; save(SESS_FILE,sessions)
        await reply('✅ Sessão salva! Use `/listgroups`.')
        await ensure_client(uid)
        return

    client = await ensure_client(uid)
    if not client:
        return await reply('❌ Use `/setsession SUA_SESSION` primeiro.')

    # /listgroups
    if txt=='/listgroups':
        dlg = await client.get_dialogs()
        lines=[f"{d.title or 'Sem título'} — `{d.id}`" for d in dlg if d.is_group or d.is_channel]
        return await reply('📋 *Seus grupos:*\n'+ "\n".join(lines[:50]),parse_mode='Markdown')

    # /subscribe
    if txt.startswith('/subscribe '):
        try: gid=int(txt.split(' ',1)[1])
        except: return await reply('❌ ID inválido.')
        lst=subscriptions.setdefault(str(uid),[])
        if gid in lst: return await reply('⚠️ Já inscrito.')
        lst.append(gid); save(SUBS_FILE,subscriptions)
        return await reply(f'✅ Inscrito em `{gid}`.')

    # /unsubscribe
    if txt.startswith('/unsubscribe '):
        try: gid=int(txt.split(' ',1)[1])
        except: return await reply('❌ ID inválido.')
        lst=subscriptions.get(str(uid),[])
        if gid not in lst: return await reply('❌ Não inscrito.')
        lst.remove(gid); save(SUBS_FILE,subscriptions)
        return await reply(f'🗑️ Desinscrito de `{gid}`.')

    return await reply('❓ Comando não reconhecido. `/help`.',parse_mode='Markdown')


# ── CLIENTE “ADMIN” (canais fixos) ───────────────────────────────────────────
admin = TelegramClient(StringSession(SESSION_STRING),API_ID,API_HASH)

@admin.on(events.NewMessage)
async def forward_initial(ev):
    cid = ev.chat_id
    # debug do filtro
    log.info(f"🔍 initial from {cid}, SOURCE={SOURCE_CHAT_IDS}")
    if cid not in SOURCE_CHAT_IDS:
        return

    m     = ev.message
    chat  = await admin.get_entity(cid)
    title = getattr(chat,'title',None) or str(cid)

    # cabeçalho
    await admin.send_message(DEST_CHAT_ID,f"📢 *{title}* (`{cid}`)",parse_mode='Markdown')

    # 1) forward normal
    try:
        await m.forward_to(DEST_CHAT_ID)
        return
    except Exception as e:
        log.exception(e)
        if await handle_flood(e): pass

    # 2) download + reenvio
    try:
        if m.media:
            path = await m.download_media()
            await admin.send_file(DEST_CHAT_ID,path,caption=m.text or '')
        else:
            await admin.send_message(DEST_CHAT_ID,m.text or '')
    except Exception as e:
        log.exception(e)
        if await handle_flood(e):
            # retry após flood
            if m.media:
                path = await m.download_media()
                await admin.send_file(DEST_CHAT_ID,path,caption=m.text or '')
            else:
                await admin.send_message(DEST_CHAT_ID,m.text or '')


# ── CLIENTES “USER” (dinâmicos) ─────────────────────────────────────────────
user_clients = {}
async def ensure_client(uid):
    key = str(uid)
    if key in user_clients:
        return user_clients[key]
    sess = sessions.get(key)
    if not sess:
        return None
    try:
        cli = TelegramClient(StringSession(sess),API_ID,API_HASH)
    except ValueError:
        sessions.pop(key,None); save(SESS_FILE,sessions)
        await bot.send_message(uid,'🚫 Sessão inválida. `/setsession`.')
        return None

    await cli.start()
    user_clients[key]=cli

    @cli.on(events.NewMessage)
    async def fwd_user(ev):
        if ev.chat_id not in subscriptions.get(key,[]): return
        m     = ev.message
        chat  = await cli.get_entity(ev.chat_id)
        title = getattr(chat,'title',None) or str(ev.chat_id)

        await bot.send_message(DEST_CHAT_ID,f"📢 *{title}* (`{ev.chat_id}`)",parse_mode='Markdown')

        try:
            await m.forward_to(DEST_CHAT_ID)
            return
        except Exception as e:
            log.exception(e)
            if isinstance(e,errors.FloodWaitError):
                await asyncio.sleep(e.seconds+1)

        # download + reenvio
        if m.media:
            path = await m.download_media()
            await bot.send_file(DEST_CHAT_ID,path,caption=m.text or '')
        else:
            await bot.send_message(DEST_CHAT_ID,m.text or '')

    asyncio.create_task(cli.run_until_disconnected())
    return cli


# ── ENTRYPOINT ─────────────────────────────────────────────────────────────
async def main():
    threading.Thread(target=run_flask,daemon=True).start()
    await asyncio.gather(admin.start(), bot.start(bot_token=BOT_TOKEN))
    log.info('🤖 Bots rodando…')
    await asyncio.gather(admin.run_until_disconnected(), bot.run_until_disconnected())

if __name__=='__main__':
    asyncio.run(main())
