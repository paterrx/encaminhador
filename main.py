# main.py — simples, estável e com dashboard embutido
import os
import json
import time
import asyncio
import threading
import logging
from typing import Dict, List, Optional, Tuple

from flask import Flask, jsonify, Response

from telethon import TelegramClient, events, errors, functions
from telethon.sessions import StringSession
from telethon.tl.types import Message

# ───────────────────────────── LOG ─────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("encaminhador")

# ───────────────────────────── DESTINOS (hardcoded) ─────────────────────────────
DEST_POSTS: int     = -1002897690215   # canal de posts
DEST_COMMENTS: int  = -1002489338128   # canal de comentários

# ───────────────────────────── FONTES (hardcoded) ─────────────────────────────
# OUÇA APENAS estes 3 pares:
# TRIADE (canal -> chat), LF Tips (canal->chat), Psico (canal->chat)
LINKS: Dict[int, int] = {
    -1002794084735: -1002722732606,  # TRIADE
    -1002855377727: -1002813556527,  # LF Tips
    -1002468014496: -1002333613791,  # Psico
}
SOURCE_FIXED: List[int] = list(LINKS.keys()) + list(LINKS.values())

# ───────────────────────────── SESSÕES (hardcoded) ─────────────────────────────
SESSIONS: Dict[str, str] = {
    "786880968": "1AZWarzgBu1SEPUzUrUUDXtnGbkyoDEknN2-8Pfcae8iOjJNEQ6qryf1VY47_IYDt0urjMxZCvnQS6X6CNO7WRmKID2YVdRHkIm-1MWX_4NCQQPzMrlN8OsdCF-JaIx4Pt3vXhfbx1qR68ISM9yLTx8-Ud9wy5xtb1DRYRB95IzV5bimJLTEP_9N8Og7rANevX4H29_NKZkCoTA7Qg8jTeVPgK0I6ClQvVbcaxi04kiZ9vwfjsOx3YwWbZsWFLFovQRjnGezXWVPn3BxfRWiHE1sHOM8X6qsEwnWsmdjkyTDxzg8sTjLYs8Bnapw275LhRwCscROeGQ-YmvlzHZ4AqB7JyFRnfH4=",
    "435374422": "1AZWarzsBu7Rd3aDhcqB9VW1tW9nPh-UVr8HMPCwEKBh_jVQ4wAaYx8xd4ZEltEJTsUJyNWAbPSeT61ZZJGxg6vPBXfXYWaCoylT2rBullBn0ZG1VXofd4jO-tGOPy8LYb9xBvmVkmuPILGN0_ZJsz92is901v2Eys4o5ULHrp2TT9o6jwU1rFKYpv0T6PdptBrwh2XgdViewk1xjMy1bS0GZD8EltJ8FdaTqXj2DXj96TjAa3nWk1ExUKvnaWW81MytyVMjGzsCgYDeU-Z641a3c29L0iFXXjDq4H7m0-Pxy1tJG5CASlnBv4ShOOToc0W4JFTgkKZp6IF9mWGd9hvNSkSr3XYo=",
    "6209300823": "1AZWarzcBu2MRTYFPOYL8dNP86W39b2XnUIZn4VGhnJsFWuNIL1zSUqLAiBb0zq58HGRmuSsRWkrS4apG9bvRP3gsMgHvwfti0Jp4-KA-tVNdot7tLdn20u5tNY2ZVfqki_xG9VpQqgCmjMpV6___rVZLMy_bHR2IN5a8YIP2ApvANw4p_1Dw-o044FdIgREBGSQ6ONURKj45b_8Nm2y0JcRutNCCH94zAILysNxhQlIdCSahNxfiA78-FGr_fvk7WIPfHHDtVmylNUZMUpu-5UlT9OuLHxazyxDyM9uPTmh8cD3CG7JvY44652m-ajPDPlB4d3MfPIC_95uxJIJhoymrfr4HQoE=",
}

# quais bases cada sessão vai ouvir (os 3 pares gêmeos base+chat)
SUBS: Dict[str, List[int]] = {
    "786880968": SOURCE_FIXED,
    "435374422": SOURCE_FIXED,
    "6209300823": SOURCE_FIXED,
}

# ───────────────────────────── BOTs / TELEGRAM API ─────────────────────────────
API_ID  = int(os.environ.get("TELEGRAM_API_ID", "0") or 0)
API_HASH = os.environ.get("TELEGRAM_API_HASH", "") or ""

# Um ou dois bots:
BOT_SENDER_TOKEN = os.environ.get("BOT_TOKEN", "").strip()        # envia posts/chats
CMD_BOT_TOKEN    = os.environ.get("CMD_BOT_TOKEN", "").strip()     # recebe comandos (opcional)
if not CMD_BOT_TOKEN:
    CMD_BOT_TOKEN = BOT_SENDER_TOKEN  # pode ser o mesmo bot

bot_sender = TelegramClient("bot_sender", API_ID, API_HASH)
bot_cmd    = TelegramClient("bot_cmd",    API_ID, API_HASH)

# clientes “ouvidos”
user_clients: Dict[str, TelegramClient] = {}

# mapa (base_channel_id, src_msg_id) -> dest_post_msg_id
POSTMAP_FILE = "/data/postmap.json"
post_map: Dict[str, int] = {}
os.makedirs("/data", exist_ok=True)
if os.path.exists(POSTMAP_FILE):
    try:
        post_map = json.load(open(POSTMAP_FILE, "r", encoding="utf-8"))
    except Exception:
        post_map = {}

def pm_key(base: int, msg_id: int) -> str:
    return f"{base}:{msg_id}"

def pm_set(base: int, src_id: int, dest_id: int):
    post_map[pm_key(base, src_id)] = dest_id
    # guarda máx 10k
    if len(post_map) > 10000:
        # drop antigos
        for k in list(post_map.keys())[:2000]:
            post_map.pop(k, None)
    json.dump(post_map, open(POSTMAP_FILE, "w", encoding="utf-8"))

def pm_get(base: int, src_id: int) -> Optional[int]:
    return post_map.get(pm_key(base, src_id))

# inverso: chat->base
CHAT_TO_BASE: Dict[int, int] = {v: k for k, v in LINKS.items()}

# ───────────────────────────── Flask (dashboard) ─────────────────────────────
app = Flask("keep_alive")

@app.get("/")
def root():
    return "OK"

@app.get("/health")
def health():
    return jsonify(ok=True, sessions=len(user_clients))

@app.get("/dash")
def dash():
    data = {
        "dest_posts": DEST_POSTS,
        "dest_comments": DEST_COMMENTS,
        "links": LINKS,
        "subs": SUBS,
        "listening_sessions": list(user_clients.keys()),
        "postmap_size": len(post_map),
        "ts": int(time.time()),
    }
    html = (
        "<html><body style='font-family:Inter,system-ui,-apple-system,sans-serif;padding:16px;background:#0b1020;color:#e8eefc'>"
        "<h2>Encaminhador — Dashboard</h2>"
        f"<p><b>DEST_POSTS:</b> {DEST_POSTS}<br><b>DEST_COMMENTS:</b> {DEST_COMMENTS}</p>"
        "<h3>Links (base → chat)</h3><pre style='background:#0f162f;padding:12px;border-radius:10px'>"
        + json.dumps(LINKS, indent=2) + "</pre>"
        "<h3>Sessoes Escutando</h3><pre style='background:#0f162f;padding:12px;border-radius:10px'>"
        + json.dumps(list(user_clients.keys()), indent=2) + "</pre>"
        f"<p><b>POSTMAP size:</b> {len(post_map)}</p>"
        "</body></html>"
    )
    return Response(html, mimetype="text/html")

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)

# ───────────────────────────── Helpers de envio (sempre via BOT) ─────────────────────────────
async def send_text(dst: int, text: str, reply_to: Optional[int] = None, parse_md=True):
    try:
        return await bot_sender.send_message(dst, text, parse_mode=("Markdown" if parse_md else None), reply_to=reply_to)
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        return await bot_sender.send_message(dst, text, parse_mode=("Markdown" if parse_md else None), reply_to=reply_to)

async def copy_via_bot(dst: int, m: Message, reply_to: Optional[int] = None):
    """
    Forward garantido: se não der forward/copy, baixa e reenvia.
    """
    try:
        if m.media:
            path = await m.download_media()
            return await bot_sender.send_file(dst, path, caption=(m.text or ""), reply_to=reply_to)
        else:
            return await bot_sender.send_message(dst, m.text or "", reply_to=reply_to)
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        if m.media:
            path = await m.download_media()
            return await bot_sender.send_file(dst, path, caption=(m.text or ""), reply_to=reply_to)
        else:
            return await bot_sender.send_message(dst, m.text or "", reply_to=reply_to)

# ───────────────────────────── Reply resolver (3 tentativas) ─────────────────────────────
async def find_top_post_id_for_chat_msg(cli: TelegramClient, chat_msg: Message) -> Optional[int]:
    """
    Tenta descobrir o ID do post no canal-base ao qual a msg do chat está ligada.
    1) reply_to.reply_to_top_id
    2) functions.messages.GetDiscussionMessageRequest
    3) varre últimas mensagens retornadas pela API (fallback).
    """
    # 0) qual é o base canal?
    base_id = CHAT_TO_BASE.get(chat_msg.chat_id)
    if not base_id:
        return None

    # 1) direto do reply_to
    r = getattr(chat_msg, "reply_to", None)
    if r:
        top_id = getattr(r, "reply_to_top_id", None)
        if top_id:
            return int(top_id)

    # 2) API (às vezes ajuda)
    try:
        ent = await cli.get_entity(base_id)
        # pega top do objeto via API se vier reply_to_msg_id
        maybe_id = getattr(r, "reply_to_msg_id", None) or getattr(chat_msg, "id", None)
        if maybe_id:
            dm = await cli(functions.messages.GetDiscussionMessageRequest(peer=ent, msg_id=int(maybe_id)))
            # preferir 'top_message' se existir
            top = getattr(dm, "top_message", None)
            if top:
                return int(top)
            # 3) fallback: vasculha 'messages'
            mm = getattr(dm, "messages", None)
            if isinstance(mm, list):
                for m in mm:
                    rr = getattr(m, "reply_to", None)
                    if rr and getattr(rr, "reply_to_top_id", None):
                        return int(rr.reply_to_top_id)
    except Exception as e:
        log.debug(f"[reply-fallback API] fail: {type(e).__name__}")

    return None

# ───────────────────────────── Listeners (para cada sessão) ─────────────────────────────
async def ensure_session(uid: str):
    if uid in user_clients:
        return user_clients[uid]
    sess = SESSIONS.get(uid)
    if not sess:
        return None

    cli = TelegramClient(StringSession(sess), API_ID, API_HASH)
    await cli.start()

    allowed = set(SOURCE_FIXED)

    @cli.on(events.NewMessage(chats=list(allowed)))
    async def on_msg(ev: events.NewMessage.Event, _uid=uid):
        try:
            m: Message = ev.message
            cid: int = ev.chat_id

            is_chat = cid in LINKS.values()
            if is_chat:
                # ----------------- CHAT (comentários) -----------------
                base_id = CHAT_TO_BASE.get(cid)
                title = (getattr((await cli.get_entity(cid)), "title", None) or str(cid))

                # tenta resolver top_id p/ reply no destino
                top_id = await find_top_post_id_for_chat_msg(cli, m)
                dest_reply_to = None
                if top_id is not None:
                    dest_post = pm_get(base_id, int(top_id))
                    if dest_post:
                        dest_reply_to = dest_post

                # nome do autor
                sender = await ev.get_sender()
                sname = " ".join(filter(None, [getattr(sender, "first_name", None),
                                               getattr(sender, "last_name", None)])) or (getattr(sender, "username", None) or "alguém")

                header = f"💬 *{title}* — {sname} (`{cid}`)"
                await send_text(DEST_COMMENTS, header)
                await copy_via_bot(DEST_COMMENTS, m, reply_to=dest_reply_to)
                log.info(f"[chat {_uid}] {cid} -> comments (reply_to={dest_reply_to})")

            else:
                # ----------------- POST (canal base) -----------------
                title = (getattr((await cli.get_entity(cid)), "title", None) or str(cid))
                header = f"📢 *{title}* (`{cid}`)"
                await send_text(DEST_POSTS, header)
                sent = await copy_via_bot(DEST_POSTS, m)
                # salva mapa pro reply
                if sent and getattr(sent, "id", None):
                    pm_set(cid, int(m.id), int(sent.id))
                log.info(f"[post {_uid}] {cid} -> posts (map:{m.id}->{getattr(sent,'id',None)})")

        except Exception as e:
            log.exception(f"[listener {_uid}] fail: {e}")

    user_clients[uid] = cli
    asyncio.create_task(cli.run_until_disconnected())
    log.info(f"[dyn] ligado uid={uid} allowed={sorted(list(allowed))}")
    return cli

# ───────────────────────────── Comandos do bot (status básico) ─────────────────────────────
async def setup_bot_commands():
    @bot_cmd.on(events.NewMessage(pattern=r'^/start$'))
    async def _start(ev):
        await ev.reply(
            "👋 Encaminhador online.\n\n"
            "• /admin_status – mostra status e tamanhos\n"
            "• Dashboard: abra /dash na web do Railway",
        )

    @bot_cmd.on(events.NewMessage(pattern=r'^/admin_status$'))
    async def _status(ev):
        # constrói uma visão rápida
        out = [
            f"DEST_POSTS: {DEST_POSTS}",
            f"DEST_COMMENTS: {DEST_COMMENTS}",
            f"Sessoes: {len(SESSIONS)} | Dinâmicos ON: {len(user_clients)}",
            f"Links: {len(LINKS)} pares | POSTMAP: {len(post_map)}",
        ]
        for uid in sorted(user_clients.keys()):
            out.append(f"- {uid}")
        await ev.reply("\n".join(out))

# ───────────────────────────── MAIN ─────────────────────────────
async def main():
    # Flask
    threading.Thread(target=run_flask, daemon=True).start()

    # bots
    await bot_sender.start(bot_token=BOT_SENDER_TOKEN)
    await bot_cmd.start(bot_token=CMD_BOT_TOKEN)
    await setup_bot_commands()

    # listeners (todas as 3 sessões)
    for uid in list(SESSIONS.keys()):
        try:
            await ensure_session(uid)
        except Exception as e:
            log.exception(f"session {uid} fail: {e}")

    log.info("🤖 pronto (TRIADE, LF Tips, Psico) — envio via BOT, replies com fallback")
    await bot_cmd.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
