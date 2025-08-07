import os, json, asyncio, threading, logging
from datetime import datetime
from flask import Flask, jsonify
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession

# ── Config via ENV ──────────────────────────────────────────────────────────
API_ID         = int(os.environ['TELEGRAM_API_ID'])
API_HASH       = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN      = os.environ['BOT_TOKEN']
DEST_CHAT_ID   = int(os.environ['DEST_CHAT_ID'])
SESSION_STRING = os.environ['SESSION_STRING']
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS','[]'))
_raw_admins   = os.environ.get('ADMIN_IDS','[]')
try:
    tmp = json.loads(_raw_admins)
    ADMIN_IDS = {tmp} if isinstance(tmp,int) else set(tmp)
except:
    ADMIN_IDS = set()

# ── Persistência ────────────────────────────────────────────────────────────
DATA_DIR    = '/data'
SESS_FILE   = os.path.join(DATA_DIR,'sessions.json')
SUBS_FILE   = os.path.join(DATA_DIR,'subscriptions.json')
AUDIT_FILE  = os.path.join(DATA_DIR,'audit.json')

def load_json(path, default):
    try:
        with open(path,'r',encoding='utf-8') as f:
            return json.load(f)
    except:
        return default

def save_json(path, data):
    os.makedirs(os.path.dirname(path),exist_ok=True)
    with open(path,'w',encoding='utf-8') as f:
        json.dump(data,f,indent=2)

sessions      = load_json(SESS_FILE,{})
subscriptions = load_json(SUBS_FILE,{})
audit_trail   = load_json(AUDIT_FILE,[])

def record_audit(rec):
    audit_trail.append(rec)
    if len(audit_trail)>200: audit_trail.pop(0)
    save_json(AUDIT_FILE,audit_trail)

# ── Flask Keep-Alive + Dumps ────────────────────────────────────────────────
app = Flask('keep_alive')

@app.route('/')
def home(): return 'OK'

@app.route('/dump_subs')
def dump_subs(): return jsonify(subscriptions)

@app.route('/dump_audit')
def dump_audit(): return jsonify(audit_trail)

def run_flask():
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)),debug=False)

# ── BotFather (Admin + UI commands) ─────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('encaminhador')
bot = TelegramClient('bot_session',API_ID,API_HASH)

async def flood_wait(e):
    if isinstance(e,errors.FloodWaitError):
        await asyncio.sleep(e.seconds+1)

@bot.on(events.NewMessage(func=lambda e:e.is_private))
async def ui_handler(ev):
    uid, txt, reply = ev.sender_id, ev.raw_text.strip(), ev.reply

    # Admin set session
    if txt.startswith('/admin_set_session '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, sess = txt.split(' ',2)
            sessions[user_id]=sess
            save_json(SESS_FILE,sessions)
            return await reply(f'✅ Session de `{user_id}` registrada.',parse_mode='Markdown')
        except:
            return await reply('❌ Uso: `/admin_set_session USER_ID SESSION`')

    # Admin subscribe
    if txt.startswith('/admin_subscribe '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, gid_s = txt.split(' ',2)
            gid = int(gid_s)
            lst = subscriptions.setdefault(user_id,[])
            if gid in lst:
                return await reply('⚠️ Já inscrito.')
            lst.append(gid)
            save_json(SUBS_FILE,subscriptions)
            return await reply(f'✅ `{user_id}` inscrito em `{gid}`.',parse_mode='Markdown')
        except:
            return await reply('❌ Uso: `/admin_subscribe USER_ID GROUP_ID`')

    # Admin unsubscribe
    if txt.startswith('/admin_unsubscribe '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, gid_s = txt.split(' ',2)
            gid = int(gid_s)
            lst = subscriptions.get(user_id,[])
            if gid not in lst:
                return await reply('❌ Não inscrito.')
            lst.remove(gid)
            save_json(SUBS_FILE,subscriptions)
            return await reply(f'🗑️ `{user_id}` desinscrito de `{gid}`.',parse_mode='Markdown')
        except:
            return await reply('❌ Uso: `/admin_unsubscribe USER_ID GROUP_ID`')

    # Help
    if txt in ('/start','/help'):
        return await reply(
            "**👋 Encaminhador**\n\n"
            "1️⃣ `/myid`\n"
            "2️⃣ `/setsession SUA_SESSION`\n"
            "3️⃣ `/listgroups`\n"
            "4️⃣ `/subscribe GROUP_ID`\n"
            "5️⃣ `/unsubscribe GROUP_ID`\n\n"
            "⚙️ Admin: `/admin_set_session`, `/admin_subscribe`, `/admin_unsubscribe`",
            parse_mode='Markdown'
        )

    # /myid
    if txt=='/myid':
        return await reply(f'🆔 Seu ID: `{uid}`',parse_mode='Markdown')

    # /setsession
    if txt.startswith('/setsession '):
        sess = txt.split(' ',1)[1].strip()
        sessions[str(uid)] = sess
        save_json(SESS_FILE,sessions)
        await reply('✅ Session salva! Use `/listgroups`.',parse_mode='Markdown')
        await ensure_client(uid)
        return

    client = await ensure_client(uid)
    if not client:
        return await reply('❌ Faça `/setsession` antes.')

    # /listgroups
    if txt=='/listgroups':
        diags = await client.get_dialogs()
        lines=[]
        for d in diags:
            if getattr(d.entity,'megagroup',False) or getattr(d.entity,'broadcast',False):
                lines.append(f"- {d.title or '‹sem título›'} — `{d.id}`")
        return await reply("📋 *Seus grupos:*\n"+"\n".join(lines[:50]),parse_mode='Markdown')

    # /subscribe
    if txt.startswith('/subscribe '):
        try:
            gid=int(txt.split(' ',1)[1])
        except:
            return await reply('❌ Grupo inválido.')
        ukey=str(uid)
        lst=subscriptions.setdefault(ukey,[])
        if gid in lst:
            return await reply('⚠️ Já inscrito.')
        lst.append(gid)
        save_json(SUBS_FILE,subscriptions)
        return await reply(f'✅ Inscrito em `{gid}`.',parse_mode='Markdown')

    # /unsubscribe
    if txt.startswith('/unsubscribe '):
        try:
            gid=int(txt.split(' ',1)[1])
        except:
            return await reply('❌ Grupo inválido.')
        ukey=str(uid)
        lst=subscriptions.get(ukey,[])
        if gid not in lst:
            return await reply('❌ Não inscrito.')
        lst.remove(gid)
        save_json(SUBS_FILE,subscriptions)
        return await reply(f'🗑️ Desinscrito de `{gid}`.',parse_mode='Markdown')

    # unknown
    return await reply('❓ Use `/help`.',parse_mode='Markdown')

# ── Fixed channels client ────────────────────────────────────────────────────
admin_client = TelegramClient(StringSession(SESSION_STRING),API_ID,API_HASH)

@admin_client.on(events.NewMessage)
async def forward_fixed(ev):
    cid=ev.chat_id
    if cid not in SOURCE_CHAT_IDS: return
    m=ev.message
    chat=await admin_client.get_entity(cid)
    title=getattr(chat,'title',None) or str(cid)
    hdr=f"📣 *{title}* (`{cid}`)"
    await admin_client.send_message(DEST_CHAT_ID,hdr,parse_mode='Markdown')
    # 1: forward
    try:
        await m.forward_to(DEST_CHAT_ID)
        record_audit({'when':datetime.utcnow().isoformat(),'type':'fixed','cid':cid,'ok':'forward'})
        return
    except Exception as e:
        await flood_wait(e)
    # 2: download+send
    try:
        if m.media:
            p=await m.download_media()
            await admin_client.send_file(DEST_CHAT_ID,p,caption=m.text or '')
        else:
            await admin_client.send_message(DEST_CHAT_ID,m.text or '')
        record_audit({'when':datetime.utcnow().isoformat(),'type':'fixed','cid':cid,'ok':'download'})
        return
    except Exception as e:
        await flood_wait(e)
    # fail
    fail=f"🚨 Falha em *{title}* (`{cid}`) às {datetime.now().strftime('%H:%M:%S')}"
    await admin_client.send_message(DEST_CHAT_ID,fail,parse_mode='Markdown')
    record_audit({'when':datetime.utcnow().isoformat(),'type':'fixed','cid':cid,'ok':'fail'})

# ── Dynamic user clients ───────────────────────────────────────────────────
user_clients={}

async def ensure_client(uid):
    key=str(uid)
    if key in user_clients:
        return user_clients[key]
    sess=sessions.get(key)
    if not sess: return None
    try:
        cli=TelegramClient(StringSession(sess),API_ID,API_HASH)
    except ValueError:
        sessions.pop(key,None)
        save_json(SESS_FILE,sessions)
        await bot.send_message(uid,'🚫 Session inválida.')
        return None
    await cli.start()
    user_clients[key]=cli

    @cli.on(events.NewMessage)
    async def forward_dyn(ev):
        if ev.chat_id not in subscriptions.get(key,[]): return
        m=ev.message
        chat=await cli.get_entity(ev.chat_id)
        title=getattr(chat,'title',None) or str(ev.chat_id)
        hdr=f"📢 *{title}* (`{ev.chat_id}`)"
        await bot.send_message(DEST_CHAT_ID,hdr,parse_mode='Markdown')
        # 1:
        try:
            await m.forward_to(DEST_CHAT_ID)
            record_audit({'when':datetime.utcnow().isoformat(),'type':'dyn','cid':ev.chat_id,'ok':'forward'})
            return
        except Exception as e:
            await flood_wait(e)
        # 2:
        try:
            if m.media:
                p=await m.download_media()
                await bot.send_file(DEST_CHAT_ID,p,caption=m.text or '')
            else:
                await bot.send_message(DEST_CHAT_ID,m.text or '')
            record_audit({'when':datetime.utcnow().isoformat(),'type':'dyn','cid':ev.chat_id,'ok':'download'})
            return
        except Exception as e:
            await flood_wait(e)
        # fail
        fail=f"🚨 Falha em *{title}* (`{ev.chat_id}`) às {datetime.now().strftime('%H:%M:%S')}"
        await bot.send_message(DEST_CHAT_ID,fail,parse_mode='Markdown')
        record_audit({'when':datetime.utcnow().isoformat(),'type':'dyn','cid':ev.chat_id,'ok':'fail'})

    asyncio.create_task(cli.run_until_disconnected())
    return cli

# ── Entrypoint ─────────────────────────────────────────────────────────────
async def main():
    threading.Thread(target=run_flask,daemon=True).start()
    await asyncio.gather(
        admin_client.start(),
        bot.start(bot_token=BOT_TOKEN)
    )
    log.info('🤖 Bots rodando...')
    await asyncio.gather(
        admin_client.run_until_disconnected(),
        bot.run_until_disconnected()
    )

if __name__=='__main__':
    asyncio.run(main())
