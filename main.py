# main.py — "porcão", tudo hardcoded, sem depender do Railway/ENV
import os
import asyncio
import logging
import threading
from typing import Dict, List, Optional

from flask import Flask, jsonify
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.types import Message

# ───────────────────────────── LOG ─────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s"
)
log = logging.getLogger("encaminhador")

# ───────────────────────────── HARD-CODE CONFIG ─────────────────────────────
# 1) Destinos (deixa assim)
DEST_POSTS: int = -1002897690215      # canal onde entram os POSTS
DEST_COMMENTS: int = -1002489338128   # canal onde entram as MENSAGENS DE CHAT

# 2) Canais fixos seus (serão ouvidos pela sua própria conta)
SOURCE_FIXED: List[int] = [
    -2794084735,  # TRIADE (canal)
    -2460735067,  # ALTS (canal)
]

# 3) Mapa base->chat (chats vinculados)
LINKS: Dict[int, int] = {
    -2794084735: -2722732606,  # TRIADE canal -> chat
    -2460735067: -2779352586,  # ALTS canal -> chat
    -2855377727: -2813556527,  # LF Tips canal -> chat
    -2468014496: -2333613791,  # Psico canal -> chat
    -2770017676: -2548835100,  # Tiki-Taka canal -> chat
}

# 4) Strings de sessão (hardcoded)
SESSIONS: Dict[str, str] = {
    # SUA string (usada para ENVIAR e também ouvir fixos)
    "786880968": "1AZWarzgBu1SEPUzUrUUDXtnGbkyoDEknN2-8Pfcae8iOjJNEQ6qryf1VY47_IYDt0urjMxZCvnQS6X6CNO7WRmKID2YVdRHkIm-1MWX_4NCQQPzMrlN8OsdCF-JaIx4Pt3vXhfbx1qR68ISM9yLTx8-Ud9wy5xtb1DRYRB95IzV5bimJLTEP_9N8Og7rANevX4H29_NKZkCoTA7Qg8jTeVPgK0I6ClQvVbcaxi04kiZ9vwfjsOx3YwWbZsWFLFovQRjnGezXWVPn3BxfRWiHE1sHOM8X6qsEwnWsmdjkyTDxzg8sTjLYs8Bnapw275LhRwCscROeGQ-YmvlzHZ4AqB7JyFRnfH4=",
    # Amigos (para ouvirmos os grupos deles)
    "435374422": "1AZWarzsBu7Rd3aDhcqB9VW1tW9nPh-UVr8HMPCwEKBh_jVQ4wAaYx8xd4ZEltEJTsUJyNWAbPSeT61ZZJGxg6vPBXfXYWaCoylT2rBullBn0ZG1VXofd4jO-tGOPy8LYb9xBvmVkmuPILGN0_ZJsz92is901v2Eys4o5ULHrp2TT9o6jwU1rFKYpv0T6PdptBrwh2XgdViewk1xjMy1bS0GZD8EltJ8FdaTqXj2DXj96TjAa3nWk1ExUKvnaWW81MytyVMjGzsCgYDeU-Z641a3c29L0iFXXjDq4H7m0-Pxy1tJG5CASlnBv4ShOOToc0W4JFTgkKZp6IF9mWGd9hvNSkSr3XYo=",
    "6209300823": "1AZWarzcBu2MRTYFPOYL8dNP86W39b2XnUIZn4VGhnJsFWuNIL1zSUqLAiBb0zq58HGRmuSsRWkrS4apG9bvRP3gsMgHvwfti0Jp4-KA-tVNdot7tLdn20u5tNY2ZVfqki_xG9VpQqgCmjMpV6___rVZLMy_bHR2IN5a8YIP2ApvANw4p_1Dw-o044FdIgREBGSQ6ONURKj45b_8Nm2y0JcRutNCCH94zAILysNxhQlIdCSahNxfiA78-FGr_fvk7WIPfHHDtVmylNUZMUpu-5UlT9OuLHxazyxDyM9uPTmh8cD3CG7JvY44652m-ajPDPlB4d3MfPIC_95uxJIJhoymrfr4HQoE=",
}

# 5) Quais bases cada usuário assina (apenas os canais; o chat vem do LINKS)
SUBS: Dict[str, List[int]] = {
    "786880968": [-2794084735, -2460735067],
    "435374422": [-2855377727],
    "6209300823": [-2468014496, -2770017676],
}

# 6) (Opcional) BOT para /admin_status — se não tiver token, ele não sobe
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# ───────────────────────────── Infra básica ─────────────────────────────
app = Flask("keep_alive")

@app.get("/")
def root():
    return (
        "<h3>Encaminhador (hardcoded)</h3>"
        "<ul>"
        f"<li>DEST_POSTS: {DEST_POSTS}</li>"
        f"<li>DEST_COMMENTS: {DEST_COMMENTS}</li>"
        f"<li>Fixos: {SOURCE_FIXED}</li>"
        f"<li>Links: {LINKS}</li>"
        f"<li>Sessões: {list(SESSIONS.keys())}</li>"
        "</ul>"
    )

@app.get("/health")
def health():
    return jsonify(ok=True)

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)

# ───────────────────────────── Telethon ─────────────────────────────
API_ID = int(os.environ.get("TELEGRAM_API_ID", "0") or 0)
API_HASH = os.environ.get("TELEGRAM_API_HASH", "") or ""

# Conta principal (a sua) — envia e também escuta fixos
ADMIN_UID = 786880968
admin_client = TelegramClient(StringSession(SESSIONS[str(ADMIN_UID)]), API_ID, API_HASH)
SENDER = admin_client  # quem posta nos destinos

# Bot de comando (opcional)
bot: Optional[TelegramClient] = None
if BOT_TOKEN:
    bot = TelegramClient("bot_session", API_ID, API_HASH)

# Clientes dinâmicos
user_clients: Dict[str, TelegramClient] = {}

# ───────────────────────────── Helpers ─────────────────────────────
async def send_with_fallback(dst: int, m: Message):
    """
    Encaminha m para dst. Se forward falhar, baixa e reenvia.
    """
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

    # fallback
    try:
        if m.media:
            path = await m.download_media()
            await SENDER.send_file(dst, path, caption=(m.text or ""))
        else:
            await SENDER.send_message(dst, m.text or "")
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        if m.media:
            path = await m.download_media()
            await SENDER.send_file(dst, path, caption=(m.text or ""))
        else:
            await SENDER.send_message(dst, m.text or "")

def channel_or_chat_dst(chat_id: int) -> int:
    """
    Se vier do canal-base → manda para DEST_POSTS.
    Se vier de um dos chats (megagroup vinculados) → manda para DEST_COMMENTS.
    """
    if chat_id in LINKS.values():
        return DEST_COMMENTS
    return DEST_POSTS

# ───────────────────────────── Listeners ─────────────────────────────
@admin_client.on(events.NewMessage(chats=SOURCE_FIXED))
async def fixed_handler(ev: events.NewMessage.Event):
    try:
        cid = ev.chat_id
        dst = channel_or_chat_dst(cid)
        chat = await admin_client.get_entity(cid)
        title = getattr(chat, "title", None) or str(cid)
        header = f"📢 *{title}* (`{cid}`)"
        await SENDER.send_message(dst, header, parse_mode="Markdown")
        await send_with_fallback(dst, ev.message)
        log.info(f"[fixed] {cid} -> {dst}")
    except Exception as e:
        log.exception(f"[fixed] fail: {e}")

async def ensure_dynamic(uid: str):
    """
    Sobe o client do usuário e escuta os canais + chats vinculados dele.
    """
    if uid in user_clients:
        return user_clients[uid]

    sess = SESSIONS.get(uid)
    if not sess:
        return None

    cli = TelegramClient(StringSession(sess), API_ID, API_HASH)
    await cli.start()
    bases = SUBS.get(uid, [])
    allowed = set(bases)
    # adiciona chats vinculados
    for b in bases:
        linked = LINKS.get(b)
        if linked:
            allowed.add(linked)

    @cli.on(events.NewMessage(chats=list(allowed)))
    async def dyn_handler(ev: events.NewMessage.Event, _uid=uid):
        try:
            cid = ev.chat_id
            dst = channel_or_chat_dst(cid)
            chat = await cli.get_entity(cid)
            title = getattr(chat, "title", None) or str(cid)

            # Cabeçalho com o nome/autor quando for chat (megagroup)
            if cid in LINKS.values():
                sender = await ev.get_sender()
                sname = " ".join(filter(None, [getattr(sender, "first_name", None),
                                               getattr(sender, "last_name", None)])) or (sender.username or "alguém")
                header = f"💬 *{title}* — {sname} (`{cid}`)"
            else:
                header = f"📢 *{title}* (`{cid}`)"

            await SENDER.send_message(dst, header, parse_mode="Markdown")
            await send_with_fallback(dst, ev.message)
            log.info(f"[dyn {_uid}] {cid} -> {dst}")
        except Exception as e:
            log.exception(f"[dyn {uid}] fail: {e}")

    user_clients[uid] = cli
    asyncio.create_task(cli.run_until_disconnected())
    log.info(f"[dyn] ligado uid={uid} allowed={sorted(list(allowed))}")
    return cli

# ───────────────────────────── Bot commands (opcional) ─────────────────────────────
async def setup_bot():
    if not bot:
        return
    @bot.on(events.NewMessage(pattern=r'^/admin_status$'))
    async def _status(ev):
        lines = [
            f"DEST_POSTS: {DEST_POSTS}",
            f"DEST_COMMENTS: {DEST_COMMENTS}",
            f"Fixos: {len(SOURCE_FIXED)}",
            f"Sessoes: {len(SESSIONS)}",
            f"Dinamicos ON: {len(user_clients)}",
        ]
        for uid, cli in user_clients.items():
            lines.append(f"- {uid}")
        await ev.reply("\n".join(lines))

    await bot.start(bot_token=BOT_TOKEN)
    asyncio.create_task(bot.run_until_disconnected())

# ───────────────────────────── MAIN ─────────────────────────────
async def main():
    # Flask
    threading.Thread(target=run_flask, daemon=True).start()

    # Inicia conta principal
    await admin_client.start()

    # Sobe listeners dinâmicos para todos do hardcode
    for uid in list(SESSIONS.keys()):
        try:
            await ensure_dynamic(uid)
        except Exception as e:
            log.exception(f"dyn {uid} fail: {e}")

    # Bot (se existir)
    await setup_bot()

    log.info("🤖 pronto")
    await admin_client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
