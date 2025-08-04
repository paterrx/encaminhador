# main.py
import os, json, asyncio, threading, logging
from flask import Flask, jsonify
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession

# ─── Config via ENV ────────────────────────────────────────────────────────
API_ID          = int(os.environ['TELEGRAM_API_ID'])
API_HASH        = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN       = os.environ['BOT_TOKEN']
DEST_CHAT_ID    = int(os.environ['DEST_CHAT_ID'])
SESSION_STRING  = os.environ['SESSION_STRING']
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS','[]'))
_raw_admins     = os.environ.get('ADMIN_IDS','[]')
try:
    _parsed = json.loads(_raw_admins)
    ADMIN_IDS = {_parsed} if isinstance(_parsed,int) else set(_parsed)
except:
    ADMIN_IDS = set()

# Volume persistente em /data
DATA_DIR   = '/data'
SESS_FILE  = os.path.join(DATA_DIR, 'sessions.json')
SUBS_FILE  = os.path.join(DATA_DIR, 'subscriptions.json')

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('encaminhador')

# ─── I/O JSON ────────────────────────────────────────────────────────────────
def load_file(path):
    try:
        with open(path,'r',encoding='utf-8') as f: return json.load(f)
    except: return {}

def save_file(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path,'w',encoding='utf-8') as f: json.dump(obj,f,indent=2)

sessions      = load_file(SESS_FILE)    # { user_id: session_string, ... }
subscriptions = load_file(SUBS_FILE)    # { user_id: [ group_id, ... ], ... }

# ─── Flask keep-alive & debug ────────────────────────────────────────────────
app = Flask('keep_alive')
@app.route('/')
def home(): return 'OK'
@app.route('/dump_subs')
def dump_subs(): return jsonify(subscriptions)
def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)), debug=False)

# ─── Bot “admin” (interface de usuário para /admin_*) ─────────────────────────
bot = TelegramClient('bot_session', API_ID, API_HASH)

@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def ui_handler(ev):
    uid, txt, reply = ev.sender_id, ev.raw_text.strip(), ev.reply

    # helper: flood-wait
    async def handle_flood(e):
        if isinstance(e, errors.FloodWaitError):
            await asyncio.sleep(e.seconds+1)
            return True
        return False

    # ─ admin_set_session USER_ID SESSION
    if txt.startswith('/admin_set_session '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, sess = txt.split(' ',2)
            sessions[user_id] = sess
            save_file(SESS_FILE, sessions)
            # **inicia** o client desse user_id na hora
            await ensure_client(int(user_id))
            return await reply(f'✅ Sessão de `{user_id}` registrada e client iniciado.')
        except:
            return await reply('❌ Uso: `/admin_set_session USER_ID SESSION`')

    # ─ admin_subscribe USER_ID GROUP_ID
    if txt.startswith('/admin_subscribe '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, gid_s = txt.split(' ',2)
            gid = int(gid_s)
            lst = subscriptions.setdefault(user_id, [])
            if gid in lst:
                return await reply('⚠️ Já inscrito.')
            lst.append(gid)
            save_file(SUBS_FILE, subscriptions)
            # garante que o client esteja rodando
            await ensure_client(int(user_id))
            return await reply(f'✅ `{user_id}` inscrito em `{gid}` e client atualizado.')
        except:
            return await reply('❌ Uso: `/admin_subscribe USER_ID GROUP_ID`')

    # ─ admin_unsubscribe USER_ID GROUP_ID
    if txt.startswith('/admin_unsubscribe '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, gid_s = txt.split(' ',2)
            gid = int(gid_s)
            lst = subscriptions.get(user_id, [])
            if gid not in lst:
                return await reply('❌ Não inscrito.')
            lst.remove(gid)
            save_file(SUBS_FILE, subscriptions)
            return await reply(f'🗑️ `{user_id}` desinscrito de `{gid}`.')
        except:
            return await reply('❌ Uso: `/admin_unsubscribe USER_ID GROUP_ID`')

    # ─ /start e /help
    if txt in ('/start','/help'):
        return await reply(
            "**👋 Bem-vindo ao Encaminhador!**\n\n"
            "1️⃣ `/myid`\n"
            "2️⃣ `/setsession SUA_SESSION`\n"
            "3️⃣ `/listgroups`\n"
            "4️⃣ `/subscribe GROUP_ID`\n"
            "5️⃣ `/unsubscribe GROUP_ID`",
            parse_mode='Markdown'
        )

    # ─ /myid
    if txt == '/myid':
        return await reply(f'🆔 Seu ID: `{uid}`', parse_mode='Markdown')

    # ─ /setsession
    if txt.startswith('/setsession '):
        sess = txt.split(' ',1)[1].strip()
        sessions[str(uid)] = sess
        save_file(SESS_FILE, sessions)
        # iniciar client na hora
        await ensure_client(uid)
        return await reply('✅ Session salva e client iniciado! Use `/listgroups`.')

    # 👇 para os comandos abaixo já preciso do client
    client = await ensure_client(uid)
    if not client:
        return await reply('❌ Use `/setsession SUA_SESSION` primeiro.')

    # ─ /listgroups
    if txt == '/listgroups':
        dialogs = await client.get_dialogs()
        lines = [
            f"{d.title or 'Sem título'} — `{d.id}`"
            for d in dialogs if d.is_channel or d.is_group
        ]
        return await reply('📋 *Seus grupos:*\n' + "\n".join(lines[:50]), parse_mode='Markdown')

    # ─ /subscribe GROUP_ID
    if txt.startswith('/subscribe '):
        try:
            gid = int(txt.split(' ',1)[1])
        except:
            return await reply('❌ ID inválido.')
        lst = subscriptions.setdefault(str(uid), [])
        if gid in lst:
            return await reply('⚠️ Já inscrito.')
        lst.append(gid)
        save_file(SUBS_FILE, subscriptions)
        await ensure_client(uid)
        return await reply(f'✅ Inscrito em `{gid}`.')

    # ─ /unsubscribe GROUP_ID
    if txt.startswith('/unsubscribe '):
        try:
            gid = int(txt.split(' ',1)[1])
        except:
            return await reply('❌ ID inválido.')
        lst = subscriptions.get(str(uid), [])
        if gid not in lst:
            return await reply('❌ Não inscrito.')
        lst.remove(gid)
        save_file(SUBS_FILE, subscriptions)
        return await reply(f'🗑️ Desinscrito de `{gid}`.')

    return await reply('❓ Comando não reconhecido. Use `/help`.', parse_mode='Markdown')


# ─── CLIENTE “ADMIN” PARA OS 3 CANAIS FIXOS ──────────────────────────────────
admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

@admin_client.on(events.NewMessage)
async def forward_initial(ev):
    cid = ev.chat_id
    log.info(f"🔍 FIXO em {cid}, SOURCE={SOURCE_CHAT_IDS}")
    if cid not in SOURCE_CHAT_IDS:
        return

    m    = ev.message
    chat = await admin_client.get_entity(cid)
    title = getattr(chat,'title',None) or str(cid)

    # Cabeçalho
    await admin_client.send_message(
        DEST_CHAT_ID,
        f"📢 *{title}* (`{cid}`)",
        parse_mode='Markdown'
    )

    # 1) forward normal
    try:
        await m.forward_to(DEST_CHAT_ID)
        return
    except Exception as e:
        log.exception(e)
        if isinstance(e,errors.FloodWaitError):
            await asyncio.sleep(e.seconds+1)

    # 2) download+reenvio
    try:
        if m.media:
            path = await m.download_media()
            await admin_client.send_file(DEST_CHAT_ID,path,caption=m.text or '')
        else:
            await admin_client.send_message(DEST_CHAT_ID,m.text or '')
    except Exception as e:
        log.exception(e)
        if isinstance(e,errors.FloodWaitError):
            await asyncio.sleep(e.seconds+1)
            if m.media:
                path = await m.download_media()
                await admin_client.send_file(DEST_CHAT_ID,path,caption=m.text or '')
            else:
                await admin_client.send_message(DEST_CHAT_ID,m.text or '')


# ─── CLIENTES “USER” PARA INSCRIÇÕES DINÂMICAS ─────────────────────────────
user_clients = {}

async def ensure_client(uid:int):
    key = str(uid)
    if key in user_clients:
        return user_clients[key]

    sess = sessions.get(key)
    if not sess:
        return None

    try:
        cli = TelegramClient(StringSession(sess), API_ID, API_HASH)
    except ValueError:
        sessions.pop(key,None)
        save_file(SESS_FILE,sessions)
        await bot.send_message(uid,'🚫 Sessão inválida. Use `/setsession`.')
        return None

    await cli.start()
    user_clients[key] = cli

    # ─ handler dinâmico
    @cli.on(events.NewMessage)
    async def forward_user(ev):
        if ev.chat_id not in subscriptions.get(key,[]):
            return
        log.info(f"🔍 DINÂMICO de {ev.chat_id} pelo user {key}")

        m    = ev.message
        chat = await cli.get_entity(ev.chat_id)
        title = getattr(chat,'title',None) or str(ev.chat_id)

        await bot.send_message(
            DEST_CHAT_ID,
            f"📢 *{title}* (`{ev.chat_id}`)",
            parse_mode='Markdown'
        )

        try:
            await m.forward_to(DEST_CHAT_ID)
            return
        except Exception as e:
            log.exception(e)
            if isinstance(e,errors.FloodWaitError):
                await asyncio.sleep(e.seconds+1)

        if m.media:
            path = await m.download_media()
            await bot.send_file(DEST_CHAT_ID,path,caption=m.text or '')
        else:
            await bot.send_message(DEST_CHAT_ID,m.text or '')

    # dissociar em background
    asyncio.create_task(cli.run_until_disconnected())
    return cli


# ─── ENTRYPOINT ──────────────────────────────────────────────────────────────
async def main():
    # 1) flask thread
    threading.Thread(target=run_flask,daemon=True).start()

    # 2) start dos dois clients
    await asyncio.gather(
        admin_client.start(),
        bot.start(bot_token=BOT_TOKEN)
    )

    # 3) **BOOTSTRAP** de todas as sessões salvas
    for uid_str in list(sessions.keys()):
        try:
            await ensure_client(int(uid_str))
        except Exception:
            log.exception(f"Não consegui iniciar client para {uid_str}")

    log.info('🤖 Bots rodando...')
    # 4) fica rodando
    await asyncio.gather(
        admin_client.run_until_disconnected(),
        bot.run_until_disconnected()
    )

if __name__ == '__main__':
    asyncio.run(main())
