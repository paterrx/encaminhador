# main.py — estável, envio via BOT, 3 grupos (TRIADE, LF TIPS, PSICO), replies com fallbacks
import os
import json
import asyncio
import logging
import threading
import base64
import time
from typing import Dict, List, Optional, Tuple

from flask import Flask, jsonify, Response
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.types import Message

# ───────────────────────── LOG ─────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("encaminhador")

# ───────────────────────── DESTINOS (DEFAULTS) ─────────────────────────
# Canal principal (posts) e canal de comentários (thread)
DEST_POSTS_DEFAULT    = -1002897690215
DEST_COMMENTS_DEFAULT = -1002489338128

# ───────────────────────── GRUPOS (DEFAULTS) ─────────────────────────
# base -> chat (SOMENTE: TRIADE, LF Tips, Psico)
LINKS_DEFAULT: Dict[int, int] = {
    -1002794084735: -1002722732606,  # TRIADE canal -> chat
    -1002855377727: -1002813556527,  # LF Tips canal -> chat
    -1002468014496: -1002333613791,  # Psico canal -> chat
}

# Suas 3 sessões (apenas para OUVIR)
SESSIONS_DEFAULT: Dict[str, str] = {
    "786880968": "1AZWarzgBu1SEPUzUrUUDXtnGbkyoDEknN2-8Pfcae8iOjJNEQ6qryf1VY47_IYDt0urjMxZCvnQS6X6CNO7WRmKID2YVdRHkIm-1MWX_4NCQQPzMrlN8OsdCF-JaIx4Pt3vXhfbx1qR68ISM9yLTx8-Ud9wy5xtb1DRYRB95IzV5bimJLTEP_9N8Og7rANevX4H29_NKZkCoTA7Qg8jTeVPgK0I6ClQvVbcaxi04kiZ9vwfjsOx3YwWbZsWFLFovQRjnGezXWVPn3BxfRWiHE1sHOM8X6qsEwnWsmdjkyTDxzg8sTjLYs8Bnapw275LhRwCscROeGQ-YmvlzHZ4AqB7JyFRnfH4=",
    "435374422": "1AZWarzsBu7Rd3aDhcqB9VW1tW9nPh-UVr8HMPCwEKBh_jVQ4wAaYx8xd4ZEltEJTsUJyNWAbPSeT61ZZJGxg6vPBXfXYWaCoylT2rBullBn0ZG1VXofd4jO-tGOPy8LYb9xBvmVkmuPILGN0_ZJsz92is901v2Eys4o5ULHrp2TT9o6jwU1rFKYpv0T6PdptBrwh2XgdViewk1xjMy1bS0GZD8EltJ8FdaTqXj2DXj96TjAa3nWk1ExUKvnaWW81MytyVMjGzsCgYDeU-Z641a3c29L0iFXXjDq4H7m0-Pxy1tJG5CASlnBv4ShOOToc0W4JFTgkKZp6IF9mWGd9hvNSkSr3XYo=",
    "6209300823": "1AZWarzcBu2MRTYFPOYL8dNP86W39b2XnUIZn4VGhnJsFWuNIL1zSUqLAiBb0zq58HGRmuSsRWkrS4apG9bvRP3gsMgHvwfti0Jp4-KA-tVNdot7tLdn20u5tNY2ZVfqki_xG9VpQqgCmjMpV6___rVZLMy_bHR2IN5a8YIP2ApvANw4p_1Dw-o044FdIgREBGSQ6ONURKj45b_8Nm2y0JcRutNCCH94zAILysNxhQlIdCSahNxfiA78-FGr_fvk7WIPfHHDtVmylNUZMUpu-5UlT9OuLHxazyxDyM9uPTmh8cD3CG7JvY44652m-ajPDPlB4d3MfPIC_95uxJIJhoymrfr4HQoE=",
}

# Quais bases cada sessão escuta (apenas CANAIS; o chat é inferido por LINKS)
SUBS_DEFAULT: Dict[str, List[int]] = {
    "786880968": [-1002794084735],           # TRIADE
    "435374422": [-1002855377727],           # LF Tips
    "6209300823": [-1002468014496],          # Psico
}

# ───────────────────────── OPÇÕES VIA ENV (opcional) ─────────────────────────
# Se quiser substituir tudo sem tocar no código, exporte CONFIG_B64 com um JSON nesta forma:
# {
#   "dest_posts": -100xxxx,
#   "dest_comments": -100yyyy,
#   "sessions": {"uid": "StringSession", ...},
#   "subs": {"uid": [-100canal1, ...], ...},
#   "links": {"-100canal": -100chat, ...}
# }
def load_config_from_env() -> Tuple[int, int, Dict[str, str], Dict[str, List[int]], Dict[int, int]]:
    cfg_b64 = os.environ.get("CONFIG_B64", "").strip()
    if not cfg_b64:
        return (DEST_POSTS_DEFAULT, DEST_COMMENTS_DEFAULT,
                SESSIONS_DEFAULT, SUBS_DEFAULT, LINKS_DEFAULT)
    try:
        raw = base64.b64decode(cfg_b64).decode("utf-8")
        obj = json.loads(raw)
        dest_posts    = int(obj["dest_posts"])
        dest_comments = int(obj["dest_comments"])
        sessions      = {str(k): str(v) for k, v in obj["sessions"].items()}
        subs          = {str(k): list(map(int, v)) for k, v in obj["subs"].items()}
        links         = {int(k): int(v) for k, v in obj["links"].items()}
        return (dest_posts, dest_comments, sessions, subs, links)
    except Exception as e:
        log.warning(f"CONFIG_B64 inválido, usando defaults. Erro: {e}")
        return (DEST_POSTS_DEFAULT, DEST_COMMENTS_DEFAULT,
                SESSIONS_DEFAULT, SUBS_DEFAULT, LINKS_DEFAULT)

DEST_POSTS, DEST_COMMENTS, SESSIONS, SUBS, LINKS = load_config_from_env()

# ───────────────────────── BOT + API ─────────────────────────
API_ID  = int(os.environ.get("TELEGRAM_API_ID", "0") or 0)
API_HASH = os.environ.get("TELEGRAM_API_HASH", "") or ""
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not (API_ID and API_HASH and BOT_TOKEN):
    raise SystemExit("Faltam TELEGRAM_API_ID / TELEGRAM_API_HASH / BOT_TOKEN")

# BOT envia TUDO (posts e chats)
bot = TelegramClient("bot_sender", API_ID, API_HASH)

# Sessões ouvintes
user_clients: Dict[str, TelegramClient] = {}

# ───────────────────────── Reply map (persistente) ─────────────────────────
DATA_DIR = "/data"
POSTMAP_FILE = os.path.join(DATA_DIR, "postmap.json")
POSTMAP: Dict[str, Dict[str, int]] = {}   # key="base:src_id" -> {"posts": id_no_DEST_POSTS, "comments": id_no_DEST_COMMENTS}

def _key(base_id: int, msg_id: int) -> str:
    return f"{base_id}:{msg_id}"

def postmap_load():
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(POSTMAP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                POSTMAP.update(data)
    except Exception:
        pass

def postmap_save():
    tmp = POSTMAP_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(POSTMAP, f, ensure_ascii=False, indent=2)
        os.replace(tmp, POSTMAP_FILE)
    except Exception as e:
        log.warning(f"postmap save fail: {e}")

postmap_load()

# ───────────────────────── Flask dashboard ─────────────────────────
app = Flask("keep_alive")

@app.get("/")
def home():
    return (
        "<h3>Encaminhador • envio via BOT</h3>"
        "<ul>"
        f"<li>DEST_POSTS: {DEST_POSTS}</li>"
        f"<li>DEST_COMMENTS: {DEST_COMMENTS}</li>"
        f"<li>LINKS (base→chat): {LINKS}</li>"
        f"<li>SESSIONS loaded: {list(SESSIONS.keys())}</li>"
        f"<li>SUBS: {SUBS}</li>"
        f"<li>POSTMAP size: {len(POSTMAP)}</li>"
        "</ul>"
        "<p>Endpoints: /status, /dump_postmap</p>"
    )

@app.get("/status")
def status():
    live = {uid: True for uid in user_clients.keys()}
    return jsonify(
        dest_posts=DEST_POSTS,
        dest_comments=DEST_COMMENTS,
        sessions=list(SESSIONS.keys()),
        subs=SUBS,
        links=LINKS,
        listeners=live,
        postmap_size=len(POSTMAP),
    )

@app.get("/dump_postmap")
def dump_postmap():
    return jsonify(POSTMAP)

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)

# ───────────────────────── Utilidades ─────────────────────────
def base_from_chat(chat_id: int) -> Optional[int]:
    for b, c in LINKS.items():
        if c == chat_id:
            return b
    return None

def extract_channel_msg_id_from_chat(m: Message) -> Optional[int]:
    """
    Fallbacks para tentar descobrir o ID do post original do canal-base:
    1) reply_to.reply_to_top_id
    2) reply_to.reply_to_msg_id
    3) None
    """
    rt = getattr(m, "reply_to", None)
    if rt:
        top_id = getattr(rt, "reply_to_top_id", None)
        if top_id:
            return int(top_id)
        rid = getattr(rt, "reply_to_msg_id", None)
        if rid:
            return int(rid)
    return None

async def send_text(dst: int, text: str, reply_to: Optional[int] = None, parse_md=True):
    try:
        return await bot.send_message(dst, text, parse_mode=("Markdown" if parse_md else None), reply_to=reply_to)
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        return await bot.send_message(dst, text, parse_mode=("Markdown" if parse_md else None), reply_to=reply_to)

async def copy_via_bot(src_client: TelegramClient, dst: int, m: Message, reply_to: Optional[int] = None) -> Message:
    """
    Copia conteúdo de m (baixando pelo src_client) e envia via BOT para dst.
    Nunca usa forward entre contas.
    """
    try:
        if m.media:
            path = await src_client.download_media(m)
            return await bot.send_file(dst, path, caption=(m.text or ""), reply_to=reply_to)
        else:
            return await bot.send_message(dst, m.text or "", reply_to=reply_to)
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        if m.media:
            path = await src_client.download_media(m)
            return await bot.send_file(dst, path, caption=(m.text or ""), reply_to=reply_to)
        else:
            return await bot.send_message(dst, m.text or "", reply_to=reply_to)

# ───────────────────────── Manipuladores ─────────────────────────
async def handle_post_from(client: TelegramClient, ev: events.NewMessage.Event):
    """
    Mensagem vinda de um CANAL-BASE:
      - Envia header+conteúdo para DEST_POSTS
      - Envia header+conteúdo para DEST_COMMENTS
      - Salva mapping base:src_id -> (posts_id, comments_id) para replies
    """
    cid = ev.chat_id
    chat = await client.get_entity(cid)
    title = getattr(chat, "title", None) or str(cid)
    header = f"📢 *{title}* (`{cid}`)"

    # posts
    await send_text(DEST_POSTS, header)
    dst_post_msg = await copy_via_bot(client, DEST_POSTS, ev.message)

    # comments (ancora da thread)
    await send_text(DEST_COMMENTS, header)
    dst_comm_msg = await copy_via_bot(client, DEST_COMMENTS, ev.message)

    POSTMAP[_key(cid, ev.message.id)] = {
        "posts": int(dst_post_msg.id),
        "comments": int(dst_comm_msg.id)
    }
    postmap_save()
    log.info(f"[POST] base={cid} src={ev.message.id} -> posts={dst_post_msg.id} comments={dst_comm_msg.id}")

async def handle_chat_from(client: TelegramClient, ev: events.NewMessage.Event):
    """
    Mensagem vinda do CHAT (megagroup) vinculado.
    Tenta responder (reply) ao post clonado em DEST_COMMENTS.
    """
    chat_id = ev.chat_id
    base_id = base_from_chat(chat_id)
    if not base_id:
        return

    # descobre o post de origem no canal-base
    src_channel_msg_id = extract_channel_msg_id_from_chat(ev.message)
    reply_to_dest: Optional[int] = None
    if src_channel_msg_id:
        mp = POSTMAP.get(_key(base_id, src_channel_msg_id))
        if mp and "comments" in mp:
            reply_to_dest = int(mp["comments"])

    # header com autor
    try:
        s = await ev.get_sender()
        sname = " ".join(filter(None, [getattr(s, "first_name", None), getattr(s, "last_name", None)])) or (getattr(s, "username", None) or "alguém")
    except Exception:
        sname = "alguém"

    ent = await client.get_entity(chat_id)
    title = getattr(ent, "title", None) or str(chat_id)
    header = f"💬 *{title}* — {sname} (`{chat_id}`)"

    await send_text(DEST_COMMENTS, header, reply_to=reply_to_dest)
    sent = await copy_via_bot(client, DEST_COMMENTS, ev.message, reply_to=reply_to_dest)
    log.info(f"[CHAT] chat={chat_id} -> comments={DEST_COMMENTS} reply_to={reply_to_dest} sent={sent.id}")

# ───────────────────────── Subir listeners das 3 sessões ─────────────────────────
async def ensure_dynamic(uid: str):
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
        ch = LINKS.get(b)
        if ch:
            allowed.add(ch)

    @cli.on(events.NewMessage(chats=list(allowed)))
    async def _dyn(ev: events.NewMessage.Event, _uid=uid):
        try:
            cid = ev.chat_id
            if cid in LINKS:       # é post do CANAL
                await handle_post_from(cli, ev)
            else:                   # é mensagem do CHAT vinculado
                await handle_chat_from(cli, ev)
        except Exception as e:
            log.exception(f"[dyn {_uid}] {e}")

    user_clients[uid] = cli
    asyncio.create_task(cli.run_until_disconnected())
    log.info(f"[dyn] ligado uid={uid} allowed={sorted(list(allowed))}")
    return cli

# ───────────────────────── Bot comandos (admin) ─────────────────────────
ADMIN_IDS = {786880968}  # você é o admin dos comandos

def is_admin(uid: int) -> bool:
    try:
        return int(uid) in ADMIN_IDS
    except Exception:
        return False

async def setup_bot_commands():
    @bot.on(events.NewMessage(pattern=r'^/admin_status$'))
    async def _status(ev):
        if not is_admin(ev.sender_id):
            return await ev.reply("🚫")
        lines = [
            f"DEST_POSTS: {DEST_POSTS}",
            f"DEST_COMMENTS: {DEST_COMMENTS}",
            f"Sessões: {len(SESSIONS)} | Dinâmicos ON: {len(user_clients)}",
        ]
        for uid in user_clients.keys():
            lines.append(f"- {uid}")
        lines.append(f"POSTMAP: {len(POSTMAP)} chaves")
        await ev.reply("\n".join(lines))

    @bot.on(events.NewMessage(pattern=r'^/admin_export$'))
    async def _export(ev):
        if not is_admin(ev.sender_id):
            return await ev.reply("🚫")
        cfg = {
            "dest_posts": DEST_POSTS,
            "dest_comments": DEST_COMMENTS,
            "sessions": SESSIONS,
            "subs": SUBS,
            "links": LINKS,
        }
        raw = json.dumps(cfg, ensure_ascii=False)
        b64 = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
        await ev.reply(f"CONFIG_B64:\n`{b64}`", parse_mode="Markdown")

    @bot.on(events.NewMessage(pattern=r'^/admin_reset_all\s+CONFIRM$'))
    async def _reset(ev):
        if not is_admin(ev.sender_id):
            return await ev.reply("🚫")
        POSTMAP.clear()
        postmap_save()
        await ev.reply("🧹 postmap limpo. (config permanece)")

    @bot.on(events.NewMessage(pattern=r'^/admin_reload$'))
    async def _reload(ev):
        if not is_admin(ev.sender_id):
            return await ev.reply("🚫")
        # não recarrego sessões (estão em código/CONFIG_B64). Apenas reporto.
        await ev.reply("🔄 reload leve: nada a recarregar (config em código/CONFIG_B64).")

# ───────────────────────── MAIN ─────────────────────────
async def main():
    # Flask
    threading.Thread(target=run_flask, daemon=True).start()

    # BOT (quem envia)
    await bot.start(bot_token=BOT_TOKEN)
    await setup_bot_commands()

    # Sobe as 3 sessões ouvintes
    for uid in list(SESSIONS.keys()):
        try:
            await ensure_dynamic(uid)
        except Exception as e:
            log.exception(f"ensure_dynamic {uid} fail: {e}")

    log.info("🤖 pronto (TRIADE, LF Tips, Psico) — envio via BOT, replies com fallback")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
