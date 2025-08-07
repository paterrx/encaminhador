# main.py
# Encaminhador ultimate: fallbacks robustos, deduplicação, retry, edição e audit trail
import os
import json
import asyncio
import threading
import logging
from datetime import datetime
from flask import Flask, jsonify
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession

# ── CONFIGURAÇÃO VIA ENV ───────────────────────────────────────────────────
API_ID          = int(os.environ['TELEGRAM_API_ID'])
API_HASH        = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN       = os.environ['BOT_TOKEN']
DEST_CHAT_ID    = int(os.environ['DEST_CHAT_ID'])
SESSION_STRING  = os.environ['SESSION_STRING']

# IDs puros (sem -100) dos canais fixos
elem = os.environ.get('SOURCE_CHAT_IDS','[]')
SOURCE_CHAT_IDS = json.loads(elem)

# Admin IDs (int ou lista)
raw_admins = os.environ.get('ADMIN_IDS','[]')
try:
    parsed = json.loads(raw_admins)
    ADMIN_IDS = {parsed} if isinstance(parsed,int) else set(parsed)
except:
    ADMIN_IDS = set()

# Persistência em volume montado em /data
DATA_DIR   = '/data'
SESS_FILE  = os.path.join(DATA_DIR,'sessions.json')
SUBS_FILE  = os.path.join(DATA_DIR,'subscriptions.json')
FWDS_FILE  = os.path.join(DATA_DIR,'forwarded.json')  # mapeia orig->dest
AUDIT_FILE = os.path.join(DATA_DIR,'audit.json')      # log de eventos

# ── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('encaminhador')

# ── JSON I/O ─────────────────────────────────────────────────────────────────
def load(path, default):
    try:
        with open(path,'r',encoding='utf-8') as f: return json.load(f)
    except:
        return default

def save(path,obj):
    os.makedirs(os.path.dirname(path),exist_ok=True)
    with open(path,'w',encoding='utf-8') as f:
        json.dump(obj,f,indent=2)

sessions      = load(SESS_FILE, {})
subscriptions = load(SUBS_FILE, {})
forwarded     = load(FWDS_FILE, {})  # {"chat_id:msg_id": dest_msg_id}
audit_log     = load(AUDIT_FILE, []) # list of events

# Deduplicação simples
from collections import deque
MAX_CACHE = 1000
dedup_cache = set()
dedup_queue = deque()

def is_duplicate(key):
    if key in dedup_cache:
        return True
    dedup_cache.add(key)
    dedup_queue.append(key)
    if len(dedup_queue) > MAX_CACHE:
        old = dedup_queue.popleft()
        dedup_cache.remove(old)
    return False

# Audit trail
def record_audit(entry):
    audit_log.append(entry)
    save(AUDIT_FILE,audit_log)

# ── FLASK KEEP-ALIVE + DUMP_SUBS ─────────────────────────────────────────────
app = Flask('keep_alive')
@app.route('/')
def home(): return 'OK'
@app.route('/dump_subs')
def dump_subs(): return jsonify(subscriptions)
def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)), debug=False)

# ── BotFather BOT (UI admin e público) ───────────────────────────────────────
bot = TelegramClient('bot_session', API_ID, API_HASH)
@bot.on(events.NewMessage(func=lambda e:e.is_private))
async def ui_handler(ev):
    uid,txt,reply = ev.sender_id, ev.raw_text.strip(), ev.reply
    async def retry(fn,*args,**kwargs):
        for i in range(3):
            try: return await fn(*args,**kwargs)
            except errors.FloodWaitError as f:
                await asyncio.sleep(f.seconds+1)
            except Exception as e:
                log.exception(e)
                await asyncio.sleep(1*(2**i))
        return None

    # Admin commands
    if txt.startswith('/admin_set_session '):
        if uid not in ADMIN_IDS: return await reply('🚫 Sem permissão')
        _,user_id,sess = txt.split(' ',2)
        sessions[user_id]=sess; save(SESS_FILE,sessions)
        await ensure_client(int(user_id))
        return await reply(f'✅ Sessão `{user_id}` salva e listener ativo')

    if txt.startswith('/admin_subscribe '):
        if uid not in ADMIN_IDS: return await reply('🚫 Sem permissão')
        _,user_id,gid = txt.split(' ',2); gid=int(gid)
        subs = subscriptions.setdefault(user_id,[])
        if gid in subs: return await reply('⚠️ Já inscrito')
        subs.append(gid); save(SUBS_FILE,subscriptions)
        await ensure_client(int(user_id))
        return await reply(f'✅ `{user_id}` inscrito em `{gid}`')

    if txt.startswith('/admin_unsubscribe '):
        if uid not in ADMIN_IDS: return await reply('🚫 Sem permissão')
        _,user_id,gid = txt.split(' ',2); gid=int(gid)
        subs = subscriptions.get(user_id,[])
        if gid not in subs: return await reply('❌ Não inscrito')
        subs.remove(gid); save(SUBS_FILE,subscriptions)
        return await reply(f'🗑️ `{user_id}` desinscrito de `{gid}`')

    # Público
    if txt in ('/start','/help'):
        return await reply(
            "**👋 Bem-vindo!**\n"
            "1️⃣ `/myid`\n2️⃣ `/setsession SUA_SESSION`\n"
            "3️⃣ `/listgroups`\n4️⃣ `/subscribe GROUP_ID`\n"
            "5️⃣ `/unsubscribe GROUP_ID`",
            parse_mode='Markdown'
        )
    if txt=='/myid': return await reply(f'🆔 `{uid}`',parse_mode='Markdown')
    if txt.startswith('/setsession '):
        sess=txt.split(' ',1)[1]
        sessions[str(uid)]=sess; save(SESS_FILE,sessions)
        await ensure_client(uid)
        return await reply('✅ Session salva e listener ativo')
    client = await ensure_client(uid)
    if not client: return await reply('❌ Use `/setsession` antes')
    if txt=='/listgroups':
        dlg=await client.get_dialogs()
        lines=[f"{d.title or 'Sem título'} — `{d.id}`" for d in dlg if d.is_group or d.is_channel]
        return await reply('📋 Grupos:\n'+"\n".join(lines[:50]),parse_mode='Markdown')
    if txt.startswith('/subscribe '):
        gid=int(txt.split(' ',1)[1]); subs=subscriptions.setdefault(str(uid),[])
        if gid in subs: return await reply('⚠️ Já inscrito')
        subs.append(gid); save(SUBS_FILE,subscriptions)
        await ensure_client(uid)
        return await reply(f'✅ Inscrito em `{gid}`')
    if txt.startswith('/unsubscribe '):
        gid=int(txt.split(' ',1)[1]); subs=subscriptions.get(str(uid),[])
        if gid not in subs: return await reply('❌ Não inscrito')
        subs.remove(gid); save(SUBS_FILE,subscriptions)
        return await reply(f'🗑️ Desinscrito de `{gid}`')
    return await reply('❓ Comando não reconhecido',parse_mode='Markdown')

# ── HANDLERS FIXOS + EDIT + FALLBACKS + AUDIT ────────────────────────────────
admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

async def process_message(client, ev, is_edit=False, user_key=None):
    cid = ev.chat_id
    title = (await client.get_entity(cid)).title or str(cid)
    key = f"{cid}:{ev.message.id}"
    if is_duplicate(key) and not is_edit:
        log.info(f"Skipped duplicate {key}")
        return
    fwd_id = forwarded.get(key)
    text = ev.message.text or ev.message.message or ''
    ts = ev.message.date.isoformat()
    action_chain = [
        ('forward_to', lambda: ev.message.forward_to(DEST_CHAT_ID)),
        ('download_send', lambda: client.send_file(DEST_CHAT_ID, ev.message.download_media(), caption=text)),
        ('send_text', lambda: client.send_message(DEST_CHAT_ID, text))
    ]
    # Header only for new messages
    if not is_edit:
        await client.send_message(DEST_CHAT_ID, f"📢 *{title}* (`{cid}`)", parse_mode='Markdown')
    # If edit: try edit_message
    if is_edit and fwd_id:
        try:
            await client.edit_message(DEST_CHAT_ID, fwd_id, text)
            record_audit({'ts':ts,'cid':cid,'title':title,'mid':ev.message.id,'status':'edited'})
            return
        except Exception as e:
            log.exception(e)
    # Try chain
    for name,fn in action_chain:
        try:
            result = await fn()
            new_id = (result.id if hasattr(result,'id') else result.message_id)
            forwarded[key] = new_id
            save(FWDS_FILE, forwarded)
            record_audit({'ts':ts,'cid':cid,'title':title,'mid':ev.message.id,'status':name})
            return
        except Exception as e:
            log.exception(e)
            if isinstance(e, errors.FloodWaitError):
                await asyncio.sleep(e.seconds+1)
    # All failed
    await client.send_message(DEST_CHAT_ID, f"❗ Falha ao enviar de {title} (`{cid}`) em {ts}")
    record_audit({'ts':ts,'cid':cid,'title':title,'mid':ev.message.id,'status':'failure'})

@admin_client.on(events.NewMessage(chats=SOURCE_CHAT_IDS))
async def fixed_handler(ev):
    await process_message(admin_client, ev)

@admin_client.on(events.MessageEdited(chats=SOURCE_CHAT_IDS))
async def fixed_edit(ev):
    await process_message(admin_client, ev, is_edit=True)

# ── HANDLERS DINÂMICOS (user clients)
user_clients = {}

async def ensure_client(uid:int):
    key = str(uid)
    if key in user_clients:
        return user_clients[key]
    sess = sessions.get(key)
    if not sess: return None
    cli = TelegramClient(StringSession(sess), API_ID, API_HASH)
    await cli.start()
    user_clients[key] = cli
    @cli.on(events.NewMessage(chats=subscriptions.get(key,[])))
    async def dyn_msg(ev): await process_message(cli, ev, user_key=key)
    @cli.on(events.MessageEdited(chats=subscriptions.get(key,[])))
    async def dyn_edit(ev): await process_message(cli, ev, is_edit=True, user_key=key)
    asyncio.create_task(cli.run_until_disconnected())
    return cli

# ── ENTRYPOINT ─────────────────────────────────────────────────────────────
async def main():
    threading.Thread(target=run_flask, daemon=True).start()
    await asyncio.gather(admin_client.start(), bot.start(bot_token=BOT_TOKEN))
    # Bootstrap users
    for uid_str in sessions.keys():
        try: await ensure_client(int(uid_str))
        except Exception as e: log.exception(e)
    log.info('🤖 Bots rodando...')
    await asyncio.gather(admin_client.run_until_disconnected(), bot.run_until_disconnected())

if __name__=='__main__':
    asyncio.run(main())
