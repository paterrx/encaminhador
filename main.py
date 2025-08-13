# main.py — BOT único, cópia (sem forward), reply estável por base→chat
import os
import asyncio
import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

from flask import Flask, jsonify, Response
import html as html_std

from telethon import TelegramClient, events, errors, functions
from telethon.sessions import StringSession
from telethon.tl.types import Message

# ───────────────────────────── LOG ─────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("encaminhador")

# ───────────────────────────── HARD-CODE CONFIG ─────────────────────────────
# DESTINOS
DEST_POSTS: int = -1002897690215      # canal de POSTS
DEST_COMMENTS: int = -1002489338128   # canal de CHAT/clonagem de comentários

# PARES base→chat (apenas estes 3)
LINKS: Dict[int, int] = {
    -1002794084735: -1002722732606,  # TRIADE
    -1002855377727: -1002813556527,  # LF Tips
    -1002468014496: -1002333613791,  # Psico
}
INV_LINKS: Dict[int, int] = {v: k for k, v in LINKS.items()}

# SESSÕES (ouvir)
SESSIONS: Dict[str, str] = {
    "786880968": "1AZWarzgBu1SEPUzUrUUDXtnGbkyoDEknN2-8Pfcae8iOjJNEQ6qryf1VY47_IYDt0urjMxZCvnQS6X6CNO7WRmKID2YVdRHkIm-1MWX_4NCQQPzMrlN8OsdCF-JaIx4Pt3vXhfbx1qR68ISM9yLTx8-Ud9wy5xtb1DRYRB95IzV5bimJLTEP_9N8Og7rANevX4H29_NKZkCoTA7Qg8jTeVPgK0I6ClQvVbcaxi04kiZ9vwfjsOx3YwWbZsWFLFovQRjnGezXWVPn3BxfRWiHE1sHOM8X6qsEwnWsmdjkyTDxzg8sTjLYs8Bnapw275LhRwCscROeGQ-YmvlzHZ4AqB7JyFRnfH4=",
    "435374422": "1AZWarzsBu7Rd3aDhcqB9VW1tW9nPh-UVr8HMPCwEKBh_jVQ4wAaYx8xd4ZEltEJTsUJyNWAbPSeT61ZZJGxg6vPBXfXYWaCoylT2rBullBn0ZG1VXofd4jO-tGOPy8LYb9xBvmVkmuPILGN0_ZJsz92is901v2Eys4o5ULHrp2TT9o6jwU1rFKYpv0T6PdptBrwh2XgdViewk1xjMy1bS0GZD8EltJ8FdaTqXj2DXj96TjAa3nWk1ExUKvnaWW81MytyVMjGzsCgYDeU-Z641a3c29L0iFXXjDq4H7m0-Pxy1tJG5CASlnBv4ShOOToc0W4JFTgkKZp6IF9mWGd9hvNSkSr3XYo=",
    "6209300823": "1AZWarzcBu2MRTYFPOYL8dNP86W39b2XnUIZn4VGhnJsFWuNIL1zSUqLAiBb0zq58HGRmuSsRWkrS4apG9bvRP3gsMgHvwfti0Jp4-KA-tVNdot7tLdn20u5tNY2ZVfqki_xG9VpQqgCmjMpV6___rVZLMy_bHR2IN5a8YIP2ApvANw4p_1Dw-o044FdIgREBGSQ6ONURKj45b_8Nm2y0JcRutNCCH94zAILysNxhQlIdCSahNxfiA78-FGr_fvk7WIPfHHDtVmylNUZMUpu-5UlT9OuLHxazyxDyM9uPTmh8cD3CG7JvY44652m-ajPDPlB4d3MfPIC_95uxJIJhoymrfr4HQoE=",
}

# ASSINATURAS por dono (apenas base; chat vem de LINKS)
SUBS: Dict[str, List[int]] = {
    "786880968": [-1002794084735, -1002855377727, -1002468014496],
    "435374422": [-1002855377727],
    "6209300823": [-1002468014496],
}

# API + BOT
API_ID = int(os.environ.get("TELEGRAM_API_ID", "0") or 0)
API_HASH = (os.environ.get("TELEGRAM_API_HASH", "") or "").strip()
BOT_TOKEN = (os.environ.get("BOT_TOKEN", "") or "").strip()  # @encaminhadorAdmin_bot

# ───────────────────────────── Infra web ─────────────────────────────
app = Flask("keep_alive")

@app.get("/")
def root() -> str:
    return (
        "<h3>Encaminhador (hardcoded)</h3>"
        "<ul>"
        f"<li>DEST_POSTS: {DEST_POSTS}</li>"
        f"<li>DEST_COMMENTS: {DEST_COMMENTS}</li>"
        f"<li>Sessões: {list(SESSIONS.keys())}</li>"
        f"<li>Links (pares): {len(LINKS)}</li>"
        "</ul>"
        '<p>Abra <code>/dash</code>.</p>'
    )

@app.get("/health")
def health() -> dict:
    return {"ok": True, "sessions": len(SESSIONS)}

@app.get("/dash")
def dash() -> Response:
    rows = []
    rows.append("<h2>Resumo</h2>")
    rows.append(f"<p>DEST_POSTS: {DEST_POSTS} | DEST_COMMENTS: {DEST_COMMENTS}</p>")
    rows.append(f"<p>Sessões: {len(SESSIONS)} | Dinâmicos ON: {len(user_clients)}</p>")
    rows.append("<h3>Links (base → chat)</h3><pre>")
    for b, c in LINKS.items():
        rows.append(f"{html_std.escape(str(b))} → {html_std.escape(str(c))}")
    rows.append("</pre>")
    rows.append("<h3>Últimos posts (base → dest_id, ts)</h3><pre>")
    for b, info in last_post_by_base.items():
        rows.append(f"{b} → {info[0]} @ {time.strftime('%H:%M:%S', time.localtime(info[1]))}")
    rows.append("</pre>")
    return Response("\n".join(rows), mimetype="text/html")

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)

# ───────────────────────────── Núcleo ─────────────────────────────
bot_client: Optional[TelegramClient] = None
user_clients: Dict[str, TelegramClient] = {}
user_handlers: Dict[str, Tuple] = {}  # uid -> (callback, event_builder)

# Para reply estável: base_id -> (dest_post_msg_id, ts)
last_post_by_base: Dict[int, Tuple[int, float]] = {}

def is_base(chat_id: int) -> bool:
    return chat_id in LINKS.keys()

def is_chat(chat_id: int) -> bool:
    return chat_id in INV_LINKS.keys()

def base_of_chat(chat_id: int) -> Optional[int]:
    return INV_LINKS.get(chat_id)

def dst_for(chat_id: int) -> int:
    return DEST_COMMENTS if is_chat(chat_id) else DEST_POSTS

async def copy_message(dst: int, m: Message, reply_to: Optional[int] = None) -> Message:
    """
    Copia a mensagem (texto com entities; mídia via download/upload).
    Retorna a mensagem enviada (Message) — usamos o id para mapear reply.
    """
    try:
        if m.media:
            path = await m.download_media()
            sent = await bot_client.send_file(dst, path, caption=(m.message or ""), reply_to=reply_to)
        else:
            sent = await bot_client.send_message(dst, m.message or "", entities=m.entities, reply_to=reply_to)
        return sent
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        return await copy_message(dst, m, reply_to)
    except Exception as e:
        log.exception(f"[copy] falhou: {type(e).__name__}: {e}")
        # último recurso: texto simples
        txt = m.message or ""
        return await bot_client.send_message(dst, txt, reply_to=reply_to)

def allowed_for(uid: str) -> List[int]:
    bases = SUBS.get(uid, [])
    s = set(bases)
    for b in bases:
        c = LINKS.get(b)
        if c:
            s.add(c)
    return sorted(list(s))

# ─────────────── Listeners por sessão (apenas ouvir) ───────────────
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
        cb, evb = user_handlers.pop(uid, (None, None))
        if cb and evb:
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
                title = getattr(ent, "title", None) or getattr(ent, "username", None) or str(cid)
                dest = dst_for(cid)

                # BASE → publica post + grava mapeamento para replies
                if is_base(cid):
                    # cabeçalho
                    header = f"📣 *{title}* (`{cid}`)"
                    await bot_client.send_message(DEST_POSTS, header, parse_mode="Markdown")
                    sent = await copy_message(DEST_POSTS, ev.message, reply_to=None)
                    last_post_by_base[cid] = (sent.id, time.time())
                    log.info(f"[base {_uid}] {cid} → {DEST_POSTS} (dest_id={sent.id})")
                    return

                # CHAT → responde ao último post da base correspondente
                if is_chat(cid):
                    b = base_of_chat(cid)
                    reply_to_id: Optional[int] = None
                    if b and b in last_post_by_base:
                        reply_to_id = last_post_by_base[b][0]

                    # nome do autor na frente do texto
                    try:
                        s = await ev.get_sender()
                        sname = " ".join(filter(None, [getattr(s, "first_name", None), getattr(s, "last_name", None)])) \
                                or (getattr(s, "username", None) or "alguém")
                    except Exception:
                        sname = "alguém"

                    # “cabeçalho inline” (uma única msg): nome + conteúdo copiado
                    # Se houver mídia, colocamos o nome acima como texto puro
                    if ev.message.media:
                        await bot_client.send_message(
                            DEST_COMMENTS,
                            f"💬 *{title}* — {sname} (`{cid}`)",
                            parse_mode="Markdown",
                            reply_to=reply_to_id
                        )
                        await copy_message(DEST_COMMENTS, ev.message, reply_to=reply_to_id)
                    else:
                        # prefixar o nome ao corpo mantendo entities do usuário
                        prefix = f"{sname}: "
                        body = (ev.message.message or "")
                        sent = await bot_client.send_message(
                            DEST_COMMENTS,
                            prefix + body,
                            # entities não dá pra “deslocar” facilmente; enviamos sem entities no modo prefixado
                            reply_to=reply_to_id
                        )
                    log.info(f"[chat {_uid}] {cid} → {DEST_COMMENTS} (reply_to={reply_to_id})")
                    return

            except Exception as e:
                log.exception(f"[dyn {_uid}] falha: {type(e).__name__}: {e}")

        cli.add_event_handler(_cb, evb)
        user_handlers[uid] = (_cb, evb)
        log.info(f"[dyn] ligado uid={uid} allowed={chats_list}")
    return cli

# ─────────────── BOT de comandos ───────────────
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
            title = getattr(ent, "title", None) or getattr(ent, "username", None) or str(cid)
            grabbed += 1
            if grabbed > (page - 1) * size and len(out) < size:
                out.append(f"- `{cid}` — {title}")
            if grabbed >= want:
                break

    if temp:
        try:
            await cli.disconnect()
        except Exception:
            pass

    if not out:
        out.append("(vazio)")
    return out

async def setup_bot_commands():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN vazio — configure o token do @encaminhadorAdmin_bot")

    global bot_client
    bot_client = TelegramClient("admin_bot_session", API_ID, API_HASH)
    await bot_client.start(bot_token=BOT_TOKEN)

    @bot_client.on(events.NewMessage(pattern=r'^/start$'))
    async def _start(ev):
        await ev.reply(
            "👋 Encaminhador online.\n\n"
            "• `/admin_status` — status e tamanhos\n"
            "• `/listgroups [OWNER_ID] [página] [tamanho]` — lista diálogos da sessão\n"
            "• `/subscribe OWNER_ID BASE_ID` — assina um canal-base\n"
            "• `/linkchat OWNER_ID BASE_ID CHAT_ID` — define/atualiza chat vinculado\n"
            "• `/debug_allowed OWNER_ID` — mostra os ids que o UID está ouvindo\n"
            "• Dashboard: abra `/dash` na url do Railway"
        )

    @bot_client.on(events.NewMessage(pattern=r'^/admin_status$'))
    async def _status(ev):
        lines = [
            f"DEST_POSTS: {DEST_POSTS}",
            f"DEST_COMMENTS: {DEST_COMMENTS}",
            f"Sessões: {len(SESSIONS)} | Dinâmicos ON: {len(user_clients)}",
            f"Links: {len(LINKS)} pares | POSTMAP: {len(last_post_by_base)}",
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

    @bot_client.on(events.NewMessage(pattern=r'^/subscribe'))
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
        await ev.reply(f"✅ {owner} assina `{base}`.", parse_mode="Markdown")

    @bot_client.on(events.NewMessage(pattern=r'^/linkchat'))
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
        INV_LINKS.clear()
        INV_LINKS.update({v: k for k, v in LINKS.items()})
        await ensure_dynamic(owner, force=True)
        await ev.reply(f"🔗 {base} → {chat} vinculado para `{owner}`.", parse_mode="Markdown")

    @bot_client.on(events.NewMessage(pattern=r'^/debug_allowed'))
    async def _dbg(ev):
        parts = ev.raw_text.strip().split()
        if len(parts) != 2:
            return await ev.reply("Uso: `/debug_allowed OWNER_ID`", parse_mode="Markdown")
        uid = parts[1]
        ids = allowed_for(uid)
        await ev.reply("Allowed:\n" + "\n".join(map(str, ids)))

    asyncio.create_task(bot_client.run_until_disconnected())

# ───────────────────────────── MAIN ─────────────────────────────
async def main():
    threading.Thread(target=run_flask, daemon=True).start()

    # BOT primeiro (precisamos dele para postar)
    await setup_bot_commands()

    # Subimos os 3 listeners (e amigos) já com force=True
    for uid in list(SESSIONS.keys()):
        try:
            await ensure_dynamic(uid, force=True)
        except Exception as e:
            log.exception(f"dyn {uid} fail on start: {e}")

    log.info("🤖 pronto — BOT posta, sessão(ões) apenas escutam; reply por base->último post")

    # pendurar em um cliente qualquer
    any_cli = next(iter(user_clients.values()))
    await any_cli.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
