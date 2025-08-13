# main.py — hardcoded, envio via BOT, com listagem/assinatura ao vivo
import os
import asyncio
import logging
import threading
from typing import Dict, List, Optional, Tuple

from flask import Flask, jsonify, Response
import html as html_std  # <<<<<< CORRETO
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.types import Message

# ───────────────────────────── LOG ─────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("encaminhador")

# ───────────────────────────── HARD-CODE CONFIG ─────────────────────────────
# Destinos
DEST_POSTS: int = -1002897690215      # canal de POSTS
DEST_COMMENTS: int = -1002489338128   # canal de CHAT/clonagem de comentários

# Pares base→chat (o que já sabemos)
LINKS: Dict[int, int] = {
    -1002794084735: -1002722732606,  # TRIADE
    -1002855377727: -1002813556527,  # LF Tips
    -1002468014496: -1002333613791,  # Psico
}

# Strings de sessão (donos)
SESSIONS: Dict[str, str] = {
    "786880968": "1AZWarzgBu1SEPUzUrUUDXtnGbkyoDEknN2-8Pfcae8iOjJNEQ6qryf1VY47_IYDt0urjMxZCvnQS6X6CNO7WRmKID2YVdRHkIm-1MWX_4NCQQPzMrlN8OsdCF-JaIx4Pt3vXhfbx1qR68ISM9yLTx8-Ud9wy5xtb1DRYRB95IzV5bimJLTEP_9N8Og7rANevX4H29_NKZkCoTA7Qg8jTeVPgK0I6ClQvVbcaxi04kiZ9vwfjsOx3YwWbZsWFLFovQRjnGezXWVPn3BxfRWiHE1sHOM8X6qsEwnWsmdjkyTDxzg8sTjLYs8Bnapw275LhRwCscROeGQ-YmvlzHZ4AqB7JyFRnfH4=",
    "435374422": "1AZWarzsBu7Rd3aDhcqB9VW1tW9nPh-UVr8HMPCwEKBh_jVQ4wAaYx8xd4ZEltEJTsUJyNWAbPSeT61ZZJGxg6vPBXfXYWaCoylT2rBullBn0ZG1VXofd4jO-tGOPy8LYb9xBvmVkmuPILGN0_ZJsz92is901v2Eys4o5ULHrp2TT9o6jwU1rFKYpv0T6PdptBrwh2XgdViewk1xjMy1bS0GZD8EltJ8FdaTqXj2DXj96TjAa3nWk1ExUKvnaWW81MytyVMjGzsCgYDeU-Z641a3c29L0iFXXjDq4H7m0-Pxy1tJG5CASlnBv4ShOOToc0W4JFTgkKZp6IF9mWGd9hvNSkSr3XYo=",
    "6209300823": "1AZWarzcBu2MRTYFPOYL8dNP86W39b2XnUIZn4VGhnJsFWuNIL1zSUqLAiBb0zq58HGRmuSsRWkrS4apG9bvRP3gsMgHvwfti0Jp4-KA-tVNdot7tLdn20u5tNY2ZVfqki_xG9VpQqgCmjMpV6___rVZLMy_bHR2IN5a8YIP2ApvANw4p_1Dw-o044FdIgREBGSQ6ONURKj45b_8Nm2y0JcRutNCCH94zAILysNxhQlIdCSahNxfiA78-FGr_fvk7WIPfHHDtVmylNUZMUpu-5UlT9OuLHxazyxDyM9uPTmh8cD3CG7JvY44652m-ajPDPlB4d3MfPIC_95uxJIJhoymrfr4HQoE=",
}

# Assinaturas por dono (apenas base; o chat vem de LINKS)
SUBS: Dict[str, List[int]] = {
    "786880968": [-1002794084735, -1002855377727, -1002468014496],  # triade, lf, psico
    "435374422": [-1002855377727],
    "6209300823": [-1002468014496],
}

# API e Bot
API_ID = int(os.environ.get("TELEGRAM_API_ID", "0") or 0)
API_HASH = os.environ.get("TELEGRAM_API_HASH", "") or ""
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()  # @encaminhadorAdmin_bot

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
    return Response("\n".join(rows), mimetype="text/html")

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)

# ───────────────────────────── Core de envio ─────────────────────────────
bot_client: Optional[TelegramClient] = None
user_clients: Dict[str, TelegramClient] = {}
user_handlers: Dict[str, Tuple] = {}  # uid -> (callback, event_builder)

def is_chat_id(chat_id: int) -> bool:
    return chat_id in LINKS.values()

def dst_for(chat_id: int) -> int:
    return DEST_COMMENTS if is_chat_id(chat_id) else DEST_POSTS

async def send_with_fallback(dst: int, m: Message):
    try:
        await m.forward_to(dst)
        return
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        try:
            await m.forward_to(dst)
            return
        except Exception:
            pass
    except Exception:
        pass
    try:
        if m.media:
            path = await m.download_media()
            await bot_client.send_file(dst, path, caption=(m.text or ""))
        else:
            await bot_client.send_message(dst, m.text or "")
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        if m.media:
            path = await m.download_media()
            await bot_client.send_file(dst, path, caption=(m.text or ""))
        else:
            await bot_client.send_message(dst, m.text or "")

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
                dest = dst_for(cid)
                ent = await cli.get_entity(cid)
                title = getattr(ent, "title", None) or getattr(ent, "username", None) or str(cid)

                if is_chat_id(cid):
                    sender = await ev.get_sender()
                    sname = " ".join(filter(None, [
                        getattr(sender, "first_name", None),
                        getattr(sender, "last_name", None),
                    ])) or (getattr(sender, "username", None) or "alguém")
                    header = f"💬 *{title}* — {sname} (`{cid}`)"
                else:
                    header = f"📢 *{title}* (`{cid}`)"

                await bot_client.send_message(dest, header, parse_mode="Markdown")
                await send_with_fallback(dest, ev.message)
                log.info(f"[dyn {_uid}] {cid} -> {dest}")
            except Exception as e:
                log.exception(f"[dyn {_uid}] fail: {e}")

        cli.add_event_handler(_cb, evb)
        user_handlers[uid] = (_cb, evb)
        log.info(f"[dyn] ligado uid={uid} allowed={chats_list}")
    return cli

# ───────────────────────────── Bot: comandos ─────────────────────────────
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
        log.warning("BOT_TOKEN não definido; comandos desativados.")
        return

    global bot_client
    bot_client = TelegramClient("admin_bot_session", API_ID, API_HASH)
    await bot_client.start(bot_token=BOT_TOKEN)

    @bot_client.on(events.NewMessage(pattern=r'^/start$'))
    async def _start(ev):
        await ev.reply(
            "👋 Encaminhador online.\n\n"
            "• `/admin_status` — status geral\n"
            "• `/listgroups [OWNER_ID] [página] [tamanho]` — lista canais/grupos\n"
            "• `/subscribe OWNER_ID BASE_ID` — assina um canal base\n"
            "• `/linkchat OWNER_ID BASE_ID CHAT_ID` — vincula o chat do canal\n"
            "• Dashboard: abra `/dash` na url do Railway"
        )

    @bot_client.on(events.NewMessage(pattern=r'^/admin_status$'))
    async def _status(ev):
        lines = [
            f"DEST_POSTS: {DEST_POSTS}",
            f"DEST_COMMENTS: {DEST_COMMENTS}",
            f"Sessões: {len(SESSIONS)} | Dinâmicos ON: {len(user_clients)}",
            f"Links: {len(LINKS)} pares"
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
        await ev.reply(f"✅ Assinado owner `{owner}` em `{base}`", parse_mode="Markdown")

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
        await ensure_dynamic(owner, force=True)
        await ev.reply(f"🔗 Vinculado `{base}` → `{chat}` (owner `{owner}`)", parse_mode="Markdown")

    asyncio.create_task(bot_client.run_until_disconnected())

# ───────────────────────────── MAIN ─────────────────────────────
async def main():
    # web
    threading.Thread(target=run_flask, daemon=True).start()

    # BOT primeiro (para garantir que bot_client exista antes de chegar msg)
    await setup_bot_commands()

    # inicia dinâmicos para todos os donos configurados
    for uid in list(SESSIONS.keys()):
        try:
            await ensure_dynamic(uid, force=True)
        except Exception as e:
            log.exception(f"dyn {uid} fail on start: {e}")

    log.info("🤖 pronto (TRIADE, LF Tips, Psico) — envio via BOT, replies com fallback")
    # pendura no primeiro cliente para aguardar
    any_cli = next(iter(user_clients.values()))
    await any_cli.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
