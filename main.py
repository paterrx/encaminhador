# main.py — BOT único, cópia (sem forward), reply fixado por âncora estável
import os
import asyncio
import logging
import threading
from typing import Dict, List, Optional, Tuple, Union

from flask import Flask, jsonify, Response
import html as html_std

from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.types import Message

# ───────────────────────────── LOG ─────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("encaminhador")

# ───────────────────────────── DESTINOS ─────────────────────────────
DEST_POSTS: int = -1002897690215      # canal de POSTS
DEST_COMMENTS: int = -1002489338128   # canal de CHAT

# ───────────────────────────── PARES base→chat ─────────────────────────────
LINKS: Dict[int, int] = {
    -1002794084735: -1002722732606,  # TRIADE
    -1002855377727: -1002813556527,  # LF Tips
    -1002468014496: -1002333613791,  # Psico
}
INV_LINKS: Dict[int, int] = {v: k for k, v in LINKS.items()}

# ───────────────────────────── DONOS / ASSINATURAS ─────────────────────────────
SESSIONS: Dict[str, str] = {
    "786880968": "1AZWarzgBu1SEPUzUrUUDXtnGbkyoDEknN2-8Pfcae8iOjJNEQ6qryf1VY47_IYDt0urjMxZCvnQS6X6CNO7WRmKID2YVdRHkIm-1MWX_4NCQQPzMrlN8OsdCF-JaIx4Pt3vXhfbx1qR68ISM9yLTx8-Ud9wy5xtb1DRYRB95IzV5bimJLTEP_9N8Og7rANevX4H29_NKZkCoTA7Qg8jTeVPgK0I6ClQvVbcaxi04kiZ9vwfjsOx3YwWbZsWFLFovQRjnGezXWVPn3BxfRWiHE1sHOM8X6qsEwnWsmdjkyTDxzg8sTjLYs8Bnapw275LhRwCscROeGQ-YmvlzHZ4AqB7JyFRnfH4=",
    "435374422": "1AZWarzsBu7Rd3aDhcqB9VW1tW9nPh-UVr8HMPCwEKBh_jVQ4wAaYx8xd4ZEltEJTsUJyNWAbPSeT61ZZJGxg6vPBXfXYWaCoylT2rBullBn0ZG1VXofd4jO-tGOPy8LYb9xBvmVkmuPILGN0_ZJsz92is901v2Eys4o5ULHrp2TT9o6jwU1rFKYpv0T6PdptBrwh2XgdViewk1xjMy1bS0GZD8EltJ8FdaTqXj2DXj96TjAa3nWk1ExUKvnaWW81MytyVMjGzsCgYDeU-Z641a3c29L0iFXXjDq4H7m0-Pxy1tJG5CASlnBv4ShOOToc0W4JFTgkKZp6IF9mWGd9hvNSkSr3XYo=",
    "6209300823": "1AZWarzcBu2MRTYFPOYL8dNP86W39b2XnUIZn4VGhnJsFWuNIL1zSUqLAiBb0zq58HGRmuSsRWkrS4apG9bvRP3gsMgHvwfti0Jp4-KA-tVNdot7tLdn20u5tNY2ZVfqki_xG9VpQqgCmjMpV6___rVZLMy_bHR2IN5a8YIP2ApvANw4p_1Dw-o044FdIgREBGSQ6ONURKj45b_8Nm2y0JcRutNCCH94zAILysNxhQlIdCSahNxfiA78-FGr_fvk7WIPfHHDtVmylNUZMUpu-5UlT9OuLHxazyxDyM9uPTmh8cD3CG7JvY44652m-ajPDPlB4d3MfPIC_95uxJIJhoymrfr4HQoE=",
}
SUBS: Dict[str, List[int]] = {
    "786880968": [-1002794084735, -1002855377727, -1002468014496],
    "435374422": [-1002855377727],
    "6209300823": [-1002468014496],
}

# ───────────────────────────── API + BOT ─────────────────────────────
API_ID = int(os.environ.get("TELEGRAM_API_ID", "0") or 0)
API_HASH = os.environ.get("TELEGRAM_API_HASH", "") or ""
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# ───────────────────────────── WEB ─────────────────────────────
app = Flask("keep_alive")

@app.get("/")
def root() -> str:
    return (
        "<h3>Encaminhador</h3>"
        f"<p>DEST_POSTS: {DEST_POSTS} | DEST_COMMENTS: {DEST_COMMENTS}</p>"
        f"<p>Sessões: {len(SESSIONS)} | Pares: {len(LINKS)}</p>"
        '<p>Dashboard: <a href="/dash">/dash</a></p>'
    )

@app.get("/health")
def health(): return jsonify(ok=True)

@app.get("/dash")
def dash() -> Response:
    rows = []
    rows.append("<h2>Resumo</h2>")
    rows.append(f"<p>Dinâmicos ON: {len(user_clients)} | Pares: {len(LINKS)}</p>")
    rows.append("<h3>Links (base → chat)</h3><pre>")
    for b, c in LINKS.items():
        rows.append(f"{html_std.escape(str(b))} → {html_std.escape(str(c))}")
    rows.append("</pre>")
    rows.append("<h3>Âncoras</h3><pre>")
    for base, sub in post_map.items():
        for top_src, dest_id in sub.items():
            rows.append(f"base {base} :: {top_src} → {dest_id}")
    rows.append("</pre>")
    return Response("\n".join(rows), mimetype="text/html")

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)

# ───────────────────────────── ESTADO (reply map) ─────────────────────────────
post_map: Dict[int, Dict[int, int]] = {}        # base_id -> {src_top_id: dest_post_id}
last_anchor: Dict[int, Tuple[int, int]] = {}    # base_id -> (src_top_id, dest_post_id)

def set_anchor(base_id: int, src_top_id: int, dest_id: int):
    post_map.setdefault(base_id, {})[src_top_id] = dest_id
    last_anchor[base_id] = (src_top_id, dest_id)

def get_anchor(base_id: int, src_top_id: Optional[int]) -> Optional[int]:
    if src_top_id is not None:
        got = post_map.get(base_id, {}).get(src_top_id)
        if got:
            return got
    la = last_anchor.get(base_id)
    return la[1] if la else None

# ───────────────────────────── CLIENTES ─────────────────────────────
bot_client: Optional[TelegramClient] = None
user_clients: Dict[str, TelegramClient] = {}
user_handlers: Dict[str, Tuple] = {}

def is_chat_id(chat_id: int) -> bool: return chat_id in INV_LINKS
def dest_for(chat_id: int) -> int: return DEST_COMMENTS if is_chat_id(chat_id) else DEST_POSTS

async def _send_copy(dst: int, msg: Message, reply_to: Optional[int]) -> int:
    """Copia SEM forward. Retorna o id exato da nova mensagem criada."""
    async def _do() -> Union[Message, List[Message]]:
        if msg.media:
            path = await msg.download_media()
            return await bot_client.send_file(dst, path, caption=(msg.text or ""), reply_to=reply_to)
        else:
            return await bot_client.send_message(dst, msg.text or "", reply_to=reply_to)

    try:
        res = await _do()
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        res = await _do()

    if isinstance(res, list):
        return res[0].id if res else 0
    return res.id

def _sender_name(sender) -> str:
    name = " ".join(filter(None, [getattr(sender, "first_name", None),
                                  getattr(sender, "last_name", None)])).strip()
    return name or getattr(sender, "username", None) or "alguém"

def _title(ent) -> str:
    return getattr(ent, "title", None) or getattr(ent, "username", None) or str(getattr(ent, "id", "?"))

def _extract_top_id(m: Message) -> Optional[int]:
    try:
        rt = getattr(m, "reply_to", None)
        top = getattr(rt, "reply_to_top_id", None)
        if top:
            return int(top)
    except Exception:
        pass
    try:
        top = getattr(m, "reply_to_top_id", None)
        if top:
            return int(top)
    except Exception:
        pass
    return None

# ───────────────────────────── DINÂMICOS ─────────────────────────────
def allowed_for(uid: str) -> List[int]:
    bases = SUBS.get(uid, [])
    s = set(bases)
    for b in bases:
        chat = LINKS.get(b)
        if chat:
            s.add(chat)
    return sorted(list(s))

async def ensure_dynamic(uid: str, force: bool = False) -> Optional[TelegramClient]:
    sess = SESSIONS.get(uid)
    if not sess:
        return None

    cli = user_clients.get(uid)
    if cli is None:
        cli = TelegramClient(StringSession(sess), API_ID, API_HASH)
        await cli.start()
        user_clients[uid] = cli

    if force and uid in user_handlers:
        cb, evb = user_handlers.pop(uid)
        try:
            cli.remove_event_handler(cb, evb)
        except Exception:
            pass

    if uid not in user_handlers:
        chats_list = allowed_for(uid)
        evb = events.NewMessage(chats=chats_list)

        async def _cb(ev: events.NewMessage.Event, _uid=uid):
            try:
                cid = ev.chat_id
                ent = await cli.get_entity(cid)
                title = _title(ent)
                dest = dest_for(cid)

                # 1) Mensagens no canal-base → copia e cria âncora com o ID REAL retornado
                if not is_chat_id(cid):
                    header = f"📢 *{title}* (`{cid}`)"
                    await bot_client.send_message(dest, header, parse_mode="Markdown")
                    content_id = await _send_copy(dest, ev.message, reply_to=None)
                    set_anchor(cid, ev.id, content_id)
                    log.info(f"[post] base={cid} src_top={ev.id} -> dest_id={content_id}")
                    return

                # 2) Mensagens no chat (vinculado ao post)
                base_id = INV_LINKS.get(cid)

                # Ignora o "espelho" que o Telegram injeta no chat (fwd_from do canal)
                if getattr(ev.message, "fwd_from", None) is not None and ev.message.reply_to is None:
                    log.debug(f"[chat] espelho ignorado base={base_id}")
                    return

                top_src = _extract_top_id(ev.message)  # geralmente vem certinho
                anchor_dest = get_anchor(base_id, top_src)  # senão, usa último da base

                sender = await ev.get_sender()
                who = _sender_name(sender)
                header = f"💬 *{title}* — {who} (`{cid}`)"
                await bot_client.send_message(dest, header, parse_mode="Markdown", reply_to=anchor_dest)
                await _send_copy(dest, ev.message, reply_to=anchor_dest)
                log.info(f"[chat] base={base_id} top_src={top_src} -> reply_to={anchor_dest}")
            except Exception as e:
                log.exception(f"[dyn {_uid}] fail: {e}")

        cli.add_event_handler(_cb, evb)
        user_handlers[uid] = (_cb, evb)
        log.info(f"[dyn] ligado uid={uid} allowed={chats_list}")
    return cli

# ───────────────────────────── COMANDOS DO BOT ─────────────────────────────
def parse_int(s: str) -> Optional[int]:
    try:
        return int(s)
    except Exception:
        return None

async def listgroups_for(uid: str, page: int, size: int) -> List[str]:
    cli = user_clients.get(uid)
    temp = False
    if cli is None:
        sess = SESSIONS.get(uid)
        if not sess:
            return ["UID desconhecido."]
        cli = TelegramClient(StringSession(sess), API_ID, API_HASH)
        await cli.start()
        temp = True

    out, grabbed = [], 0
    want = page * size
    async for dlg in cli.iter_dialogs(limit=3000):
        ent = dlg.entity
        if getattr(ent, "megagroup", False) or getattr(ent, "broadcast", False):
            cid = getattr(ent, "id", None)
            title = _title(ent)
            grabbed += 1
            if grabbed > (page - 1) * size and len(out) < size:
                out.append(f"- `{cid}` — {title}")
            if grabbed >= want:
                break

    if temp:
        try: await cli.disconnect()
        except Exception: pass

    return out or ["(vazio)"]

async def setup_bot_commands():
    if not BOT_TOKEN:
        log.error("BOT_TOKEN não definido.")
        raise SystemExit(1)

    global bot_client
    bot_client = TelegramClient("admin_bot_session", API_ID, API_HASH)
    await bot_client.start(bot_token=BOT_TOKEN)

    @bot_client.on(events.NewMessage(pattern=r'^/start$'))
    async def _start(ev):
        await ev.reply(
            "👋 Encaminhador online.\n\n"
            "• `/admin_status` — status\n"
            "• `/listgroups [OWNER_ID] [página] [tamanho]`\n"
            "• `/subscribe OWNER_ID BASE_ID`\n"
            "• `/linkchat OWNER_ID BASE_ID CHAT_ID`\n"
            "• Dashboard: abra `/dash` no Railway",
            parse_mode="Markdown"
        )

    @bot_client.on(events.NewMessage(pattern=r'^/admin_status$'))
    async def _status(ev):
        lines = [
            f"DEST_POSTS: {DEST_POSTS}",
            f"DEST_COMMENTS: {DEST_COMMENTS}",
            f"Sessões: {len(SESSIONS)} | Dinâmicos ON: {len(user_clients)}",
            f"Links: {len(LINKS)} pares | POSTMAP: {sum(len(v) for v in post_map.values())}",
        ]
        for uid in sorted(user_clients.keys()):
            lines.append(f"- {uid}")
        await ev.reply("\n".join(lines))

    @bot_client.on(events.NewMessage(pattern=r'^/listgroups'))
    async def _list(ev):
        parts = ev.raw_text.strip().split()
        uid = (parts[1] if len(parts) >= 2 and parts[1].isdigit() else "786880968")
        page = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 1
        size = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 50
        page = max(1, page)
        size = min(100, max(5, size))
        rows = await listgroups_for(uid, page, size)
        await ev.reply(f"📋 *Grupos/Canais* (owner `{uid}`, pág {page}, tam {size}):\n" + "\n".join(rows),
                       parse_mode="Markdown")

    @bot_client.on(events.NewMessage(pattern=r'^/subscribe '))
    async def _subscribe(ev):
        parts = ev.raw_text.strip().split()
        if len(parts) != 3:
            return await ev.reply("Uso: `/subscribe OWNER_ID BASE_CHANNEL_ID`", parse_mode="Markdown")
        owner = parts[1]
        base = parse_int(parts[2])
        if base is None:
            return await ev.reply("ID inválido.")
        SUBS.setdefault(owner, [])
        if base not in SUBS[owner]:
            SUBS[owner].append(base)
        await ensure_dynamic(owner, force=True)
        await ev.reply(f"✅ {owner} assina `{base}`", parse_mode="Markdown")

    @bot_client.on(events.NewMessage(pattern=r'^/linkchat '))
    async def _link(ev):
        parts = ev.raw_text.strip().split()
        if len(parts) != 4:
            return await ev.reply("Uso: `/linkchat OWNER_ID BASE_ID CHAT_ID`", parse_mode="Markdown")
        owner = parts[1]
        base = parse_int(parts[2])
        chat = parse_int(parts[3])
        if base is None or chat is None:
            return await ev.reply("IDs inválidos.")
        LINKS[base] = chat
        INV_LINKS[chat] = base
        await ensure_dynamic(owner, force=True)
        await ev.reply(f"🔗 `{base}` → `{chat}` vinculado", parse_mode="Markdown")

    asyncio.create_task(bot_client.run_until_disconnected())

# ───────────────────────────── MAIN ─────────────────────────────
async def main():
    threading.Thread(target=run_flask, daemon=True).start()
    await setup_bot_commands()
    for uid in list(SESSIONS.keys()):
        try:
            await ensure_dynamic(uid, force=True)
        except Exception as e:
            log.exception(f"dyn {uid} fail on start: {e}")
    log.info("🤖 pronto — cópia sempre (sem forward) + reply por âncora estável")
    any_cli = next(iter(user_clients.values()))
    await any_cli.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
