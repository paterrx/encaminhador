# main.py — clone com replies mapeados (TRIADE, LF Tips, Psico)
import os
import asyncio
import logging
import threading
from typing import Dict, List, Optional, Tuple

from flask import Flask, jsonify, Response
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.types import Message, MessageReplyHeader

# ───────────────────────────── LOG ─────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("encaminhador")

# ───────────────────────────── DESTINOS ─────────────────────────────
DEST_POSTS: int    = -1002897690215     # Canal principal (posts)
DEST_COMMENTS: int = -1002489338128     # Grupo de comentários/Chat

# ───────────────────────────── MAPA BASE↔CHAT (apenas 3 grupos) ─────────────────────────────
#  base (canal) -> chat (megagroup de discussão)
LINKS: Dict[int, int] = {
    -1002794084735: -1002722732606,  # TRIADE canal → chat
    -1002855377727: -1002813556527,  # LF Tips canal → chat
    -1002468014496: -1002333613791,  # Psico canal → chat
}
# inverso pra descobrir qual canal pertence um chat
REV_LINKS: Dict[int, int] = {v: k for k, v in LINKS.items()}

# ───────────────────────────── SESSÕES (OUVIR) ─────────────────────────────
SESSIONS: Dict[str, str] = {
    "786880968": "1AZWarzgBu1SEPUzUrUUDXtnGbkyoDEknN2-8Pfcae8iOjJNEQ6qryf1VY47_IYDt0urjMxZCvnQS6X6CNO7WRmKID2YVdRHkIm-1MWX_4NCQQPzMrlN8OsdCF-JaIx4Pt3vXhfbx1qR68ISM9yLTx8-Ud9wy5xtb1DRYRB95IzV5bimJLTEP_9N8Og7rANevX4H29_NKZkCoTA7Qg8jTeVPgK0I6ClQvVbcaxi04kiZ9vwfjsOx3YwWbZsWFLFovQRjnGezXWVPn3BxfRWiHE1sHOM8X6qsEwnWsmdjkyTDxzg8sTjLYs8Bnapw275LhRwCscROeGQ-YmvlzHZ4AqB7JyFRnfH4=",
    "435374422": "1AZWarzsBu7Rd3aDhcqB9VW1tW9nPh-UVr8HMPCwEKBh_jVQ4wAaYx8xd4ZEltEJTsUJyNWAbPSeT61ZZJGxg6vPBXfXYWaCoylT2rBullBn0ZG1VXofd4jO-tGOPy8LYb9xBvmVkmuPILGN0_ZJsz92is901v2Eys4o5ULHrp2TT9o6jwU1rFKYpv0T6PdptBrwh2XgdViewk1xjMy1bS0GZD8EltJ8FdaTqXj2DXj96TjAa3nWk1ExUKvnaWW81MytyVMjGzsCgYDeU-Z641a3c29L0iFXXjDq4H7m0-Pxy1tJG5CASlnBv4ShOOToc0W4JFTgkKZp6IF9mWGd9hvNSkSr3XYo=",
    "6209300823": "1AZWarzcBu2MRTYFPOYL8dNP86W39b2XnUIZn4VGhnJsFWuNIL1zSUqLAiBb0zq58HGRmuSsRWkrS4apG9bvRP3gsMgHvwfti0Jp4-KA-tVNdot7tLdn20u5tNY2ZVfqki_xG9VpQqgCmjMpV6___rVZLMy_bHR2IN5a8YIP2ApvANw4p_1Dw-o044FdIgREBGSQ6ONURKj45b_8Nm2y0JcRutNCCH94zAILysNxhQlIdCSahNxfiA78-FGr_fvk7WIPfHHDtVmylNUZMUpu-5UlT9OuLHxazyxDyM9uPTmh8cD3CG7JvY44652m-ajPDPlB4d3MfPIC_95uxJIJhoymrfr4HQoE=",
}

# quais bases cada sessão deve ouvir (apenas canais; o chat vem de LINKS)
SUBS: Dict[str, List[int]] = {
    "786880968": [-1002794084735],                    # TRIADE
    "435374422": [-1002855377727],                    # LF Tips
    "6209300823": [-1002468014496],                   # Psico
}

# ───────────────────────────── CREDENCIAIS ─────────────────────────────
API_ID  = int(os.environ.get("TELEGRAM_API_ID", "0") or 0)
API_HASH= os.environ.get("TELEGRAM_API_HASH", "") or ""
BOT_TOKEN = os.environ.get("BOT_TOKEN_ENCAMINHADORADMIN", "").strip()  # 8226...J59o

# ───────────────────────────── Infra Flask ─────────────────────────────
app = Flask("keep_alive")

@app.get("/")
def root():  # simples
    return Response(
        "<h3>Encaminhador — TRIADE / LF Tips / Psico</h3>"
        "<ul>"
        f"<li>DEST_POSTS: {DEST_POSTS}</li>"
        f"<li>DEST_COMMENTS: {DEST_COMMENTS}</li>"
        f"<li>Links: {LINKS}</li>"
        f"<li>Sessões: {list(SESSIONS.keys())}</li>"
        "</ul>", mimetype="text/html"
    )

@app.get("/health")
def health(): return jsonify(ok=True)

@app.get("/dash")
def dash():
    return jsonify({
        "dest_posts": DEST_POSTS,
        "dest_comments": DEST_COMMENTS,
        "links": LINKS,
        "sessions": list(SESSIONS.keys()),
        "postmap_size": len(POSTMAP),
        "msgmap_size": len(MSGMAP),
        "dyn_online": list(user_clients.keys()),
    })

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)

# ───────────────────────────── Clientes ─────────────────────────────
# Bot que ENVIA para os destinos
poster_bot = TelegramClient("poster_bot", API_ID, API_HASH)

# Clientes dinâmicos (ouvem os 3 grupos)
user_clients: Dict[str, TelegramClient] = {}

# ───────────────────────────── Mapas de replies ─────────────────────────────
# (canal_origem, id_msg) -> id_no_destino
POSTMAP: Dict[Tuple[int, int], int] = {}
# (chat_origem,  id_msg) -> id_no_destino
MSGMAP:  Dict[Tuple[int, int], int] = {}

def _prune_map(d: dict, max_len: int = 50000):
    # evita crescer para sempre
    if len(d) > max_len:
        for _ in range(len(d) - max_len // 2):
            d.pop(next(iter(d)))

def channel_or_chat_dst(chat_id: int) -> int:
    return DEST_COMMENTS if chat_id in REV_LINKS else DEST_POSTS

# ───────────────────────────── Envio/clonagem ─────────────────────────────
async def _send_text_or_media(dst: int, m: Message, reply_to: Optional[int]) -> Message:
    """Reenvia conteúdo (não usa forward) para poder respeitar reply_to."""
    if m.media:
        path = await m.download_media()
        sent = await poster_bot.send_file(dst, path, caption=(m.text or ""), reply_to=reply_to)
    else:
        sent = await poster_bot.send_message(dst, m.text or "", reply_to=reply_to)
    return sent

async def clone_post_and_map(src_chat: int, m: Message) -> int:
    """Clona um post do canal e registra em POSTMAP; devolve id no destino."""
    try:
        # header opcional – mantém identidade do canal
        ent = await poster_bot.get_entity(DEST_POSTS)  # só pra garantir sessão conectada
        _ = ent  # no-op
    except Exception:
        pass
    # reenvia conteúdo (podíamos fazer forward; reenvio garante consistência)
    sent = await _send_text_or_media(DEST_POSTS, m, reply_to=None)
    POSTMAP[(src_chat, m.id)] = sent.id
    _prune_map(POSTMAP)
    return sent.id

def _sender_name(sender) -> str:
    if not sender:
        return "alguém"
    parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
    name = " ".join([p for p in parts if p]) or (getattr(sender, "username", None) or "alguém")
    return name

async def resolve_reply_for_chat(src_chat: int, m: Message) -> Optional[int]:
    """Descobre o id no destino para responder (chat → chat ou chat → post-top)."""
    r: MessageReplyHeader = getattr(m, "reply_to", None)
    if not r:
        return None

    # 1) reply para outra msg do chat
    if getattr(r, "reply_to_msg_id", None):
        return MSGMAP.get((src_chat, r.reply_to_msg_id))

    # 2) reply para o topo (post do canal)
    top_id = getattr(r, "reply_to_top_id", None)
    if top_id:
        base = REV_LINKS.get(src_chat)  # canal dono desta discussão
        if base is not None:
            return POSTMAP.get((base, top_id))
    return None

async def clone_chat_message_and_map(cli: TelegramClient, src_chat: int, m: Message) -> None:
    """Clona uma mensagem de chat, fazendo reply correto e gravando em MSGMAP."""
    # header com nome + título do chat
    chat = await cli.get_entity(src_chat)
    title = getattr(chat, "title", None) or str(src_chat)
    sender = await m.get_sender()
    sname  = _sender_name(sender)
    header = f"💬 *{title}* — {sname} (`{src_chat}`)"
    await poster_bot.send_message(DEST_COMMENTS, header, parse_mode="Markdown")

    reply_to_dest = await resolve_reply_for_chat(src_chat, m)
    sent = await _send_text_or_media(DEST_COMMENTS, m, reply_to=reply_to_dest)

    MSGMAP[(src_chat, m.id)] = sent.id
    _prune_map(MSGMAP)

# ───────────────────────────── Dinâmicos (três sessões) ─────────────────────────────
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
        chat_id = LINKS.get(b)
        if chat_id:
            allowed.add(chat_id)

    @cli.on(events.NewMessage(chats=list(allowed)))
    async def _dyn(ev: events.NewMessage.Event, _uid=uid):
        try:
            src = ev.chat_id
            dst = channel_or_chat_dst(src)

            if dst == DEST_POSTS:
                # post de canal
                await clone_post_and_map(src, ev.message)
                log.info(f"[post {_uid}] {src} -> posts")
            else:
                # mensagem de chat
                await clone_chat_message_and_map(cli, src, ev.message)
                log.info(f"[chat {_uid}] {src} -> comments")
        except Exception as e:
            log.exception(f"[dyn {_uid}] erro: {e}")

    user_clients[uid] = cli
    asyncio.create_task(cli.run_until_disconnected())
    log.info(f"[dyn] ligado uid={uid} allowed={sorted(list(allowed))}")
    return cli

# ───────────────────────────── Bot admin (encaminhadorAdmin_bot) ─────────────────────────────
admin_bot: Optional[TelegramClient] = None
if BOT_TOKEN:
    admin_bot = TelegramClient("admin_ui_bot", API_ID, API_HASH)

async def setup_admin_bot():
    if not admin_bot:
        return
    await admin_bot.start(bot_token=BOT_TOKEN)

    @admin_bot.on(events.NewMessage(pattern=r"^/start$"))
    async def _start(ev):
        await ev.reply(
            "👋 Encaminhador online.\n\n"
            "• `/admin_status` – mostra status e tamanhos\n"
            "• Dashboard: abra `/dash` na web do Railway\n"
            "• `/listgroups UID [página] [tamanho]` – lista diálogos da sessão",
        )

    @admin_bot.on(events.NewMessage(pattern=r"^/admin_status$"))
    async def _st(ev):
        lines = [
            f"DEST_POSTS: {DEST_POSTS}",
            f"DEST_COMMENTS: {DEST_COMMENTS}",
            f"Sessões: {len(SESSIONS)} | Dinâmicos ON: {len(user_clients)}",
            f"Links: {len(LINKS)} pares | POSTMAP: {len(POSTMAP)} | MSGMAP: {len(MSGMAP)}",
        ]
        for u in user_clients.keys():
            lines.append(f"- {u}")
        await ev.reply("\n".join(lines))

    @admin_bot.on(events.NewMessage(pattern=r"^/dash$"))
    async def _dash(ev):
        await ev.reply("Abra a rota /dash no Railway (⚙️ Deploy → Open URL → /dash).")

    @admin_bot.on(events.NewMessage(pattern=r"^/listgroups(\s+.*)?$"))
    async def _lg(ev):
        txt = ev.raw_text.strip().split()
        if len(txt) < 2:
            return await ev.reply("Uso: `/listgroups UID [página] [tamanho]`", parse_mode="Markdown")
        uid = txt[1]
        page = int(txt[2]) if len(txt) >= 3 and txt[2].isdigit() else 1
        size = int(txt[3]) if len(txt) >= 4 and txt[3].isdigit() else 50
        page = max(1, page); size = max(5, min(100, size))

        if uid not in SESSIONS:
            return await ev.reply("UID não tem sessão carregada.")

        tmp = TelegramClient(StringSession(SESSIONS[uid]), API_ID, API_HASH)
        await tmp.start()
        want = page * size
        grabbed, lines = 0, []
        async for d in tmp.iter_dialogs(limit=3000):
            ent = d.entity
            if getattr(ent, "megagroup", False) or getattr(ent, "broadcast", False):
                grabbed += 1
                if grabbed > (page - 1) * size and len(lines) < size:
                    title = getattr(ent, "title", None) or getattr(ent, "username", None) or str(ent.id)
                    lines.append(f"- `{ent.id}` — {title}")
                if grabbed >= want:
                    break
        await tmp.disconnect()
        if not lines:
            return await ev.reply(f"(pág {page} vazia)")
        await ev.reply("📋 *Diálogos*:\n" + "\n".join(lines), parse_mode="Markdown")

    asyncio.create_task(admin_bot.run_until_disconnected())

# ───────────────────────────── MAIN ─────────────────────────────
async def main():
    # 1) Flask
    threading.Thread(target=run_flask, daemon=True).start()

    # 2) Conectar bot de postagem (obrigatório)
    await poster_bot.start(bot_token=BOT_TOKEN)

    # 3) Ligar os três ouvintes
    for uid in list(SESSIONS.keys()):
        try: await ensure_dynamic(uid)
        except Exception as e: log.exception(f"dyn {uid} fail: {e}")

    # 4) Subir bot admin (opcional mas recomendado)
    await setup_admin_bot()

    log.info("🤖 pronto (TRIADE, LF Tips, Psico) — envio via BOT, replies mapeados")
    await poster_bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
