# main.py — bot envia tudo (sem forward), 3 sessões escutam, reply robusto + dashboard
import os
import asyncio
import logging
import threading
from typing import Dict, List, Optional, Tuple

from flask import Flask, jsonify, Response
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.types import Message

# ───────────────────────── LOG ─────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("encaminhador")

# ───────────────────── CONFIG FIXA (edite aqui) ─────────────────────
# Destinos (SEU canal e chat de comentários)
DEST_POSTS: int    = -1002897690215      # "Repasse Vips" (canal)
DEST_COMMENTS: int = -1002489338128      # "Repasse Vips Chat" (megagroup/discussion)

# Mapa canal_base → chat_vinculado (somente estes 3, como você pediu)
LINKS: Dict[int, int] = {
    -1002794084735: -1002722732606,  # TRIADE canal → chat
    -1002855377727: -1002813556527,  # LF Tips canal → chat
    -1002468014496: -1002333613791,  # Psico canal → chat
}
BASE_BY_CHAT: Dict[int, int] = {chat: base for base, chat in LINKS.items()}

# Suas três sessões (apenas para OUVIR)
SESSIONS: Dict[str, str] = {
    # SUA (paterra)
    "786880968": "1AZWarzgBu1SEPUzUrUUDXtnGbkyoDEknN2-8Pfcae8iOjJNEQ6qryf1VY47_IYDt0urjMxZCvnQS6X6CNO7WRmKID2YVdRHkIm-1MWX_4NCQQPzMrlN8OsdCF-JaIx4Pt3vXhfbx1qR68ISM9yLTx8-Ud9wy5xtb1DRYRB95IzV5bimJLTEP_9N8Og7rANevX4H29_NKZkCoTA7Qg8jTeVPgK0I6ClQvVbcaxi04kiZ9vwfjsOx3YwWbZsWFLFovQRjnGezXWVPn3BxfRWiHE1sHOM8X6qsEwnWsmdjkyTDxzg8sTjLYs8Bnapw275LhRwCscROeGQ-YmvlzHZ4AqB7JyFRnfH4=",
    # LF Tips
    "435374422": "1AZWarzsBu7Rd3aDhcqB9VW1tW9nPh-UVr8HMPCwEKBh_jVQ4wAaYx8xd4ZEltEJTsUJyNWAbPSeT61ZZJGxg6vPBXfXYWaCoylT2rBullBn0ZG1VXofd4jO-tGOPy8LYb9xBvmVkmuPILGN0_ZJsz92is901v2Eys4o5ULHrp2TT9o6jwU1rFKYpv0T6PdptBrwh2XgdViewk1xjMy1bS0GZD8EltJ8FdaTqXj2DXj96TjAa3nWk1ExUKvnaWW81MytyVMjGzsCgYDeU-Z641a3c29L0iFXXjDq4H7m0-Pxy1tJG5CASlnBv4ShOOToc0W4JFTgkKZp6IF9mWGd9hvNSkSr3XYo=",
    # Psico
    "6209300823": "1AZWarzcBu2MRTYFPOYL8dNP86W39b2XnUIZn4VGhnJsFWuNIL1zSUqLAiBb0zq58HGRmuSsRWkrS4apG9bvRP3gsMgHvwfti0Jp4-KA-tVNdot7tLdn20u5tNY2ZVfqki_xG9VpQqgCmjMpV6___rVZLMy_bHR2IN5a8YIP2ApvANw4p_1Dw-o044FdIgREBGSQ6ONURKj45b_8Nm2y0JcRutNCCH94zAILysNxhQlIdCSahNxfiA78-FGr_fvk7WIPfHHDtVmylNUZMUpu-5UlT9OuLHxazyxDyM9uPTmh8cD3CG7JvY44652m-ajPDPlB4d3MfPIC_95uxJIJhoymrfr4HQoE=",
}

# Quais canais-base cada usuário escuta (o chat será inferido via LINKS)
SUBS: Dict[str, List[int]] = {
    "786880968": [-1002794084735],
    "435374422": [-1002855377727],
    "6209300823": [-1002468014496],
}

# ───────────────────── ENV (bot + api) ─────────────────────
API_ID  = int(os.environ.get("TELEGRAM_API_ID", "0") or 0)
API_HASH = os.environ.get("TELEGRAM_API_HASH", "") or ""
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

if not (API_ID and API_HASH and BOT_TOKEN):
    raise SystemExit("Defina TELEGRAM_API_ID, TELEGRAM_API_HASH e BOT_TOKEN no Railway.")

# ───────────────────── Flask (dashboard simples) ─────────────────────
app = Flask("keep_alive")

@app.get("/health")
def _health():  # ping rápido
    return jsonify(ok=True)

@app.get("/dash")
def _dash():
    rows = []
    rows.append("<h3>Encaminhador — Dashboard</h3>")
    rows.append(f"<p>DEST_POSTS: {DEST_POSTS} | DEST_COMMENTS: {DEST_COMMENTS}</p>")
    rows.append(f"<p>Links (base→chat): {len(LINKS)} pares</p>")
    rows.append("<ul>")
    for b, c in LINKS.items():
        rows.append(f"<li>{b} → {c}</li>")
    rows.append("</ul>")
    rows.append(f"<p>Sessões ativas (escuta): {len(user_clients)}</p>")
    rows.append("<ul>")
    for uid in user_clients.keys():
        rows.append(f"<li>{uid}</li>")
    rows.append("</ul>")
    rows.append(f"<p>Âncoras mapeadas: {len(post_map)}</p>")
    return Response("\n".join(rows), mimetype="text/html")

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)

# ───────────────────── Telethon (bot + ouvintes) ─────────────────────
# bot para ENVIAR
poster_bot = TelegramClient("poster_bot", API_ID, API_HASH)

# ouvintes por usuário
user_clients: Dict[str, TelegramClient] = {}
allowed_map: Dict[str, set] = {}   # uid -> {ids permitidos}

# mapeamento para replies: (canal_base, id_original) -> id_copiado_no_DEST_POSTS
post_map: Dict[Tuple[int, int], int] = {}

# ───────────────────── Helpers de cópia / reply ─────────────────────
async def copy_message_no_forward(dst: int, m: Message, reply_to: Optional[int] = None) -> Optional[Message]:
    """
    Copia SEM forward. Retorna a mensagem criada (para pegar .id).
    """
    try:
        if m.media:
            path = await m.download_media()
            return await poster_bot.send_file(dst, path, caption=(m.text or ""), reply_to=reply_to)
        else:
            return await poster_bot.send_message(dst, m.text or "", reply_to=reply_to)
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        try:
            if m.media:
                path = await m.download_media()
                return await poster_bot.send_file(dst, path, caption=(m.text or ""), reply_to=reply_to)
            else:
                return await poster_bot.send_message(dst, m.text or "", reply_to=reply_to)
        except Exception:
            return None
    except Exception:
        return None


def remember_anchor(base_id: int, src_msg_id: int, dest_msg_id: int) -> None:
    if base_id and src_msg_id and dest_msg_id:
        post_map[(base_id, src_msg_id)] = dest_msg_id


def top_id_from_comment(msg: Message) -> Optional[int]:
    """
    Extrai o id do post de canal ao qual este comentário pertence.
    """
    rt = getattr(msg, "reply_to", None)
    if not rt:
        return getattr(msg, "reply_to_top_id", None) or getattr(msg, "reply_to_msg_id", None)
    for attr in ("reply_to_top_id", "top_msg_id", "reply_to_msg_id"):
        val = getattr(rt, attr, None)
        if isinstance(val, int) and val > 0:
            return val
    return None


async def get_or_make_anchor(listener: TelegramClient, base_id: int, top_id: int, base_title: str) -> Optional[int]:
    """
    Retorna o id do âncora (cópia do post no DEST_POSTS).
    Se ainda não existir, cria na hora copiando o post original.
    """
    key = (base_id, top_id)
    if key in post_map:
        return post_map[key]

    # dá alguns ticks para outra corrotina gravar
    for _ in range(3):
        await asyncio.sleep(0.4)
        if key in post_map:
            return post_map[key]

    # cria âncora agora
    try:
        orig = await listener.get_messages(base_id, ids=top_id)
        if not orig:
            return None

        header = f"📢 *{base_title}* (`{base_id}`)"
        try:
            await poster_bot.send_message(DEST_POSTS, header, parse_mode="Markdown")
        except Exception:
            pass

        copied = await copy_message_no_forward(DEST_POSTS, orig, reply_to=None)
        if copied:
            remember_anchor(base_id, top_id, copied.id)
            return copied.id
    except Exception as e:
        log.debug(f"[anchor] create failed base={base_id} top={top_id}: {type(e).__name__}")
    return None

# ───────────────────── Ouvinte dinâmico (por usuário) ─────────────────────
async def ensure_dynamic(uid: str) -> Optional[TelegramClient]:
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
    allowed_map[uid] = allowed

    @cli.on(events.NewMessage)
    async def _listener(ev: events.NewMessage.Event, _uid=uid):
        try:
            cid = ev.chat_id
            if cid not in allowed_map.get(_uid, set()):
                return

            # se for canal base -> copiar para DEST_POSTS e lembrar âncora
            if cid in LINKS.keys():
                base_id = cid
                ent = await cli.get_entity(base_id)
                base_title = getattr(ent, "title", None) or str(base_id)

                header = f"📢 *{base_title}* (`{base_id}`)"
                try:
                    await poster_bot.send_message(DEST_POSTS, header, parse_mode="Markdown")
                except Exception:
                    pass

                copied = await copy_message_no_forward(DEST_POSTS, ev.message, reply_to=None)
                if copied:
                    remember_anchor(base_id, ev.message.id, copied.id)
                log.info(f"[post {_uid}] base={base_id} -> anchor={copied.id if copied else None}")
                return

            # se for chat vinculado -> comentário
            if cid in LINKS.values():
                base_id = BASE_BY_CHAT.get(cid)
                if not base_id:
                    return

                # título para header do comentário
                chat_ent = await cli.get_entity(cid)
                chat_title = getattr(chat_ent, "title", None) or str(cid)

                # autor
                try:
                    sender = await ev.get_sender()
                    sname = " ".join(filter(None, [
                        getattr(sender, "first_name", None),
                        getattr(sender, "last_name", None),
                    ])) or (sender.username or "alguém")
                except Exception:
                    sname = "alguém"

                # localizar/gerar âncora
                top_id = top_id_from_comment(ev.message)
                anchor_id = None
                if top_id:
                    # precisamos do título do CANAL BASE para criar header se precisar criar âncora
                    base_ent = await cli.get_entity(base_id)
                    base_title = getattr(base_ent, "title", None) or str(base_id)
                    anchor_id = post_map.get((base_id, top_id))
                    if not anchor_id:
                        anchor_id = await get_or_make_anchor(cli, base_id, top_id, base_title)

                header = f"💬 *{chat_title}* — {sname} (`{cid}`)"
                try:
                    await poster_bot.send_message(DEST_COMMENTS, header, parse_mode="Markdown", reply_to=anchor_id)
                except Exception:
                    pass

                # copia o comentário (preferindo reply ao âncora quando houver)
                await copy_message_no_forward(DEST_COMMENTS, ev.message, reply_to=anchor_id)
                log.info(f"[chat {_uid}] chat={cid} -> reply={anchor_id}")
        except Exception as e:
            log.exception(f"[dyn {_uid}] fail: {e}")

    user_clients[uid] = cli
    asyncio.create_task(cli.run_until_disconnected())
    log.info(f"[dyn] ligado uid={uid} allowed={sorted(list(allowed))}")
    return cli

# ───────────────────── Comandos do bot (admin) ─────────────────────
def is_admin(uid: int) -> bool:
    # você é o admin; se quiser, acrescente mais IDs por vírgula via env ADMIN_IDS
    if uid == 786880968:
        return True
    try:
        ids = os.environ.get("ADMIN_IDS", "")
        if ids.strip():
            arr = [int(x) for x in ids.replace(",", " ").split() if x.strip().lstrip("-").isdigit()]
            return uid in arr
    except Exception:
        pass
    return False

async def setup_bot_commands():
    @poster_bot.on(events.NewMessage(pattern=r"^/start$"))
    async def _start(ev):
        await ev.reply(
            "👋 Encaminhador online.\n\n"
            "• `/admin_status` — mostra status e tamanhos\n"
            "• Dashboard: abra `/dash` na web do Railway\n"
            "• `/listgroups UID [página] [tamanho]` — lista diálogos da sessão\n"
            "• `/subscribe UID BASE_ID` — assina um canal-base para UID\n"
            "• `/linkchat BASE_ID CHAT_ID` — define/atualiza chat vinculado\n"
            "• `/debug_allowed UID` — mostra quais ids o UID está ouvindo",
            parse_mode="Markdown",
        )

    @poster_bot.on(events.NewMessage(pattern=r"^/admin_status$"))
    async def _status(ev):
        if not is_admin(ev.sender_id):
            return await ev.reply("🚫")
        lines = [
            f"DEST_POSTS: {DEST_POSTS}",
            f"DEST_COMMENTS: {DEST_COMMENTS}",
            f"Sessões: {len(SESSIONS)} | Dinâmicos ON: {len(user_clients)}",
            f"Links: {len(LINKS)} pares | POSTMAP: {len(post_map)}",
        ]
        for uid in user_clients.keys():
            lines.append(f"- {uid}")
        await ev.reply("\n".join(lines))

    @poster_bot.on(events.NewMessage(pattern=r"^/listgroups(\s+.+)?$"))
    async def _listgroups(ev):
        if not is_admin(ev.sender_id):
            return
        try:
            parts = (ev.raw_text or "").split()
            if len(parts) < 2:
                return await ev.reply("Uso: `/listgroups UID [página] [tamanho]`", parse_mode="Markdown")
            uid = parts[1].strip()
            page = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 1
            size = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 50
            page = max(1, page); size = min(100, max(5, size))

            sess = SESSIONS.get(uid)
            if not sess:
                return await ev.reply("UID sem session.")
            tmp = TelegramClient(StringSession(sess), API_ID, API_HASH)
            await tmp.start()
            want = page * size
            lines, grabbed = [], 0
            async for d in tmp.iter_dialogs(limit=3000):
                ent = d.entity
                cid = getattr(ent, 'id', None)
                if getattr(ent, 'megagroup', False) or getattr(ent, 'broadcast', False):
                    title = getattr(ent, 'title', None) or getattr(ent, 'username', None) or str(cid)
                    grabbed += 1
                    if grabbed > (page - 1) * size and len(lines) < size:
                        lines.append(f"- `{cid}` — {title}")
                    if grabbed >= want:
                        break
            await tmp.disconnect()
            if not lines:
                return await ev.reply(f"Página {page} vazia.")
            await ev.reply("📋 *Grupos/Channels:*\n" + "\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await ev.reply(f"erro: {type(e).__name__}")

    @poster_bot.on(events.NewMessage(pattern=r"^/subscribe\s+(\d+)\s+(-?\d+)$"))
    async def _subscribe(ev):
        if not is_admin(ev.sender_id):
            return
        uid, base = ev.pattern_match.group(1), int(ev.pattern_match.group(2))
        arr = SUBS.setdefault(uid, [])
        if base not in arr:
            arr.append(base)
        # atualizar allowed em runtime
        for u in [uid]:
            if u in allowed_map:
                allowed_map[u].add(base)
                chat = LINKS.get(base)
                if chat:
                    allowed_map[u].add(chat)
        await ensure_dynamic(uid)
        await ev.reply(f"✅ {uid} assina {base}.")

    @poster_bot.on(events.NewMessage(pattern=r"^/linkchat\s+(-?\d+)\s+(-?\d+)$"))
    async def _linkchat(ev):
        if not is_admin(ev.sender_id):
            return
        base = int(ev.pattern_match.group(1))
        chat = int(ev.pattern_match.group(2))
        LINKS[base] = chat
        BASE_BY_CHAT[chat] = base
        # propagar aos allowed existentes
        for uid, allowed in allowed_map.items():
            if base in SUBS.get(uid, []):
                allowed.add(chat)
        await ev.reply(f"🔗 {base} → {chat} vinculado.")

    @poster_bot.on(events.NewMessage(pattern=r"^/debug_allowed\s+(\d+)$"))
    async def _dbg(ev):
        if not is_admin(ev.sender_id):
            return
        uid = ev.pattern_match.group(1)
        ids = sorted(list(allowed_map.get(uid, set())))
        await ev.reply("Allowed:\n" + "\n".join(map(str, ids)) if ids else "(vazio)")

# ───────────────────── MAIN ─────────────────────
async def main():
    # dashboard
    threading.Thread(target=run_flask, daemon=True).start()

    # inicia o BOT (quem envia)
    await poster_bot.start(bot_token=BOT_TOKEN)
    await setup_bot_commands()

    # inicia ouvintes (três sessões)
    for uid in list(SESSIONS.keys()):
        try:
            await ensure_dynamic(uid)
        except Exception as e:
            log.exception(f"dyn {uid} fail: {e}")

    log.info("🤖 pronto (TRIADE, LF Tips, Psico) — envio via BOT, replies com fallback")
    await poster_bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
