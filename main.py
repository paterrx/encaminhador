import os
import json
import time
import asyncio
import threading
import logging
from typing import Dict, List, Optional

from flask import Flask, jsonify
from telethon import TelegramClient, events, errors, functions
from telethon.sessions import StringSession
from telethon.tl.types import Message

# ───────────────────────────── CONFIG ─────────────────────────────
API_ID          = int(os.environ['TELEGRAM_API_ID'])
API_HASH        = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN       = os.environ['BOT_TOKEN']

# Para posts (canal principal)
DEST_CHAT_ID    = int(os.environ['DEST_CHAT_ID'])

# Para comentários (grupo/discuss de destino). Se 0, não replica comentários
DEST_DISCUSS_ID = int(os.environ.get('DEST_DISCUSS_ID', '0') or 0)

# Admin (para canais fixos)
SESSION_STRING  = os.environ['SESSION_STRING']
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS', '[]'))

# Incluir chats vinculados para usuários dinâmicos
INCLUDE_LINKED_DYNAMIC = bool(int(os.environ.get('INCLUDE_LINKED_DYNAMIC', '1')))

# Persistência
DATA_DIR  = '/data'
SESS_FILE = os.path.join(DATA_DIR, 'sessions.json')
SUBS_FILE = os.path.join(DATA_DIR, 'subscriptions.json')
AUD_FILE  = os.path.join(DATA_DIR, 'audit.json')
POSTMAP_FILE = os.path.join(DATA_DIR, 'postmap.json')   # novo

os.makedirs(DATA_DIR, exist_ok=True)

# ───────────────────────────── LOG ─────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("encaminhador")

# ─────────────────────── UTIL: JSON persistente ───────────────────────
def _load_json(path: str, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

sessions: Dict[str, str]               = _load_json(SESS_FILE, {})
subscriptions: Dict[str, List[int]]    = _load_json(SUBS_FILE, {})
audit: List[dict]                      = _load_json(AUD_FILE, [])
postmap: Dict[str, int]                = _load_json(POSTMAP_FILE, {})  # (base_id:msg_id) -> dest_msg_id

def audit_push(event: dict):
    event['ts'] = int(time.time())
    audit.append(event)
    del audit[:-500]
    _save_json(AUD_FILE, audit)

def _key(base_id: int, msg_id: int) -> str:
    return f"{base_id}:{msg_id}"

# ─────────────────────── FLASK keep-alive ───────────────────────
app = Flask("keep_alive")

@app.route("/")
def home(): return "OK"

@app.route("/dump_subs")
def dump_subs(): return jsonify(subscriptions)

@app.route("/dump_audit")
def dump_audit(): return jsonify(audit)

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)

# ─────────────────────── CLIENTES TELETHON ───────────────────────
bot = TelegramClient("bot_session", API_ID, API_HASH)
admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# destinos cacheados
DEST_POSTS_ENTITY = None         # canal onde as apostas vão
DEST_COMMENTS_ENTITY = None      # grupo de comentários

# mapeia chat_de_discussão -> canal_base
linked_of: Dict[int, int] = {}

# ─────────────── Descoberta de chat vinculado ───────────────
async def resolve_linked_for(client: TelegramClient, channel_id: int) -> int:
    """
    Retorna o chat de discussão (id negativo -100...) vinculado ao canal `channel_id`.
    1) tenta API oficial (GetFullChannel / GetFullChannelRequest)
    2) fallback por nome ( "<title> chat" )
    Retorna 0 se não achar.
    """
    try:
        ent = await client.get_entity(channel_id)
    except Exception as e:
        log.debug(f"[linked] get_entity fail {channel_id}: {e}")
        return 0

    # 1) API oficial
    try:
        req_cls = getattr(functions.channels, 'GetFullChannel', None)
        if req_cls is not None:
            full = await client(req_cls(channel=ent))
        else:
            from telethon.tl.functions.channels import GetFullChannelRequest
            full = await client(GetFullChannelRequest(channel=ent))
        lc = getattr(full.full_chat, 'linked_chat_id', None)
        if lc:
            # garante formato -100...
            return int(f"-100{lc}") if lc > 0 else lc
    except Exception as e:
        log.debug(f"[linked] API resolve failed for {channel_id}: {e}")

    # 2) por nome (usuário precisa estar no chat)
    try:
        base = (getattr(ent, 'title', '') or '').strip().lower()
        if base:
            candidates = {
                f"{base} chat",
                f"{base} - chat",
                f"{base} • chat",
                f"{base} – chat",
            }
            async for d in client.iter_dialogs():
                title = (getattr(d.entity, 'title', '') or '').strip().lower()
                if not title:
                    continue
                if getattr(d.entity, 'megagroup', False):
                    if title in candidates or (title.startswith(base) and 'chat' in title):
                        log.info(f"[linked] guessed by name: {channel_id} -> {d.entity.id} ({title})")
                        return int(d.entity.id)
    except Exception as e:
        log.debug(f"[linked] name fallback failed for {channel_id}: {e}")
    return 0

# ─────────────── Forward/cópia com retorno do ID clonado ───────────────
async def forward_with_fallback(send_client: TelegramClient, m: Message, header: str) -> Optional[int]:
    """
    Envia 'header' + m para DEST_POSTS_ENTITY.
    Retorna o id da mensagem clonada (no destino) para podermos mapear comentários.
    """
    # header
    try:
        await send_client.send_message(DEST_POSTS_ENTITY, header, parse_mode='Markdown')
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        await send_client.send_message(DEST_POSTS_ENTITY, header, parse_mode='Markdown')

    # 1) forward, pegando o objeto resultante
    try:
        sent = await send_client.forward_messages(DEST_POSTS_ENTITY, m)
        if isinstance(sent, list):
            sent = sent[0]
        if sent:
            return sent.id
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
    except Exception:
        pass

    # 2) fallback (download+reenviar)
    try:
        if m.media:
            path = await m.download_media()
            sent = await send_client.send_file(DEST_POSTS_ENTITY, path, caption=(m.text or ''))
            return getattr(sent, 'id', None)
        else:
            sent = await send_client.send_message(DEST_POSTS_ENTITY, m.text or '')
            return getattr(sent, 'id', None)
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        if m.media:
            path = await m.download_media()
            sent = await send_client.send_file(DEST_POSTS_ENTITY, path, caption=(m.text or ''))
            return getattr(sent, 'id', None)
        else:
            sent = await send_client.send_message(DEST_POSTS_ENTITY, m.text or '')
            return getattr(sent, 'id', None)
    except Exception:
        return None

# ─────────────── Replicação de comentários ───────────────
async def replicate_comment(src_client: TelegramClient, ev: events.NewMessage.Event):
    """
    Copia um comentário do chat vinculado para DEST_COMMENTS_ENTITY,
    respondendo ao post clonado correto.
    """
    if not DEST_COMMENTS_ENTITY:
        return

    base_id = linked_of.get(ev.chat_id)
    if not base_id:
        return

    orig_post_id = getattr(ev.message, 'reply_to_msg_id', None)
    if not orig_post_id:
        return  # comentários de discussão SEM reply explícito (pouco comum), ignoramos

    dest_post_id = postmap.get(_key(base_id, orig_post_id))
    if not dest_post_id:
        # ainda não mapeamos esse post (por ex, foi clonado antes do bot subir)
        return

    # monta "autor"
    try:
        s = await src_client.get_entity(ev.sender_id)
        name = (getattr(s, 'first_name', '') or '') + (' ' + getattr(s, 'last_name', '') if getattr(s, 'last_name', None) else '')
        user = f"{name.strip() or 'Usuário'}"
        if getattr(s, 'username', None):
            user += f" (@{s.username})"
    except Exception:
        user = "Usuário"

    header = f"💬 {user}"

    try:
        # primeiro uma linha de cabeçalho (opcional)
        await bot.send_message(DEST_COMMENTS_ENTITY, header, reply_to=dest_post_id)

        # depois o conteúdo do comentário
        if ev.message.media:
            path = await ev.message.download_media()
            await bot.send_file(DEST_COMMENTS_ENTITY, path, caption=(ev.message.text or ''), reply_to=dest_post_id)
        else:
            await bot.send_message(DEST_COMMENTS_ENTITY, ev.message.text or '', reply_to=dest_post_id)
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        # tenta só o conteúdo
        if ev.message.media:
            path = await ev.message.download_media()
            await bot.send_file(DEST_COMMENTS_ENTITY, path, caption=(ev.message.text or ''), reply_to=dest_post_id)
        else:
            await bot.send_message(DEST_COMMENTS_ENTITY, ev.message.text or '', reply_to=dest_post_id)

# ─────────────── Admin: canais fixos (sua session) ───────────────
@admin_client.on(events.NewMessage)
async def fixed_listener(ev: events.NewMessage.Event):
    cid = ev.chat_id
    if cid in SOURCE_CHAT_IDS:
        # post do canal fixo
        chat  = await admin_client.get_entity(cid)
        title = getattr(chat, 'title', None) or str(cid)
        header = f"📢 *{title}* (`{cid}`)"
        dest_id = await forward_with_fallback(bot, ev.message, header)
        if dest_id:
            postmap[_key(cid, ev.message.id)] = dest_id
            _save_json(POSTMAP_FILE, postmap)
        return

    # comentário em chat vinculado de algum canal fixo?
    if DEST_DISCUSS_ID and cid in linked_of:
        await replicate_comment(admin_client, ev)

# ─────────────── Usuários dinâmicos ───────────────
user_clients: Dict[str, TelegramClient] = {}
allowed_map: Dict[str, set] = {}   # uid -> set(chat_ids permitidos, incluindo linked)

async def ensure_client(uid: int) -> Optional[TelegramClient]:
    key = str(uid)
    if key in user_clients:
        return user_clients[key]

    sess = sessions.get(key)
    if not sess:
        return None

    try:
        cli = TelegramClient(StringSession(sess), API_ID, API_HASH)
        await cli.start()
    except Exception:
        sessions.pop(key, None)
        _save_json(SESS_FILE, sessions)
        await bot.send_message(uid, "🚫 Session inválida. Use `/setsession SUA_SESSION`.")
        return None

    user_clients[key] = cli
    allowed_map[key] = set(subscriptions.get(key, []))

    # inclui chats vinculados
    if INCLUDE_LINKED_DYNAMIC and allowed_map[key]:
        base_list = list(allowed_map[key])
        for base_id in base_list:
            try:
                linked = await resolve_linked_for(cli, base_id)
                if linked:
                    allowed_map[key].add(linked)
                    linked_of[linked] = base_id
            except Exception as e:
                log.info(f"[expand] uid={uid} base_id={base_id} falha={type(e).__name__}")

    @cli.on(events.NewMessage)
    async def forward_user(ev: events.NewMessage.Event):
        aid = allowed_map.get(key, set())
        if ev.chat_id not in aid:
            return

        # Se veio do chat vinculado, trata como comentário.
        if DEST_DISCUSS_ID and ev.chat_id in linked_of:
            await replicate_comment(cli, ev)
            return

        # Caso contrário, é um post do canal base do usuário.
        chat  = await cli.get_entity(ev.chat_id)
        title = getattr(chat, 'title', None) or str(ev.chat_id)
        header = f"📢 *{title}* (`{ev.chat_id}`)"
        dest_id = await forward_with_fallback(bot, ev.message, header)
        if dest_id:
            postmap[_key(ev.chat_id, ev.message.id)] = dest_id
            _save_json(POSTMAP_FILE, postmap)

    asyncio.create_task(cli.run_until_disconnected())
    return cli

# ─────────────── BOT UI (DM) ───────────────
@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def ui(ev: events.NewMessage.Event):
    uid = ev.sender_id
    txt = (ev.raw_text or '').strip()
    reply = ev.reply

    async def _is_admin() -> bool:
        admins_env = os.environ.get('ADMIN_IDS', '[]')
        try:
            parsed = json.loads(admins_env)
            if isinstance(parsed, int):
                return uid == parsed
            elif isinstance(parsed, list):
                return uid in parsed
        except Exception:
            pass
        try:
            single = int(os.environ.get('ADMIN_ID', '0') or 0)
            if single:
                return uid == single
        except Exception:
            pass
        return False

    if txt in ('/start', '/help'):
        return await reply(
            "👋 *Bem-vindo ao Encaminhador Bot*\n\n"
            "1⃣ `/myid`\n"
            "2⃣ `/setsession SUA_SESSION`\n"
            "3⃣ `/listgroups`\n"
            "4⃣ `/subscribe GROUP_ID`\n"
            "5⃣ `/unsubscribe GROUP_ID`\n\n"
            "🔧 *Admin*\n"
            "`/admin_set_session USER_ID SESSION`\n"
            "`/admin_subscribe USER_ID GROUP_ID`\n"
            "`/admin_unsubscribe USER_ID GROUP_ID`\n"
            "`/admin_whois_session SESSION`\n"
            "`/admin_listgroups_by_session SESSION [limite]`\n",
            parse_mode='Markdown'
        )

    if txt == '/myid':
        return await reply(f"🆔 `{uid}`", parse_mode='Markdown')

    # público
    if txt.startswith('/setsession '):
        sess = txt.split(' ', 1)[1].strip()
        sessions[str(uid)] = sess
        _save_json(SESS_FILE, sessions)
        await reply("✅ Session salva! Agora use `/listgroups`.")
        await ensure_client(uid)
        return

    if txt == '/listgroups':
        cli = await ensure_client(uid)
        if not cli:
            return await reply("⚠️ Primeiro use `/setsession SUA_SESSION`.")
        lines = []
        async for d in cli.iter_dialogs():
            ent = d.entity
            cid = getattr(ent, 'id', None)
            title = getattr(ent, 'title', None) or getattr(ent, 'username', None) or str(cid)
            if getattr(ent, 'megagroup', False) or getattr(ent, 'broadcast', False):
                lines.append(f"- `{cid}` — {title}")
                if len(lines) >= 50:
                    break
        return await reply("📋 *Seus grupos:*\n" + ("\n".join(lines) if lines else "Nenhum."), parse_mode='Markdown')

    if txt.startswith('/subscribe '):
        try:
            gid = int(txt.split(' ', 1)[1])
        except Exception:
            return await reply("ID inválido.")
        lst = subscriptions.setdefault(str(uid), [])
        if gid in lst:
            return await reply("⚠️ Já inscrito.")
        lst.append(gid)
        _save_json(SUBS_FILE, subscriptions)
        await ensure_client(uid)
        return await reply(f"✅ Inscrito em `{gid}`.", parse_mode='Markdown')

    if txt.startswith('/unsubscribe '):
        try:
            gid = int(txt.split(' ', 1)[1])
        except Exception:
            return await reply("ID inválido.")
        lst = subscriptions.get(str(uid), [])
        if gid not in lst:
            return await reply("⚠️ Não estava inscrito.")
        lst.remove(gid)
        _save_json(SUBS_FILE, subscriptions)
        await ensure_client(uid)
        return await reply(f"🗑️ Removido `{gid}`.", parse_mode='Markdown')

    # admin-only
    if txt.startswith('/admin_') and not await _is_admin():
        return await reply("🚫 Sem permissão.")

    if txt.startswith('/admin_set_session '):
        try:
            _, u, sess = txt.split(' ', 2)
            sessions[u] = sess
            _save_json(SESS_FILE, sessions)
            await ensure_client(int(u))
            return await reply(f"✅ Session de `{u}` registrada e listener ativo.", parse_mode='Markdown')
        except Exception:
            return await reply("Uso: `/admin_set_session USER_ID SESSION`", parse_mode='Markdown')

    if txt.startswith('/admin_subscribe '):
        try:
            _, u, g = txt.split(' ', 2)
            gid = int(g)
            lst = subscriptions.setdefault(u, [])
            if gid in lst:
                return await reply("⚠️ Já inscrito.")
            lst.append(gid)
            _save_json(SUBS_FILE, subscriptions)
            await ensure_client(int(u))
            return await reply(f"✅ `{u}` inscrito em `{gid}`.", parse_mode='Markdown')
        except Exception:
            return await reply("Uso: `/admin_subscribe USER_ID GROUP_ID`", parse_mode='Markdown')

    if txt.startswith('/admin_unsubscribe '):
        try:
            _, u, g = txt.split(' ', 2)
            gid = int(g)
            lst = subscriptions.get(u, [])
            if gid not in lst:
                return await reply("⚠️ Não inscrito.")
            lst.remove(gid)
            _save_json(SUBS_FILE, subscriptions)
            await ensure_client(int(u))
            return await reply(f"🗑️ `{u}` removido de `{gid}`.", parse_mode='Markdown')
        except Exception:
            return await reply("Uso: `/admin_unsubscribe USER_ID GROUP_ID`", parse_mode='Markdown')

    if txt.startswith('/admin_whois_session '):
        sess = txt.split(' ', 1)[1].strip()
        try:
            tmp = TelegramClient(StringSession(sess), API_ID, API_HASH)
            await tmp.start()
            me = await tmp.get_me()
            info = [f"👤 *Owner ID:* `{me.id}`"]
            if me.username:
                info.append(f"Username: @{me.username}")
            await reply("\n".join(info), parse_mode='Markdown')
            await tmp.disconnect()
            return
        except Exception as e:
            return await reply(f"❌ Falha: {type(e).__name__}")

    if txt.startswith('/admin_listgroups_by_session '):
        parts = txt.split(' ', 2)
        sess = parts[1].strip()
        limit = 40
        if len(parts) == 3:
            try:
                limit = max(1, min(100, int(parts[2])))
            except Exception:
                pass
        try:
            tmp = TelegramClient(StringSession(sess), API_ID, API_HASH)
            await tmp.start()
            me = await tmp.get_me()
            lines = [f"👤 *Owner ID:* `{me.id}`", "📋 *Grupos/Canais:*"]
            count = 0
            async for d in tmp.iter_dialogs():
                ent = d.entity
                cid = getattr(ent, 'id', None)
                if getattr(ent, 'megagroup', False) or getattr(ent, 'broadcast', False):
                    title = getattr(ent, 'title', None) or getattr(ent, 'username', None) or str(cid)
                    lines.append(f"- `{cid}` — {title}")
                    count += 1
                    if count >= limit:
                        break
            await reply("\n".join(lines) if count else "Nenhum grupo/canal.", parse_mode='Markdown')
            await tmp.disconnect()
            return
        except Exception as e:
            return await reply(f"❌ Falha: {type(e).__name__}")

    return await reply("❓ Comando não reconhecido. Use /help.")

# ─────────────── Backfill do mapa de posts ───────────────
async def backfill_postmap():
    """
    Lê as últimas mensagens do canal de destino e preenche postmap para
    qualquer mensagem encaminhada (fwd_from) que ainda não esteja mapeada.
    """
    filled = 0
    async for m in bot.iter_messages(DEST_POSTS_ENTITY, limit=300):
        fwd = getattr(m, 'forward', None)
        if not fwd:
            continue
        chan_id = getattr(fwd, 'channel_id', None)
        chan_post = getattr(fwd, 'channel_post', None)
        if not chan_id or not chan_post:
            continue
        base_id = int(f"-100{chan_id}")
        k = _key(base_id, chan_post)
        if k not in postmap:
            postmap[k] = m.id
            filled += 1
    if filled:
        _save_json(POSTMAP_FILE, postmap)
        log.info(f"[backfill] novos mapeamentos: {filled}")

# ─────────────── MAIN ───────────────
async def main():
    threading.Thread(target=run_flask, daemon=True).start()

    await asyncio.gather(
        bot.start(bot_token=BOT_TOKEN),
        admin_client.start()
    )

    global DEST_POSTS_ENTITY, DEST_COMMENTS_ENTITY
    try:
        DEST_POSTS_ENTITY = await bot.get_entity(DEST_CHAT_ID)
    except Exception:
        DEST_POSTS_ENTITY = DEST_CHAT_ID

    if DEST_DISCUSS_ID:
        try:
            DEST_COMMENTS_ENTITY = await bot.get_entity(DEST_DISCUSS_ID)
            log.info(f"[dest] usando DEST_DISCUSS_ID={DEST_DISCUSS_ID} (ENV)")
        except Exception:
            DEST_COMMENTS_ENTITY = DEST_DISCUSS_ID

    # prepara linked_of para os canais fixos
    for cid in SOURCE_CHAT_IDS:
        linked = await resolve_linked_for(admin_client, cid)
        if linked:
            linked_of[linked] = cid
            log.info(f"[discover] base={cid} discuss={linked}")

    # backfill do mapa (para comentários começarem a funcionar também em posts antigos)
    await backfill_postmap()

    log.info("🤖 Bot iniciado!")
    await asyncio.gather(
        bot.run_until_disconnected(),
        admin_client.run_until_disconnected()
    )

if __name__ == "__main__":
    asyncio.run(main())
