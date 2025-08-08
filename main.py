# main.py — estável (posts), destino resolvido como InputPeer pela sua sessão

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
BOT_TOKEN       = os.environ['BOT_TOKEN']             # bot só para UI no PV
DEST_CHAT_ID    = int(os.environ['DEST_CHAT_ID'])     # canal/grupo destino dos posts

# sua conta (admin) envia e também escuta os canais fixos
SESSION_STRING  = os.environ['SESSION_STRING']
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS', '[]'))

# incluir chats vinculados para dinâmicos (opcional)
INCLUDE_LINKED_DYNAMIC = bool(int(os.environ.get('INCLUDE_LINKED_DYNAMIC', '1')))

# Persistência
DATA_DIR  = '/data'
SESS_FILE = os.path.join(DATA_DIR, 'sessions.json')
SUBS_FILE = os.path.join(DATA_DIR, 'subscriptions.json')
AUD_FILE  = os.path.join(DATA_DIR, 'audit.json')

os.makedirs(DATA_DIR, exist_ok=True)

# ───────────────────────────── LOG ─────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("encaminhador")

# ─────────────────────── UTIL JSON ───────────────────────
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

# ─────────────────────── Telethon clients ───────────────────────
# bot: só comandos no PV
bot = TelegramClient("bot_session", API_ID, API_HASH)

# sua conta: escuta fixos e ENVIA para o destino
admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# Quem envia de verdade:
SENDER = admin_client
DEST_ENTITY: Optional[object] = None  # InputPeer resolvido pela sua sessão

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
    except Exception as e:
        log.debug(f"[linked] API resolve failed for {channel_id}: {e}")

    # fallback por nome
    try:
        base = (getattr(ent, 'title', '') or '').strip().lower()
        if not base:
            return 0
        candidates = {f"{base} chat", f"{base} - chat", f"{base} • chat", f"{base} – chat"}
        async for d in client.iter_dialogs():
            title = (getattr(d.entity, 'title', '') or '').strip().lower()
            if getattr(d.entity, 'megagroup', False):
                if title in candidates or (title.startswith(base) and 'chat' in title):
                    log.info(f"[linked] guessed by name: {channel_id} -> {d.entity.id} ({title})")
                    return int(d.entity.id)
    except Exception as e:
        log.debug(f"[linked] name fallback failed for {channel_id}: {e}")
    return 0

# ─────────────── Encaminhar/cópia com fallback (ENVIO PELA SUA SESSÃO) ───────────────
async def forward_with_fallback(m: Message, header: str):
    # 1) header (pela SUA sessão e já com InputPeer)
    try:
        await SENDER.send_message(DEST_ENTITY, header, parse_mode='Markdown')
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        await SENDER.send_message(DEST_ENTITY, header, parse_mode='Markdown')
    except Exception as e:
        log.warning(f"[send header] {type(e).__name__}: {e}")

    # 2) tenta forward (usa o client do PRÓPRIO m; se não tiver acesso, cai no fallback)
    try:
        await m.forward_to(DEST_ENTITY)
        return
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
    except Exception:
        pass

    # 3) fallback: download + reenvio pela SUA sessão
    try:
        if m.media:
            path = await m.download_media()
            await SENDER.send_file(DEST_ENTITY, path, caption=(m.text or ''))
        else:
            await SENDER.send_message(DEST_ENTITY, m.text or '')
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        try:
            if m.media:
                path = await m.download_media()
                await SENDER.send_file(DEST_ENTITY, path, caption=(m.text or ''))
            else:
                await SENDER.send_message(DEST_ENTITY, m.text or '')
        except Exception as e:
            log.warning(f"[fallback resend] {type(e).__name__}: {e}")
    except Exception as e:
        log.warning(f"[fallback] {type(e).__name__}: {e}")

# ─────────────── Admin: canais fixos (sua session) ───────────────
@admin_client.on(events.NewMessage)
async def _fixed_listener(ev: events.NewMessage.Event):
    cid = ev.chat_id
    if cid not in SOURCE_CHAT_IDS:
        return
    log.info(f"🔍 [fixed] got message in fixed chat={cid}")
    chat  = await admin_client.get_entity(cid)
    title = getattr(chat, 'title', None) or str(cid)
    header = f"📢 *{title}* (`{cid}`)"
    await forward_with_fallback(ev.message, header)

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
    allowed = set(subscriptions.get(key, []))
    allowed_map[key] = allowed

    if INCLUDE_LINKED_DYNAMIC and allowed:
        base = list(allowed)
        for base_id in base:
            try:
                linked = await resolve_linked_for(cli, base_id)
                if linked and linked not in allowed_map[key]:
                    allowed_map[key].add(linked)
            except Exception as e:
                log.info(f"[expand] uid={uid} base_id={base_id} falha={type(e).__name__}")

    @cli.on(events.NewMessage)
    async def forward_user(ev: events.NewMessage.Event):
        aid = allowed_map.get(key, set())
        if ev.chat_id not in aid:
            return
        log.info(f"🔍 [dynamic] user={uid} got message from chat={ev.chat_id}")
        chat  = await cli.get_entity(ev.chat_id)
        title = getattr(chat, 'title', None) or str(ev.chat_id)
        header = f"📢 *{title}* (`{ev.chat_id}`)"
        await forward_with_fallback(ev.message, header)

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
        if not lines:
            return await reply("Nenhum grupo/canal encontrado.")
        return await reply("📋 *Seus grupos:*\n" + "\n".join(lines), parse_mode='Markdown')

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
    # Flask
    threading.Thread(target=run_flask, daemon=True).start()

    # inicia bot + sua conta
    await asyncio.gather(
        bot.start(bot_token=BOT_TOKEN),
        admin_client.start()
    )

    # Resolver o destino **como InputPeer pela SUA sessão** (evita o erro PeerChannel)
    global DEST_ENTITY
    try:
        DEST_ENTITY = await SENDER.get_input_entity(DEST_CHAT_ID)
    except Exception as e:
        log.warning(f"[resolve dest] {type(e).__name__}: {e}; usando id literal")
        DEST_ENTITY = DEST_CHAT_ID  # último recurso

    log.info("🤖 Bot iniciado!")
    await asyncio.gather(
        bot.run_until_disconnected(),
        admin_client.run_until_disconnected()
    )

if __name__ == "__main__":
    asyncio.run(main())
