# main.py — 1 BOT para postar + comandos, sem forward (sempre clona)

import os
import asyncio
import logging
import threading
import json
import time
from typing import Dict, List, Optional, Tuple

from flask import Flask, jsonify, Response
from telethon import TelegramClient, events, errors, functions
from telethon.sessions import StringSession
from telethon.tl.types import Message

# ───────────────────────── LOG ─────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("encaminhador")

# ─────────────────────── CONFIG (IDs) ───────────────────────
def _safe_int(env_key: str, default: int) -> int:
    v = os.environ.get(env_key, "").strip()
    try:
        return int(v) if v else default
    except Exception:
        return default

# Destinos (pode sobrescrever por ENV)
DEST_POSTS: int    = _safe_int("DEST_POSTS",    -1002897690215)   # canal destino (posts)
DEST_COMMENTS: int = _safe_int("DEST_COMMENTS", -1002489338128)   # chat destino (comentários)

# Um único BOT (postagem + comandos)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# API
API_ID   = int(os.environ.get("TELEGRAM_API_ID", "0") or 0)
API_HASH = os.environ.get("TELEGRAM_API_HASH", "") or ""

# UID do dono (para permissão de admin)
ADMIN_UID = 786880968

# ─────────── Hardcodes (usados como default; também persistimos em /data) ───────────
SESSIONS: Dict[str, str] = {
    "786880968": "1AZWarzgBu1SEPUzUrUUDXtnGbkyoDEknN2-8Pfcae8iOjJNEQ6qryf1VY47_IYDt0urjMxZCvnQS6X6CNO7WRmKID2YVdRHkIm-1MWX_4NCQQPzMrlN8OsdCF-JaIx4Pt3vXhfbx1qR68ISM9yLTx8-Ud9wy5xtb1DRYRB95IzV5bimJLTEP_9N8Og7rANevX4H29_NKZkCoTA7Qg8jTeVPgK0I6ClQvVbcaxi04kiZ9vwfjsOx3YwWbZsWFLFovQRjnGezXWVPn3BxfRWiHE1sHOM8X6qsEwnWsmdjkyTDxzg8sTjLYs8Bnapw275LhRwCscROeGQ-YmvlzHZ4AqB7JyFRnfH4=",
    "435374422": "1AZWarzsBu7Rd3aDhcqB9VW1tW9nPh-UVr8HMPCwEKBh_jVQ4wAaYx8xd4ZEltEJTsUJyNWAbPSeT61ZZJGxg6vPBXfXYWaCoylT2rBullBn0ZG1VXofd4jO-tGOPy8LYb9xBvmVkmuPILGN0_ZJsz92is901v2Eys4o5ULHrp2TT9o6jwU1rFKYpv0T6PdptBrwh2XgdViewk1xjMy1bS0GZD8EltJ8FdaTqXj2DXj96TjAa3nWk1ExUKvnaWW81MytyVMjGzsCgYDeU-Z641a3c29L0iFXXjDq4H7m0-Pxy1tJG5CASlnBv4ShOOToc0W4JFTgkKZp6IF9mWGd9hvNSkSr3XYo=",
    "6209300823": "1AZWarzcBu2MRTYFPOYL8dNP86W39b2XnUIZn4VGhnJsFWuNIL1zSUqLAiBb0zq58HGRmuSsRWkrS4apG9bvRP3gsMgHvwfti0Jp4-KA-tVNdot7tLdn20u5tNY2ZVfqki_xG9VpQqgCmjMpV6___rVZLMy_bHR2IN5a8YIP2ApvANw4p_1Dw-o044FdIgREBGSQ6ONURKj45b_8Nm2y0JcRutNCCH94zAILysNxhQlIdCSahNxfiA78-FGr_fvk7WIPfHHDtVmylNUZMUpu-5UlT9OuLHxazyxDyM9uPTmh8cD3CG7JvY44652m-ajPDPlB4d3MfPIC_95uxJIJhoymrfr4HQoE=",
}

# Canais base por usuário (apenas os 3 pedidos)
SUBS: Dict[str, List[int]] = {
    "786880968": [-1002794084735],             # TRIÁDE (canal)
    "435374422": [-1002855377727],             # LF TIPS (canal)
    "6209300823": [-1002468014496],            # PSICOPATAS (canal)
}

# base → chat (linked)
LINKS: Dict[int, int] = {
    -1002794084735: -1002722732606,   # TRIÁDE canal -> chat
    -1002855377727: -1002813556527,   # LF Tips canal -> chat
    -1002468014496: -1002333613791,   # Psico canal -> chat
}

# ────────────────────── Persistência /data ──────────────────────
DATA_DIR = "/data"
os.makedirs(DATA_DIR, exist_ok=True)
SESS_FILE = os.path.join(DATA_DIR, "sessions.json")
SUBS_FILE = os.path.join(DATA_DIR, "subs.json")
LINKS_FILE = os.path.join(DATA_DIR, "links.json")

def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def persist_all():
    _save_json(SESS_FILE, SESSIONS)
    _save_json(SUBS_FILE, SUBS)
    _save_json(LINKS_FILE, LINKS)

def load_all_from_disk_if_any():
    SESSIONS.update(_load_json(SESS_FILE, {}))
    SUBS.update(_load_json(SUBS_FILE, {}))
    LINKS.update(_load_json(LINKS_FILE, {}))

load_all_from_disk_if_any()

def _rev_links() -> Dict[int, int]:
    return {v: k for k, v in LINKS.items()}

# ──────────────────── Infra Flask / Dashboard ────────────────────
app = Flask("keep_alive")

@app.get("/")
def root():
    return (
        "<h3>Encaminhador</h3>"
        "<ul>"
        f"<li>DEST_POSTS: {DEST_POSTS}</li>"
        f"<li>DEST_COMMENTS: {DEST_COMMENTS}</li>"
        f"<li>Sessões: {list(SESSIONS.keys())}</li>"
        "</ul>"
        '<p>Dashboard: <a href="/dash">/dash</a></p>'
    )

@app.get("/health")
def health():
    return jsonify(ok=True, ts=int(time.time()))

@app.get("/dash")
def dash():
    return Response(
        "<h2>Dashboard</h2>"
        f"<p>DEST_POSTS: {DEST_POSTS} | DEST_COMMENTS: {DEST_COMMENTS}</p>"
        f"<p>Sessões salvas: {len(SESSIONS)} | Dinâmicos ON: {len(user_clients)}</p>"
        f"<p>Links (base→chat): {len(LINKS)}</p>"
        f"<p>POSTMAP: {len(POSTMAP)} | TOPMAP: {len(TOPMAP)}</p>"
        '<pre style="white-space:pre-wrap;">'
        + json.dumps({"subs": SUBS, "links": LINKS}, ensure_ascii=False, indent=2)
        + "</pre>",
        mimetype="text/html"
    )

@app.get("/dump_sessions")
def dump_sessions():
    return jsonify(SESSIONS)

@app.get("/dump_subs")
def dump_subs():
    return jsonify(SUBS)

@app.get("/dump_links")
def dump_links():
    return jsonify(LINKS)

@app.get("/dump_postmap")
def dump_postmap():
    return jsonify({f"{k[0]}:{k[1]}": v for k, v in POSTMAP.items()})

@app.get("/dump_topmap")
def dump_topmap():
    return jsonify({f"{k[0]}:{k[1]}": v for k, v in TOPMAP.items()})

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)

# ───────────────────── Telethon clients ─────────────────────
# ÚNICO BOT: postagem + comandos
bot: Optional[TelegramClient] = TelegramClient("main_bot", API_ID, API_HASH) if BOT_TOKEN else None

# Dicionário de clients (ouvintes) por UID
user_clients: Dict[str, TelegramClient] = {}

# Mapas de reply:
# 1) (base_id, base_msg_id) -> dest_msg_id (post no destino)
POSTMAP: Dict[Tuple[int, int], int] = {}
# 2) (chat_id, top_id) -> dest_msg_id (thread no destino)
TOPMAP: Dict[Tuple[int, int], int]   = {}

# ───────────────────── Helpers de envio (sempre clona) ─────────────────────
async def _send_header(dst: int, text: str, reply_to: Optional[int] = None) -> int:
    if not bot:
        raise RuntimeError("BOT_TOKEN não configurado.")
    try:
        m = await bot.send_message(dst, text, parse_mode="Markdown", reply_to=reply_to)
        return m.id
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        m = await bot.send_message(dst, text, parse_mode="Markdown", reply_to=reply_to)
        return m.id

async def _clone_message(dst: int, origin_client: TelegramClient, msg: Message, reply_to: Optional[int] = None) -> int:
    if not bot:
        raise RuntimeError("BOT_TOKEN não configurado.")
    try:
        if msg.media:
            path = await origin_client.download_media(msg)
            sent = await bot.send_file(dst, path, caption=(msg.text or ""), reply_to=reply_to)
            return sent.id
        else:
            sent = await bot.send_message(dst, msg.text or "", reply_to=reply_to)
            return sent.id
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        if msg.media:
            path = await origin_client.download_media(msg)
            sent = await bot.send_file(dst, path, caption=(msg.text or ""), reply_to=reply_to)
            return sent.id
        else:
            sent = await bot.send_message(dst, msg.text or "", reply_to=reply_to)
            return sent.id

# ───────────────────── Listener dinâmico ─────────────────────
async def ensure_dynamic(uid: str):
    """Sobe (ou religa) o client para o uid e instala handlers."""
    if uid in user_clients:
        return user_clients[uid]

    sess = SESSIONS.get(uid)
    if not sess:
        return None

    cli = TelegramClient(StringSession(sess), API_ID, API_HASH)
    await cli.start()

    bases = SUBS.get(uid, [])
    allowed = set(bases)
    for b in bases:
        linked = LINKS.get(b)
        if linked:
            allowed.add(linked)

    rev = _rev_links()

    @cli.on(events.NewMessage(chats=list(allowed)))
    async def dyn(ev: events.NewMessage.Event, _uid=uid):
        try:
            cid = ev.chat_id
            ent = await cli.get_entity(cid)
            title = getattr(ent, "title", None) or str(cid)

            # VEIO DO CANAL-BASE?
            if cid in bases:
                header = f"📢 *{title}* (`{cid}`)"
                await _send_header(DEST_POSTS, header)
                dest_id = await _clone_message(DEST_POSTS, cli, ev.message)

                # tentar mapear thread
                linked_chat = LINKS.get(cid)
                if linked_chat:
                    try:
                        dm = await cli(functions.messages.GetDiscussionMessageRequest(
                            peer=cid, msg_id=ev.message.id
                        ))
                        top_id = getattr(dm, "top_message", None)
                        if top_id:
                            TOPMAP[(linked_chat, int(top_id))] = dest_id
                            POSTMAP[(cid, ev.message.id)] = dest_id
                            log.debug(f"[map] base({cid},{ev.message.id}) -> dest({dest_id}); top({linked_chat},{top_id})")
                    except Exception as e:
                        log.debug(f"[map] GetDiscussionMessage falhou: {type(e).__name__}")
                return

            # VEIO DO CHAT VINCULADO?
            if cid in rev:
                base_id = rev[cid]

                # tenta descobrir o top_id
                top_id = None
                r = getattr(ev.message, "reply_to", None)
                if r:
                    top_id = getattr(r, "reply_to_top_id", None) or getattr(r, "reply_to_msg_id", None)

                reply_to_dest = None
                if top_id and (cid, int(top_id)) in TOPMAP:
                    reply_to_dest = TOPMAP[(cid, int(top_id))]
                elif (base_id, ev.message.id) in POSTMAP:
                    reply_to_dest = POSTMAP[(base_id, ev.message.id)]

                # quem escreveu
                try:
                    sender = await ev.get_sender()
                    sname = " ".join(filter(None, [getattr(sender, "first_name", None),
                                                   getattr(sender, "last_name", None)])) \
                            or (sender.username or "alguém")
                except Exception:
                    sname = "alguém"

                header = f"💬 *{title}* — {sname} (`{cid}`)"
                await _send_header(DEST_COMMENTS, header, reply_to=reply_to_dest)
                await _clone_message(DEST_COMMENTS, cli, ev.message, reply_to=reply_to_dest)
                return
        except Exception as e:
            log.exception(f"[dyn {uid}] fail: {e}")

    user_clients[uid] = cli
    asyncio.create_task(cli.run_until_disconnected())
    log.info(f"[dyn] ligado uid={uid} allowed={sorted(list(allowed))}")
    return cli

async def restart_dynamic(uid: str):
    cli = user_clients.pop(uid, None)
    try:
        if cli:
            await cli.disconnect()
    except Exception:
        pass
    await ensure_dynamic(uid)

# ───────────────────── Bot de comandos ─────────────────────
def _is_admin(uid: int) -> bool:
    try:
        admins_env = os.environ.get("ADMIN_IDS", "[]")
        admins = json.loads(admins_env) if admins_env else []
        if isinstance(admins, int):
            admins = [admins]
    except Exception:
        admins = []
    return uid in set(admins or [ADMIN_UID])

def _as_int(x: str) -> int:
    try:
        return int(x)
    except Exception:
        raise ValueError("ID inválido")

async def setup_bot_commands():
    if not bot:
        return

    @bot.on(events.NewMessage(pattern=r"^/(start|admin_status|dash|listgroups|setsession|subscribe|linkchat|unlinkchat|save)\b"))
    async def handler(ev: events.NewMessage.Event):
        uid_from = ev.sender_id
        text = ev.raw_text.strip()
        parts = text.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd == "/start":
            return await ev.reply(
                "👋 Encaminhador online.\n\n"
                "• `/admin_status` – status\n"
                "• Dashboard: abra `/dash` na web do Railway\n"
                "• `/listgroups UID [página] [tamanho]`\n"
                "• `/setsession UID SESSION`\n"
                "• `/subscribe [UID] BASE_CHANNEL_ID`\n"
                "• `/linkchat [UID] BASE_ID CHAT_ID`\n"
                "• `/unlinkchat [UID] BASE_ID`\n"
                "• `/save`\n"
            )

        if cmd == "/dash":
            return await ev.reply("Abra /dash na URL pública do serviço.")

        if cmd == "/admin_status":
            lines = [
                f"DEST_POSTS: {DEST_POSTS}",
                f"DEST_COMMENTS: {DEST_COMMENTS}",
                f"Sessões: {len(SESSIONS)} | Dinâmicos ON: {len(user_clients)}",
                f"Links: {len(LINKS)} | POSTMAP: {len(POSTMAP)} | TOPMAP: {len(TOPMAP)}",
            ]
            for u in sorted(user_clients.keys()):
                bases = SUBS.get(u, [])
                links = [f"{b}->{LINKS.get(b,'?')}" for b in bases if b in LINKS]
                lines.append(f"- {u}: subs={len(bases)} {'| ' + ', '.join(links) if links else ''}")
            return await ev.reply("\n".join(lines))

        if cmd == "/listgroups":
            if not _is_admin(uid_from):
                return await ev.reply("🚫 Sem permissão.")
            if not args:
                return await ev.reply("Uso: `/listgroups UID [página] [tamanho]`", parse_mode="Markdown")
            target_uid = args[0]
            page = int(args[1]) if len(args) >= 2 and args[1].isdigit() else 1
            size = int(args[2]) if len(args) >= 3 and args[2].isdigit() else 50
            page = max(1, page); size = min(100, max(5, size))

            sess = SESSIONS.get(target_uid)
            if not sess:
                return await ev.reply("⚠️ UID sem session salva.")
            tmp = TelegramClient(StringSession(sess), API_ID, API_HASH)
            try:
                await tmp.start()
                want = page * size
                lines, grabbed = [], 0
                async for d in tmp.iter_dialogs(limit=3000):
                    ent = d.entity
                    if getattr(ent, "megagroup", False) or getattr(ent, "broadcast", False):
                        cid = getattr(ent, "id", None)
                        title = getattr(ent, "title", None) or getattr(ent, "username", None) or str(cid)
                        grabbed += 1
                        if grabbed > (page-1)*size and len(lines) < size:
                            lines.append(f"- `{cid}` — {title}")
                        if grabbed >= want:
                            break
                await ev.reply("📋 *Grupos:*\n" + "\n".join(lines or ["(vazio)"]), parse_mode="Markdown")
            finally:
                await tmp.disconnect()
            return

        if cmd == "/setsession":
            if not _is_admin(uid_from):
                return await ev.reply("🚫 Sem permissão.")
            if len(args) < 2:
                return await ev.reply("Uso: `/setsession UID SESSION`", parse_mode="Markdown")
            target_uid = args[0]; sess = " ".join(args[1:])
            SESSIONS[target_uid] = sess
            persist_all()
            await restart_dynamic(target_uid)
            return await ev.reply(f"✅ Session salva para `{target_uid}`.", parse_mode="Markdown")

        if cmd == "/subscribe":
            if len(args) == 1:
                target_uid = str(uid_from); base_id = _as_int(args[0])
            elif len(args) >= 2:
                if not _is_admin(uid_from):
                    return await ev.reply("🚫 Sem permissão.")
                target_uid = args[0]; base_id = _as_int(args[1])
            else:
                return await ev.reply("Uso: `/subscribe [UID] BASE_CHANNEL_ID`")
            SUBS.setdefault(target_uid, [])
            if base_id not in SUBS[target_uid]:
                SUBS[target_uid].append(base_id)
                persist_all()
                await restart_dynamic(target_uid)
                return await ev.reply(f"✅ `{target_uid}` inscrito em `{base_id}`.", parse_mode="Markdown")
            return await ev.reply("⚠️ Já inscrito.")

        if cmd == "/linkchat":
            if len(args) == 2:
                target_uid = str(uid_from)
                base_id = _as_int(args[0]); chat_id = _as_int(args[1])
            elif len(args) >= 3:
                if not _is_admin(uid_from):
                    return await ev.reply("🚫 Sem permissão.")
                target_uid = args[0]; base_id = _as_int(args[1]); chat_id = _as_int(args[2])
            else:
                return await ev.reply("Uso: `/linkchat [UID] BASE_ID CHAT_ID`")
            LINKS[base_id] = chat_id
            persist_all()
            await restart_dynamic(target_uid)
            return await ev.reply(f"🔗 `{base_id}` → `{chat_id}` vinculado para `{target_uid}`.", parse_mode="Markdown")

        if cmd == "/unlinkchat":
            if len(args) == 1:
                target_uid = str(uid_from); base_id = _as_int(args[0])
            elif len(args) >= 2:
                if not _is_admin(uid_from):
                    return await ev.reply("🚫 Sem permissão.")
                target_uid = args[0]; base_id = _as_int(args[1])
            else:
                return await ev.reply("Uso: `/unlinkchat [UID] BASE_ID`")
            if LINKS.pop(base_id, None) is not None:
                persist_all()
                await restart_dynamic(target_uid)
                return await ev.reply(f"🗑️ vínculo removido `{base_id}`.", parse_mode="Markdown")
            return await ev.reply("⚠️ Não havia vínculo.")

        if cmd == "/save":
            if not _is_admin(uid_from):
                return await ev.reply("🚫 Sem permissão.")
            persist_all()
            return await ev.reply("💾 Salvo em /data.")

    await bot.start(bot_token=BOT_TOKEN)
    asyncio.create_task(bot.run_until_disconnected())

# ─────────────────────────── MAIN ───────────────────────────
async def main():
    # Flask
    threading.Thread(target=run_flask, daemon=True).start()

    if not bot:
        log.error("BOT_TOKEN não configurado — finalizei.")
        return

    # Comandos
    await setup_bot_commands()

    # Sobe/religa todos os ouvintes
    for uid in list(SESSIONS.keys()):
        try:
            await ensure_dynamic(uid)
        except Exception as e:
            log.exception(f"ensure_dynamic {uid} fail: {e}")

    log.info("🤖 pronto (TRIÁDE, LF TIPS, PSICO) — 1 BOT (post + comandos), sem forward, replies mapeados")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
