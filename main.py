# main.py
import os, json, asyncio, threading, logging
from typing import Dict, List, Set, Optional

from flask import Flask, jsonify
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl import types
from telethon.tl.functions.channels import GetFullChannelRequest

# ── ENV ──────────────────────────────────────────────────────────────────────
API_ID          = int(os.environ['TELEGRAM_API_ID'])
API_HASH        = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN       = os.environ['BOT_TOKEN']
DEST_CHAT_ID    = int(os.environ['DEST_CHAT_ID'])                  # canal destino (Repasse Vips)
SESSION_STRING  = os.environ['SESSION_STRING']                     # sessão USER admin (não bot)
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS','[]'))

_raw_admins = os.environ.get('ADMIN_IDS','[]')
try:
    parsed = json.loads(_raw_admins)
    ADMIN_IDS = {parsed} if isinstance(parsed, int) else set(parsed)
except:
    ADMIN_IDS = set()

# Comentários ativados?
INCLUDE_LINKED_FIXED   = os.environ.get('INCLUDE_LINKED_FIXED','0') == '1'
INCLUDE_LINKED_DYNAMIC = os.environ.get('INCLUDE_LINKED_DYNAMIC','0') == '1'

# Override manual do chat de discussão do destino (recomendado)
DEST_DISCUSS_ID_ENV = os.environ.get('DEST_DISCUSS_ID', '').strip()
DEST_DISCUSS_ID: Optional[int] = int(DEST_DISCUSS_ID_ENV) if DEST_DISCUSS_ID_ENV else None

DATA_DIR     = '/data'
SESS_FILE    = os.path.join(DATA_DIR, 'sessions.json')         # { "uid": "StringSession" }
SUBS_FILE    = os.path.join(DATA_DIR, 'subscriptions.json')    # { "uid": [group_ids...] }
STATE_FILE   = os.path.join(DATA_DIR, 'state.json')            # { "last": {uid: {chat_id: msg_id}} }
PMAP_FILE    = os.path.join(DATA_DIR, 'post_map.json')         # { "srcId:msgId": dest_msg_id }

os.makedirs(DATA_DIR, exist_ok=True)

# ── LOG ──────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger('encaminhador')

# ── HELPERS PERSISTÊNCIA ────────────────────────────────────────────────────
def load_json(path, default):
    try:
        with open(path,'r',encoding='utf-8') as f:
            return json.load(f)
    except:
        return default

def save_json(path, data):
    with open(path,'w',encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

sessions: Dict[str,str]            = load_json(SESS_FILE, {})
subscriptions: Dict[str,List[int]] = load_json(SUBS_FILE, {})
state                              = load_json(STATE_FILE, {'last': {}})
post_map: Dict[str,int]            = load_json(PMAP_FILE, {})

def md_escape(s: Optional[str]) -> str:
    if not s: return ''
    return (s.replace("\\","\\\\")
             .replace("`","\\`")
             .replace("*","\\*")
             .replace("_","\\_"))

def get_last(uid: str, chat_id: int) -> int:
    return int(state.get('last', {}).get(uid, {}).get(str(chat_id), 0))

def set_last(uid: str, chat_id: int, msg_id: int):
    state.setdefault('last', {}).setdefault(uid, {})[str(chat_id)] = int(msg_id)
    save_json(STATE_FILE, state)

def post_key(src_chat_id: int, src_msg_id: int) -> str:
    return f"{src_chat_id}:{src_msg_id}"

def map_post(src_chat_id: int, src_msg_id: int, dest_msg_id: int):
    post_map[post_key(src_chat_id, src_msg_id)] = int(dest_msg_id)
    save_json(PMAP_FILE, post_map)

def get_mapped(src_chat_id: int, src_msg_id: int) -> Optional[int]:
    return post_map.get(post_key(src_chat_id, src_msg_id))

# ── FLASK KEEP-ALIVE ────────────────────────────────────────────────────────
app = Flask('keep_alive')

@app.route('/')
def home(): return 'OK'

@app.route('/dump_subs')
def dump_subs(): return jsonify(subscriptions)

@app.route('/dump_map/<int:src_id>')
def dump_map_src(src_id: int):
    items = {k: v for k, v in post_map.items() if k.startswith(f"{src_id}:")}
    return jsonify(items)

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)), debug=False)

# ── CLIENTES ─────────────────────────────────────────────────────────────────
bot          = TelegramClient('bot_session', API_ID, API_HASH)                        # bot de UI
admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)        # user admin
user_clients: Dict[str, TelegramClient] = {}

# ── DEST DISCUSSION RESOLUTION ───────────────────────────────────────────────
async def resolve_dest_discussion():
    global DEST_DISCUSS_ID
    if DEST_DISCUSS_ID:  # já veio do ENV, ótimo
        try:
            await admin_client.get_entity(DEST_DISCUSS_ID)
            log.info(f"[dest] usando DEST_DISCUSS_ID={DEST_DISCUSS_ID} (ENV)")
            return
        except Exception as e:
            log.info(f"[dest] ENV DEST_DISCUSS_ID inválido? {type(e).__name__}; tentando descobrir automaticamente.")

    try:
        ent = await admin_client.get_entity(DEST_CHAT_ID)
        if isinstance(ent, types.Channel):
            full = await admin_client(GetFullChannelRequest(channel=ent))
            linked = getattr(full.full_chat, 'linked_chat_id', None)
            if linked:
                try:
                    linked_ent = await admin_client.get_entity(types.PeerChannel(linked))
                    if isinstance(linked_ent, types.Channel):
                        DEST_DISCUSS_ID = linked_ent.id
                        log.info(f"[dest] linked discussion descoberto = {DEST_DISCUSS_ID}")
                        return
                except Exception:
                    pass
    except Exception as e:
        log.info(f"[dest] GetFullChannelRequest erro {type(e).__name__}")

    if not DEST_DISCUSS_ID:
        log.info("[dest] sem grupo de discussão vinculado detectado (comentários não serão clonados).")

# ── EXPANSÃO (linked chat) ───────────────────────────────────────────────────
async def expand_allowed(cli: TelegramClient, base_ids: List[int], include_linked: bool) -> Set[int]:
    if not include_linked:
        return set(base_ids)
    allowed: Set[int] = set(base_ids)
    for base in base_ids:
        try:
            ent = await cli.get_entity(base)
            if isinstance(ent, types.Channel):
                full = await cli(GetFullChannelRequest(channel=ent))
                linked = getattr(full.full_chat, 'linked_chat_id', None)
                if linked:
                    try:
                        linked_ent = await cli.get_entity(types.PeerChannel(linked))
                        if isinstance(linked_ent, types.Channel):
                            allowed.add(linked_ent.id)
                    except Exception:
                        pass
        except Exception:
            pass
    return allowed

def is_channel_post(msg) -> bool:
    return isinstance(msg.to_id, (types.PeerChannel,)) and not getattr(msg, 'reply_to', None)

def is_comment_message(msg) -> bool:
    rt = getattr(msg, 'reply_to', None)
    return bool(rt and getattr(rt, 'reply_to_msg_id', None))

async def author_label(cli: TelegramClient, msg) -> str:
    try:
        s = await msg.get_sender()
        if not s:
            return "Usuário"
        name = (getattr(s, 'first_name', '') or '') + (' ' + getattr(s, 'last_name','') if getattr(s,'last_name',None) else '')
        name = name.strip()
        uname = getattr(s, 'username', None)
        if uname:
            return f"{name or 'Usuário'} (@{uname})"
        return name or f"ID {getattr(s,'id','')}"
    except Exception:
        return "Usuário"

# ── CLONAGEM ─────────────────────────────────────────────────────────────────
async def clone_post(src_client: TelegramClient, message, header_title: str, src_chat_id: int) -> Optional[int]:
    header = f"📢 *{md_escape(header_title)}* (`{src_chat_id}`)"
    try:
        # pré-resolve o destino para evitar "Could not find input entity"
        await admin_client.get_entity(DEST_CHAT_ID)
        await admin_client.send_message(DEST_CHAT_ID, header, parse_mode='Markdown')
    except Exception:
        pass

    try:
        if message.media:
            path = await src_client.download_media(message)
            sent = await admin_client.send_file(DEST_CHAT_ID, path, caption=(message.text or ''))
        else:
            sent = await admin_client.send_message(DEST_CHAT_ID, message.text or '')
        return sent.id if sent else None
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        try:
            if message.media:
                path = await src_client.download_media(message)
                sent = await admin_client.send_file(DEST_CHAT_ID, path, caption=(message.text or ''))
            else:
                sent = await admin_client.send_message(DEST_CHAT_ID, message.text or '')
            return sent.id if sent else None
        except Exception as e2:
            log.exception(e2)
            return None
    except Exception as e:
        log.exception(e)
        return None

async def clone_comment(src_client: TelegramClient, comment_msg, src_post_id: int, src_channel_id: Optional[int]):
    if DEST_DISCUSS_ID is None:
        return
    if not src_channel_id:
        return
    dest_post_id = get_mapped(src_channel_id, src_post_id)
    if not dest_post_id:
        return

    prefix = f"💬 **{md_escape(await author_label(src_client, comment_msg))}**:\n"
    text = (comment_msg.text or '').strip()
    body = prefix + (text or '')

    try:
        # pré-resolve o grupo de discussão de destino
        await admin_client.get_entity(DEST_DISCUSS_ID)
        if comment_msg.media:
            path = await src_client.download_media(comment_msg)
            await admin_client.send_file(
                DEST_DISCUSS_ID,
                path,
                caption=body or prefix,
                reply_to=dest_post_id
            )
        else:
            await admin_client.send_message(
                DEST_DISCUSS_ID,
                body or prefix,
                reply_to=dest_post_id,
                parse_mode='Markdown'
            )
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds+1)
        try:
            if comment_msg.media:
                path = await src_client.download_media(comment_msg)
                await admin_client.send_file(
                    DEST_DISCUSS_ID,
                    path,
                    caption=body or prefix,
                    reply_to=dest_post_id
                )
            else:
                await admin_client.send_message(
                    DEST_DISCUSS_ID,
                    body or prefix,
                    reply_to=dest_post_id,
                    parse_mode='Markdown'
                )
        except Exception as e2:
            log.exception(e2)
    except Exception as e:
        log.exception(e)

# ── LISTENERS FIXOS ─────────────────────────────────────────────────────────
@admin_client.on(events.NewMessage)
async def fixed_handler(ev):
    cid = ev.chat_id
    if cid == DEST_CHAT_ID or cid == (DEST_DISCUSS_ID or 0):
        return

    if not hasattr(fixed_handler, "_allowed"):
        fixed_handler._base = set(SOURCE_CHAT_IDS)
        if INCLUDE_LINKED_FIXED:
            fixed_handler._allowed = await expand_allowed(admin_client, SOURCE_CHAT_IDS, include_linked=True)
        else:
            fixed_handler._allowed = set(SOURCE_CHAT_IDS)

    allowed = fixed_handler._allowed
    if cid not in allowed:
        return

    msg = ev.message

    # post de canal (base)
    if cid in fixed_handler._base and is_channel_post(msg):
        chat  = await admin_client.get_entity(cid)
        title = getattr(chat, 'title', None) or str(cid)
        dest_id = await clone_post(admin_client, msg, title, cid)
        if dest_id:
            map_post(cid, msg.id, dest_id)
        log.info(f"🔍 [fixed] post cid={cid} mapped={bool(dest_id)}")
        return

    # comentário (se habilitado)
    if INCLUDE_LINKED_FIXED and is_comment_message(msg):
        rt = msg.reply_to
        src_post_id = getattr(rt, 'reply_to_msg_id', None)
        src_peer_id = getattr(rt, 'reply_to_peer_id', None)  # canal original
        if src_post_id and src_peer_id:
            await clone_comment(admin_client, msg, src_post_id, src_peer_id)
        return

# ── LISTENERS DINÂMICOS ─────────────────────────────────────────────────────
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

    base_ids = subscriptions.get(key, [])
    dyn_allowed = await expand_allowed(cli, base_ids, include_linked=INCLUDE_LINKED_DYNAMIC)
    base_set = set(base_ids)

    @cli.on(events.NewMessage)
    async def dynamic_handler(ev):
        cid = ev.chat_id
        if cid == DEST_CHAT_ID or cid == (DEST_DISCUSS_ID or 0):
            return
        if cid not in dyn_allowed:
            return

        msg = ev.message

        # post de canal (base)
        if cid in base_set and is_channel_post(msg):
            chat  = await cli.get_entity(cid)
            title = getattr(chat, 'title', None) or str(cid)
            dest_id = await clone_post(cli, msg, title, cid)
            if dest_id:
                map_post(cid, msg.id, dest_id)
                set_last(key, cid, msg.id)
            log.info(f"🔍 [dynamic] user={key} post cid={cid} mapped={bool(dest_id)}")
            return

        # comentário (se habilitado)
        if INCLUDE_LINKED_DYNAMIC and is_comment_message(msg):
            rt = msg.reply_to
            src_post_id = getattr(rt, 'reply_to_msg_id', None)
            src_peer_id = getattr(rt, 'reply_to_peer_id', None)
            if src_post_id and src_peer_id:
                await clone_comment(cli, msg, src_post_id, src_peer_id)
            return

    # poller apenas para posts (suave)
    async def poller_posts():
        while True:
            try:
                for chat_id in base_ids:
                    last = get_last(key, chat_id)
                    try:
                        msgs = await cli.get_messages(chat_id, limit=5)
                    except Exception:
                        msgs = []
                    for m in reversed(msgs or []):
                        if not m.id or m.id <= last:
                            continue
                        if is_channel_post(m):
                            ent = await cli.get_entity(chat_id)
                            title = getattr(ent, 'title', None) or str(chat_id)
                            dest_id = await clone_post(cli, m, title, chat_id)
                            if dest_id:
                                map_post(chat_id, m.id, dest_id)
                                set_last(key, chat_id, m.id)
            except Exception as e:
                log.info(f"[poller] user={key} erro {type(e).__name__}")
            await asyncio.sleep(30)

    asyncio.create_task(poller_posts())
    asyncio.create_task(cli.run_until_disconnected())
    return cli

# ── BOT DE UI (comandos essenciais de admin e público) ──────────────────────
@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def ui_handler(ev):
    uid, txt, reply = ev.sender_id, ev.raw_text.strip(), ev.reply

    # Admin
    if txt.startswith('/admin_set_session '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, sess = txt.split(' ',2)
            sessions[user_id] = sess
            save_json(SESS_FILE, sessions)
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
                save_json(SUBS_FILE, subscriptions)
                await reply(f'✅ `{user_id}` inscrito em `{gid}`.')
                await ensure_client(int(user_id))
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
                save_json(SUBS_FILE, subscriptions)
                await reply(f'🗑️ `{user_id}` desinscrito de `{gid}`.')
        except:
            return await reply('❌ Uso: `/admin_unsubscribe USER_ID GROUP_ID`')
        return

    # Público
    if txt in ('/start','/help'):
        return await reply(
            "👋 *Encaminhador/Clonador*\n\n"
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
        save_json(SESS_FILE, sessions)
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
            ent = d.entity
            if getattr(ent, 'megagroup', False) or getattr(ent, 'broadcast', False):
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
        save_json(SUBS_FILE, subscriptions)
        await reply(f'✅ Inscrito em `{gid}`.')
        await ensure_client(uid)
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
        save_json(SUBS_FILE, subscriptions)
        return await reply(f'🗑️ Desinscrito de `{gid}`.')

    return await reply('❓ Comando não reconhecido. `/help`.', parse_mode='Markdown')

# ── ENTRYPOINT ───────────────────────────────────────────────────────────────
async def main():
    # Flask (keep-alive + inspeções)
    threading.Thread(target=run_flask, daemon=True).start()

    # Sobe admin e bot
    await asyncio.gather(
        admin_client.start(),
        bot.start(bot_token=BOT_TOKEN)
    )

    # Pré-cache entidades de destino para evitar "input entity" issues
    try:
        await admin_client.get_entity(DEST_CHAT_ID)
    except Exception:
        pass

    await resolve_dest_discussion()
    if DEST_DISCUSS_ID:
        try:
            await admin_client.get_entity(DEST_DISCUSS_ID)
        except Exception:
            pass

    # Inicializa listeners de todos já cadastrados
    for uid_str in set(list(sessions.keys()) + list(subscriptions.keys())):
        try:
            await ensure_client(int(uid_str))
        except Exception as e:
            log.exception(f"falha ao iniciar listener {uid_str}: {e}")

    log.info("🤖 Bot iniciado!")
    await asyncio.gather(
        admin_client.run_until_disconnected(),
        bot.run_until_disconnected()
    )

if __name__ == '__main__':
    asyncio.run(main())
