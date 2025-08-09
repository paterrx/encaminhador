# main.py
import os
import json
import time
import asyncio
import threading
import logging
import hashlib
from typing import Dict, List, Optional, Set, Tuple

from flask import Flask, jsonify
from telethon import TelegramClient, events, errors, functions
from telethon.sessions import StringSession
from telethon.tl.types import Message

# ───────────────────────────── CONFIG ─────────────────────────────
API_ID          = int(os.environ['TELEGRAM_API_ID'])
API_HASH        = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN       = os.environ['BOT_TOKEN']                 # só DM/comandos
DEST_CHAT_ID    = int(os.environ['DEST_CHAT_ID'])         # canal destino dos posts

# Sua conta (envia e ouve fixos)
SESSION_STRING  = os.environ['SESSION_STRING']
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS', '[]'))

# Dinâmicos
# OBS: deixe 0 pra NÃO ouvir o chat vinculado (megagroup). 1 se quiser incluir.
INCLUDE_LINKED_DYNAMIC = bool(int(os.environ.get('INCLUDE_LINKED_DYNAMIC', '0')))

# Persistência
DATA_DIR   = '/data'
SESS_FILE  = os.path.join(DATA_DIR, 'sessions.json')
SUBS_FILE  = os.path.join(DATA_DIR, 'subscriptions.json')
AUD_FILE   = os.path.join(DATA_DIR, 'audit.json')

os.makedirs(DATA_DIR, exist_ok=True)

# ───────────────────────────── LOG ─────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("encaminhador")

# ─────────────────────── JSON util ───────────────────────
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

sessions: Dict[str, str]            = _load_json(SESS_FILE, {})
subscriptions: Dict[str, List[int]] = _load_json(SUBS_FILE, {})
audit: List[dict]                   = _load_json(AUD_FILE, [])

def audit_push(event: dict):
    event['ts'] = int(time.time())
    audit.append(event)
    del audit[:-500]
    _save_json(AUD_FILE, audit)

# ─────────────────────── normalização de IDs ───────────────────────
def normalize_gid(gid: int) -> int:
    """Garante formato -100xxxxxxxxxxxx para canais/supergrupos."""
    s = str(gid)
    if s.startswith('-100'):
        return int(s)
    if s.startswith('-'):
        return int('-100' + s[1:])
    return gid

def migrate_sub_ids():
    changed = False
    for uid, lst in list(subscriptions.items()):
        nlst = []
        for g in lst:
            try:
                ng = normalize_gid(int(g))
            except Exception:
                continue
            nlst.append(ng)
            if ng != g:
                changed = True
        subscriptions[uid] = sorted(list(set(nlst)))
    if changed:
        _save_json(SUBS_FILE, subscriptions)
        log.info("[migrate] subscriptions normalizadas para formato -100…")

# ─────────────────────── FLASK keep-alive ───────────────────────
app = Flask("keep_alive")

@app.route("/")
def home(): return "OK"

@app.route("/dump_subs")
def dump_subs(): return jsonify(subscriptions)

@app.route("/dump_audit")
def dump_audit(): return jsonify(audit)

def _sanitize_sessions_dict(d: dict) -> dict:
    out = {}
    for uid, sess in (d or {}).items():
        if not sess:
            continue
        try:
            fp = hashlib.sha256(sess.encode("utf-8")).hexdigest()[:10]
            preview = f"{sess[:6]}…{sess[-6:]}" if len(sess) >= 14 else "…"
            out[str(uid)] = {
                "fingerprint": fp,
                "preview": preview,
                "length": len(sess),
                "has_subs": bool(subscriptions.get(str(uid))),
            }
        except Exception:
            pass
    return out

@app.route("/dump_sessions")
def dump_sessions():
    return jsonify(_sanitize_sessions_dict(_load_json(SESS_FILE, {})))

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)

# ─────────────────────── Telethon clients ───────────────────────
# bot: apenas para conversar no PV com você (comandos). NÃO posta no canal.
bot = TelegramClient("bot_session", API_ID, API_HASH)

# sua conta (admin): escuta canais fixos E envia para o destino
admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# Quem realmente envia:
SENDER = admin_client
DEST_ENTITY: Optional[object] = None  # cache do destino resolvido pelo SENDER

# DEDUPE simples em memória (evita reenvio da mesma mensagem)
DEDUP_MAX = 5000
_seen: Set[Tuple[int, int]] = set()

def seen(chat_id: int, msg_id: int) -> bool:
    key = (int(chat_id), int(msg_id))
    if key in _seen:
        return True
    _seen.add(key)
    # limpeza simples
    if len(_seen) > DEDUP_MAX:
        for _ in range(len(_seen) - DEDUP_MAX):
            _seen.pop()
    return False

# ─────────────── Descoberta de chat vinculado (para dinâmicos se habilitado) ───────────────
async def resolve_linked_for(client: TelegramClient, channel_id: int) -> int:
    try:
        ent = await client.get_entity(channel_id)
    except Exception as e:
        log.debug(f"[linked] get_entity fail {channel_id}: {e}")
        return 0

    # API oficial
    try:
        req_cls = getattr(functions.channels, 'GetFullChannel', None)
        if req_cls is not None:
            full = await client(req_cls(channel=ent))
        else:
            from telethon.tl.functions.channels import GetFullChannelRequest
            full = await client(GetFullChannelRequest(channel=ent))
        lc = getattr(full.full_chat, 'linked_chat_id', None)
        if lc:
            return int(f"-100{lc}") if lc > 0 else int(lc)
    except Exception:
        pass
    return 0

# ─────────────── Encaminhar/cópia com fallback (ENVIO PELA SUA SESSÃO) ───────────────
async def forward_with_fallback(m: Message, header: str):
    # 1) header
    try:
        await SENDER.send_message(DEST_ENTITY, header, parse_mode='Markdown')
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        await SENDER.send_message(DEST_ENTITY, header, parse_mode='Markdown')

    # 2) tenta forward
    try:
        await m.forward_to(DEST_ENTITY)
        return
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
    except Exception:
        pass

    # 3) fallback: download + reenvio
    try:
        if m.media:
            path = await m.download_media()
            await SENDER.send_file(DEST_ENTITY, path, caption=(m.text or ''))
        else:
            await SENDER.send_message(DEST_ENTITY, m.text or '')
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        if m.media:
            path = await m.download_media()
            await SENDER.send_file(DEST_ENTITY, path, caption=(m.text or ''))
        else:
            await SENDER.send_message(DEST_ENTITY, m.text or '')

# ─────────────── Admin: canais fixos (sua session) ───────────────
@admin_client.on(events.NewMessage)
async def fixed_listener(ev: events.NewMessage.Event):
    cid = ev.chat_id
    if cid not in SOURCE_CHAT_IDS:
        return
    if seen(cid, ev.message.id):
        return
    log.info(f"🔍 [fixed] got message in fixed chat={cid}")
    chat  = await admin_client.get_entity(cid)
    title = getattr(chat, 'title', None) or str(cid)
    header = f"📢 *{title}* (`{cid}`)"
    await forward_with_fallback(ev.message, header)

# ─────────────── Usuários dinâmicos ───────────────
user_clients: Dict[str, TelegramClient] = {}
allowed_map: Dict[str, set] = {}   # uid -> set(chat_ids permitidos, incluindo linked quando habilitado)

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
    # base permitidos (normalizados)
    allowed = {normalize_gid(int(g)) for g in subscriptions.get(key, [])}
    allowed_map[key] = set(allowed)

    # opcional: incluir chat vinculado
    if INCLUDE_LINKED_DYNAMIC and allowed:
        for base_id in list(allowed):
            try:
                linked = await resolve_linked_for(cli, base_id)
                if linked:
                    allowed_map[key].add(linked)
            except Exception:
                pass

    @cli.on(events.NewMessage)
    async def forward_user(ev: events.NewMessage.Event):
        aid = allowed_map.get(key, set())
        if ev.chat_id not in aid:
            return
        if seen(ev.chat_id, ev.message.id):
            return
        log.info(f"🔍 [dynamic] user={uid} got message from chat={ev.chat_id}")
        chat  = await cli.get_entity(ev.chat_id)
        title = getattr(chat, 'title', None) or str(ev.chat_id)
        header = f"📢 *{title}* (`{ev.chat_id}`)"
        await forward_with_fallback(ev.message, header)

    asyncio.create_task(cli.run_until_disconnected())
    log.info(f"🟢 listener dinâmico ligado para user={uid} allowed={sorted(list(allowed_map[key]))}")
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
            "1⃣ `/myid` – mostra seu ID\n"
            "2⃣ `/setsession SUA_SESSION` – salva sua sessão\n"
            "3⃣ `/listgroups` – lista seus grupos/canais\n"
            "4⃣ `/subscribe GROUP_ID` – assina um grupo seu\n"
            "5⃣ `/unsubscribe GROUP_ID` – remove assinatura\n\n"
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
        if not lines:
            return await reply("Nenhum grupo/canal encontrado.")
        return await reply("📋 *Seus grupos:*\n" + "\n".join(lines), parse_mode='Markdown')

    if txt.startswith('/subscribe '):
        try:
            gid = normalize_gid(int(txt.split(' ', 1)[1]))
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
            gid = normalize_gid(int(txt.split(' ', 1)[1]))
        except Exception:
            return await reply("ID inválido.")
        lst = subscriptions.get(str(uid), [])
        if gid not in lst:
            return await reply("⚠️ Não estava inscrito.")
        lst.remove(gid)
        _save_json(SUBS_FILE, subscriptions)
        await ensure_client(uid)
        return await reply(f"🗑️ Removido `{gid}`.", parse_mode='Markdown')

    # admin
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
            gid = normalize_gid(int(g))
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
            gid = normalize_gid(int(g))
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
            name = " ".join(filter(None, [me.first_name, me.last_name]))
            if name:
                info.append(f"Nome: {name}")
            if me.username:
                info.append(f"Username: @{me.username}")
            if me.phone:
                masked = me.phone[:-4] + "****" if len(me.phone) > 4 else "****"
                info.append(f"Phone: +{masked}")
            await reply("\n".join(info), parse_mode='Markdown')
            await tmp.disconnect()
            return
        except Exception as e:
            return await reply(f"❌ Falha ao abrir session: {type(e).__name__}")

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
            await reply("\n".join(lines) if count else "Nenhum grupo/canal encontrado.", parse_mode='Markdown')
            await tmp.disconnect()
            return
        except Exception as e:
            return await reply(f"❌ Falha ao listar grupos: {type(e).__name__}")

    return await reply("❓ Comando não reconhecido. Use /help.")

# ─────────────── MAIN ───────────────
async def main():
    # migra ids antigos (sem -100)
    migrate_sub_ids()

    # Flask
    threading.Thread(target=run_flask, daemon=True).start()

    # inicia bot + sua conta
    await asyncio.gather(
        bot.start(bot_token=BOT_TOKEN),
        admin_client.start()
    )

    # Resolver o destino **com a sua sessão**
    global DEST_ENTITY
    try:
        DEST_ENTITY = await SENDER.get_entity(DEST_CHAT_ID)
    except Exception:
        DEST_ENTITY = DEST_CHAT_ID  # último recurso

    # reativar listeners dinâmicos existentes
    if subscriptions:
        uids = [int(u) for u in subscriptions.keys() if u in sessions]
        log.info(f"♻️ restaurando listeners dinâmicos: {uids}")
        for u in uids:
            try:
                await ensure_client(u)
            except Exception:
                pass

    log.info("🤖 Bot iniciado!")
    await asyncio.gather(
        bot.run_until_disconnected(),
        admin_client.run_until_disconnected()
    )

if __name__ == "__main__":
    asyncio.run(main())
