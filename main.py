# main.py
import os, json, asyncio, threading, logging
from typing import Dict, List, Set

from flask import Flask, jsonify
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl import functions, types
from telethon.utils import get_peer_id

# ── ENV ──────────────────────────────────────────────────────────────────────
API_ID          = int(os.environ['TELEGRAM_API_ID'])
API_HASH        = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN       = os.environ['BOT_TOKEN']
DEST_CHAT_ID    = int(os.environ['DEST_CHAT_ID'])
SESSION_STRING  = os.environ['SESSION_STRING']              # sessão ADMIN (user)
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS','[]'))

_raw_admins = os.environ.get('ADMIN_IDS','[]')
try:
    parsed = json.loads(_raw_admins)
    ADMIN_IDS = {parsed} if isinstance(parsed, int) else set(parsed)
except:
    ADMIN_IDS = set()

# Flags para incluir/excluir comentários (linked discussion).
# Mantemos DESLIGADO por padrão (só posts do canal).
INCLUDE_LINKED_FIXED   = os.environ.get('INCLUDE_LINKED_FIXED','0') == '1'
INCLUDE_LINKED_DYNAMIC = os.environ.get('INCLUDE_LINKED_DYNAMIC','0') == '1'

DATA_DIR   = '/data'
SESS_FILE  = os.path.join(DATA_DIR, 'sessions.json')        # { "uid": "StringSession" }
SUBS_FILE  = os.path.join(DATA_DIR, 'subscriptions.json')   # { "uid": [group_ids...] }
STATE_FILE = os.path.join(DATA_DIR, 'state.json')           # { "last": {uid: {chat_id: msg_id}} }

# ── LOG ──────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('encaminhador')

# ── I/O ──────────────────────────────────────────────────────────────────────
def load_file(path, default):
    try:
        with open(path,'r',encoding='utf-8') as f:
            return json.load(f)
    except:
        return default

def save_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path,'w',encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

sessions: Dict[str,str]            = load_file(SESS_FILE, {})
subscriptions: Dict[str,List[int]] = load_file(SUBS_FILE, {})
state                              = load_file(STATE_FILE, {'last': {}})

def get_last(uid: str, chat_id: int) -> int:
    return int(state.get('last', {}).get(uid, {}).get(str(chat_id), 0))

def set_last(uid: str, chat_id: int, msg_id: int):
    state.setdefault('last', {}).setdefault(uid, {})[str(chat_id)] = int(msg_id)
    save_file(STATE_FILE, state)

def md_escape(s: str) -> str:
    if not s: return s
    return (s.replace("\\","\\\\")
             .replace("`","\\`")
             .replace("*","\\*")
             .replace("_","\\_"))

# ── FLASK ────────────────────────────────────────────────────────────────────
app = Flask('keep_alive')

@app.route('/')
def home(): return 'OK'

@app.route('/dump_subs')
def dump_subs(): return jsonify(subscriptions)

@app.route('/dump_allowed/<uid>')
async def dump_allowed(uid: str):
    cli = user_clients.get(uid)
    if not cli:
        return jsonify({"error": "no client"}), 404
    ids = sorted(list(await compute_allowed_ids(cli, uid, dynamic=True)))
    return jsonify({"uid": uid, "allowed": ids})

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)), debug=False)

# ── CLIENTES ─────────────────────────────────────────────────────────────────
bot          = TelegramClient('bot_session', API_ID, API_HASH)                    # interface de comandos
admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)    # SÓ ELE envia ao DEST

user_clients: Dict[str, TelegramClient] = {}   # uid -> client
LINK_CACHE: Dict[str, Dict[int, Set[int]]] = {} # uid -> {base_id: {ids...}}

# ── EXPANSÃO E CHECKS ────────────────────────────────────────────────────────
async def expand_ids_for_user(cli: TelegramClient, uid_key: str, base_id: int, include_linked: bool) -> Set[int]:
    """
    Retorna base_id + chat de discussão vinculado caso include_linked=True.
    """
    if not include_linked:
        return {base_id}

    cache = LINK_CACHE.setdefault(uid_key, {})
    if base_id in cache:
        return set(cache[base_id])

    expanded: Set[int] = {base_id}
    try:
        ent = await cli.get_entity(base_id)
        if isinstance(ent, types.Channel):
            full = await cli(functions.channels.GetFullChannelRequest(channel=ent))
            linked = getattr(full.full_chat, 'linked_chat_id', None)
            if linked:
                linked_full = get_peer_id(types.PeerChannel(linked))  # -> -100xxxxxxxxxx
                expanded.add(linked_full)
                log.info(f"[expand] uid={uid_key} base={base_id} linked_raw={linked} linked_full={linked_full}")
    except Exception as e:
        log.info(f"[expand] uid={uid_key} base={base_id} sem vinculo ({type(e).__name__})")

    cache[base_id] = expanded
    return expanded

async def compute_allowed_ids(cli: TelegramClient, uid_key: str, dynamic: bool) -> Set[int]:
    include = INCLUDE_LINKED_DYNAMIC if dynamic else INCLUDE_LINKED_FIXED
    allowed: Set[int] = set()
    for base_id in subscriptions.get(uid_key, []):
        allowed |= await expand_ids_for_user(cli, uid_key, base_id, include_linked=include)
    return allowed

async def check_access_and_warn(cli: TelegramClient, uid_key: str, base_id: int):
    try:
        ent = await cli.get_entity(base_id)
        if isinstance(ent, types.Channel):
            await cli(functions.channels.GetFullChannelRequest(channel=ent))
    except errors.ChannelPrivateError:
        await admin_client.send_message(DEST_CHAT_ID, f"⚠️ user `{uid_key}`: canal `{base_id}` é privado (sem acesso).", parse_mode='Markdown')
    except errors.ChannelInvalidError:
        await admin_client.send_message(DEST_CHAT_ID, f"⚠️ user `{uid_key}`: canal `{base_id}` inválido.", parse_mode='Markdown')
    except Exception as e:
        log.info(f"[access] uid={uid_key} base={base_id} -> {type(e).__name__}")

# ── ENCAMINHAMENTO (sempre via admin_client para o DEST) ─────────────────────
async def forward_with_fallback(src_client: TelegramClient, msg, title: str, chat_id: int):
    header = f"📢 *{md_escape(title)}* (`{chat_id}`)"
    try:
        await admin_client.get_entity(DEST_CHAT_ID)
    except Exception:
        pass

    await admin_client.send_message(DEST_CHAT_ID, header, parse_mode='Markdown')

    if src_client is admin_client:
        try:
            await msg.forward_to(DEST_CHAT_ID)
            return
        except errors.FloodWaitError as e:
            await asyncio.sleep(e.seconds+1)
        except Exception:
            pass

    try:
        if msg.media:
            path = await src_client.download_media(msg)
            await admin_client.send_file(DEST_CHAT_ID, path, caption=(msg.text or ''))
        else:
            await admin_client.send_message(DEST_CHAT_ID, msg.text or '')
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds+1)
        try:
            if msg.media:
                path = await src_client.download_media(msg)
                await admin_client.send_file(DEST_CHAT_ID, path, caption=(msg.text or ''))
            else:
                await admin_client.send_message(DEST_CHAT_ID, msg.text or '')
        except Exception as e2:
            log.exception(e2)
    except Exception as e:
        log.exception(e)

# ── POLLER (pega perdidas) ──────────────────────────────────────────────────
async def poller(cli: TelegramClient, uid_key: str):
    while True:
        try:
            allowed = await compute_allowed_ids(cli, uid_key, dynamic=True)   # sem linked (flag padrão)
            base_allowed = set(subscriptions.get(uid_key, []))
            targets = set(allowed) | base_allowed   # serão só os bases por padrão

            for chat_id in targets:
                last = get_last(uid_key, chat_id)
                try:
                    msgs = await cli.get_messages(chat_id, limit=5)
                except Exception:
                    msgs = []
                for m in reversed(msgs or []):
                    if not m.id or m.id <= last:
                        continue

                    # Como não aceitamos linked por padrão, não precisa do truque com fwd_from.
                    ent = await cli.get_entity(chat_id)
                    title = getattr(ent, 'title', None) or str(chat_id)
                    await forward_with_fallback(cli, m, title, chat_id)
                    set_last(uid_key, chat_id, m.id)
        except Exception as e:
            log.info(f"[poller] user={uid_key} erro {type(e).__name__}")
        await asyncio.sleep(30)

# ── DINÂMICOS ────────────────────────────────────────────────────────────────
user_clients: Dict[str, TelegramClient] = {}

async def ensure_client(uid: int):
    key = str(uid)
    sess = sessions.get(key)
    if not sess:
        return None
    if key in user_clients:
        return user_clients[key]

    cli = TelegramClient(StringSession(sess), API_ID, API_HASH)
    await cli.start()
    user_clients[key] = cli

    # aquece/valida acesso
    for base in subscriptions.get(key, []):
        asyncio.create_task(check_access_and_warn(cli, key, base))

    @cli.on(events.NewMessage)
    async def forward_user(ev):
        # só processa mensagens do próprio canal base (sem linked por padrão)
        if ev.message and ev.message.id:
            last = get_last(key, ev.chat_id)
            if ev.message.id <= last:
                return

        # Allowed SÓ base por padrão
        allowed = await compute_allowed_ids(cli, key, dynamic=True)
        if ev.chat_id not in allowed:
            log.info(f"⛔ [dynamic-skip] user={key} chat={ev.chat_id} not in allowed={sorted(allowed)}")
            return

        chat  = await cli.get_entity(ev.chat_id)
        title = getattr(chat, 'title', None) or str(ev.chat_id)

        await forward_with_fallback(cli, ev.message, title, ev.chat_id)
        if ev.message and ev.message.id:
            set_last(key, ev.chat_id, ev.message.id)

    asyncio.create_task(poller(cli, key))
    asyncio.create_task(cli.run_until_disconnected())
    return cli

# ── UI BOT (comandos) ───────────────────────────────────────────────────────
@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def ui_handler(ev):
    uid, txt, reply = ev.sender_id, ev.raw_text.strip(), ev.reply

    if txt.startswith('/admin_set_session '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, sess = txt.split(' ',2)
            sessions[user_id] = sess
            save_file(SESS_FILE, sessions)
            await reply(f'✅ Session de `{user_id}` registrada.')
            await ensure_client(int(user_id))
        except:
            return await reply('❌ Uso: `/admin_set_session USER_ID SESSION`')
        return

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
                LINK_CACHE.pop(user_id, None)
                await ensure_client(int(user_id))
                cli = user_clients.get(user_id)
                if cli:
                    asyncio.create_task(check_access_and_warn(cli, user_id, gid))
        except:
            return await reply('❌ Uso: `/admin_subscribe USER_ID GROUP_ID`')
        return

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
                if user_id in LINK_CACHE and gid in LINK_CACHE[user_id]:
                    LINK_CACHE[user_id].pop(gid, None)
                await reply(f'🗑️ `{user_id}` desinscrito de `{gid}`.')
        except:
            return await reply('❌ Uso: `/admin_unsubscribe USER_ID GROUP_ID`')
        return

    if txt in ('/start','/help'):
        return await reply(
            "👋 *Bem-vindo ao Encaminhador!*\n\n"
            "1️⃣ `/myid`\n"
            "2️⃣ `/setsession SUA_SESSION`\n"
            "3️⃣ `/listgroups`\n"
            "4️⃣ `/subscribe GROUP_ID`\n"
            "5️⃣ `/unsubscribe GROUP_ID`\n\n"
            "⚙️ Admin: `/admin_set_session`, `/admin_subscribe`, `/admin_unsubscribe`",
            parse_mode='Markdown'
        )

    if txt == '/myid':
        return await reply(f'🆔 Seu ID: `{uid}`', parse_mode='Markdown')

    if txt.startswith('/setsession '):
        sess = txt.split(' ',1)[1].strip()
        sessions[str(uid)] = sess
        save_file(SESS_FILE, sessions)
        await reply('✅ Session salva! Agora use `/listgroups`.')
        await ensure_client(uid)
        return

    cli = await ensure_client(uid)
    if not cli:
        return await reply('❌ Use `/setsession SUA_SESSION` antes.')

    if txt == '/listgroups':
        diags = await cli.get_dialogs()
        lines = []
        for d in diags:
            if getattr(d.entity, 'megagroup', False) or getattr(d.entity, 'broadcast', False):
                lines.append(f"- `{d.id}` — {d.title or 'Sem título'}")
        return await reply("📋 *Seus grupos:*\n" + "\n".join(lines[:50]), parse_mode='Markdown')

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
        LINK_CACHE.pop(key, None)
        await reply(f'✅ Inscrito em `{gid}`.')
        await ensure_client(uid)
        cli2 = user_clients.get(key)
        if cli2:
            asyncio.create_task(check_access_and_warn(cli2, key, gid))
        return

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
        if key in LINK_CACHE and gid in LINK_CACHE[key]:
            LINK_CACHE[key].pop(gid, None)
        return await reply(f'🗑️ Desinscrito de `{gid}`.')

    return await reply('❓ Comando não reconhecido. `/help`.', parse_mode='Markdown')

# ── FIXOS ────────────────────────────────────────────────────────────────────
@admin_client.on(events.NewMessage)
async def forward_fixed(ev):
    cid = ev.chat_id
    # Sem linked por padrão: só passa se for um dos canais base
    if cid not in SOURCE_CHAT_IDS:
        return
    log.info(f"🔍 [fixed] got message in fixed chat={cid}")
    chat  = await admin_client.get_entity(cid)
    title = getattr(chat, 'title', None) or str(cid)
    await forward_with_fallback(admin_client, ev.message, title, cid)

# ── ENTRYPOINT ───────────────────────────────────────────────────────────────
async def main():
    threading.Thread(target=run_flask, daemon=True).start()
    await asyncio.gather(
        admin_client.start(),
        bot.start(bot_token=BOT_TOKEN)
    )
    try:
        await admin_client.get_entity(DEST_CHAT_ID)
    except Exception:
        pass

    for uid_str in set(list(sessions.keys()) + list(subscriptions.keys())):
        try:
            await ensure_client(int(uid_str))
        except Exception:
            log.exception(f"falha ao iniciar listener {uid_str}")

    log.info("🤖 Bot iniciado!")
    await asyncio.gather(
        admin_client.run_until_disconnected(),
        bot.run_until_disconnected()
    )

if __name__ == '__main__':
    asyncio.run(main())
