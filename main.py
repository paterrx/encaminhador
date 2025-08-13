# main.py — v2, com comentários em posts e cópia forçada
import os
import asyncio
import logging
import threading
from typing import Dict, List, Optional, Tuple

from flask import Flask, Response
import html as html_std
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.types import Message

# ───────────────────────────── LOG ─────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("encaminhador")

# ───────────────────────────── HARD-CODE CONFIG ─────────────────────────────
# Destinos
DEST_POSTS: int = -1002650810578      # <<<<<< ALTERADO PARA O SEU CANAL DE POSTS (Repasse Vips)
DEST_COMMENTS: int = -1002851540794   # <<<<<< ALTERADO PARA O SEU CANAL DE CHAT (Repasse Vips Chat)

# Pares base→chat (o que já sabemos)
LINKS: Dict[int, int] = {
    -1002794084735: -1002722732606,  # TRIADE
    -1002855377727: -1002813556527,  # LF Tips
    -1002468014496: -1002333613791,  # Psico
}
# Adicionando os canais de teste do seu screenshot para funcionar
LINKS[DEST_POSTS] = DEST_COMMENTS

# Strings de sessão (donos)
SESSIONS: Dict[str, str] = {
    "786880968": "1AZWarzgBu1SEPUzUrUUDXtnGbkyoDEknN2-8Pfcae8iOjJNEQ6qryf1VY47_IYDt0urjMxZCvnQS6X6CNO7WRmKID2YVdRHkIm-1MWX_4NCQQPzMrlN8OsdCF-JaIx4Pt3vXhfbx1qR68ISM9yLTx8-Ud9wy5xtb1DRYRB95IzV5bimJLTEP_9N8Og7rANevX4H29_NKZkCoTA7Qg8jTeVPgK0I6ClQvVbcaxi04kiZ9vwfjsOx3YwWbZsWFLFovQRjnGezXWVPn3BxfRWiHE1sHOM8X6qsEwnWsmdjkyTDxzg8sTjLYs8Bnapw275LhRwCscROeGQ-YmvlzHZ4AqB7JyFRnfH4=",
    "435374422": "1AZWarzsBu7Rd3aDhcqB9VW1tW9nPh-UVr8HMPCwEKBh_jVQ4wAaYx8xd4ZEltEJTsUJyNWAbPSeT61ZZJGxg6vPBXfXYWaCoylT2rBullBn0ZG1VXofd4jO-tGOPy8LYb9xBvmVkmuPILGN0_ZJsz92is901v2Eys4o5ULHrp2TT9o6jwU1rFKYpv0T6PdptBrwh2XgdViewk1xjMy1bS0GZD8EltJ8FdaTqXj2DXj96TjAa3nWk1ExUKvnaWW81MytyVMjGzsCgYDeU-Z641a3c29L0iFXXjDq4H7m0-Pxy1tJG5CASlnBv4ShOOToc0W4JFTgkKZp6IF9mWGd9hvNSkSr3XYo=",
    "6209300823": "1AZWarzcBu2MRTYFPOYL8dNP86W39b2XnUIZn4VGhnJsFWuNIL1zSUqLAiBb0zq58HGRmuSsRWkrS4apG9bvRP3gsMgHvwfti0Jp4-KA-tVNdot7tLdn20u5tNY2ZVfqki_xG9VpQqgCmjMpV6___rVZLMy_bHR2IN5a8YIP2ApvANw4p_1Dw-o044FdIgREBGSQ6ONURKj45b_8Nm2y0JcRutNCCH94zAILysNxhQlIdCSahNxfiA78-FGr_fvk7WIPfHHDtVmylNUZMUpu-5UlT9OuLHxazyxDyM9uPTmh8cD3CG7JvY44652m-ajPDPlB4d3MfPIC_95uxJIJhoymrfr4HQoE=",
}

# Assinaturas por dono (apenas base; o chat vem de LINKS)
SUBS: Dict[str, List[int]] = {
    "786880968": [-1002794084735, -1002855377727, -1002468014496, DEST_POSTS], # Adicionado canal de teste
    "435374422": [-1002855377727],
    "6209300823": [-1002468014496],
}

# API e Bot
API_ID = int(os.environ.get("TELEGRAM_API_ID", "2178028") or 0)
API_HASH = os.environ.get("TELEGRAM_API_HASH", "b93c4731a5a12a524c52019702a46675") or ""
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# ##################################################################
# ##                 NOVAS ESTRUTURAS DE DADOS                    ##
# ##################################################################
# Mapa reverso para encontrar o canal base a partir do chat
REVERSE_LINKS: Dict[int, int] = {v: k for k, v in LINKS.items()}
# Mapa para armazenar a relação: (ID_canal_origem, ID_msg_origem) -> ID_msg_destino
MESSAGE_MAP: Dict[Tuple[int, int], int] = {}


# ───────────────────────────── Infra web ─────────────────────────────
app = Flask("keep_alive")

@app.get("/")
def root() -> str:
    return (
        "<h3>Encaminhador v2 (comentários)</h3>"
        "<ul>"
        f"<li>DEST_POSTS: {DEST_POSTS}</li>"
        f"<li>Sessões: {list(SESSIONS.keys())}</li>"
        f"<li>Links (pares): {len(LINKS)}</li>"
        f"<li>Mensagens Mapeadas: {len(MESSAGE_MAP)}</li>"
        "</ul>"
    )

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)

# ##################################################################
# ##               LÓGICA DE ENVIO MODIFICADA                     ##
# ##################################################################

bot_client: Optional[TelegramClient] = None
user_clients: Dict[str, TelegramClient] = {}
user_handlers: Dict[str, Tuple] = {}

def is_chat_id(chat_id: int) -> bool:
    return chat_id in LINKS.values()

async def copy_message(
    dst: int,
    m: Message,
    reply_to: Optional[int] = None,
    caption_prefix: str = ""
) -> Optional[Message]:
    """
    Copia uma mensagem (baixa e envia), nunca encaminha.
    Retorna a nova mensagem enviada para obter seu ID.
    """
    if not m:
        return None
    
    final_caption = f"{caption_prefix}{m.text or ''}"

    try:
        new_message = None
        if m.media:
            path = await m.download_media()
            new_message = await bot_client.send_file(
                dst, path, caption=final_caption, reply_to=reply_to, parse_mode="Markdown"
            )
            os.remove(path) # Limpa o arquivo baixado
        else:
            new_message = await bot_client.send_message(
                dst, final_caption, reply_to=reply_to, parse_mode="Markdown"
            )
        return new_message
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 2)
        return await copy_message(dst, m, reply_to, caption_prefix) # Tenta novamente
    except Exception as e:
        log.error(f"Falha ao copiar mensagem {m.id} para {dst}: {e}")
        return None

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
        cli = TelegramClient(StringSession(sess), API_ID, API_HASH, base_logger=log)
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
                source_cid = ev.chat_id

                # LÓGICA 1: A mensagem veio de um CHAT (é um comentário)
                if is_chat_id(source_cid):
                    if not ev.message.is_reply:
                        log.info(f"Ignorando msg no chat {source_cid} que não é reply.")
                        return

                    base_cid = REVERSE_LINKS.get(source_cid)
                    reply_to_id = ev.message.reply_to_msg_id
                    
                    if not base_cid:
                        log.warning(f"Não achei canal base para o chat {source_cid}")
                        return

                    target_post_id = MESSAGE_MAP.get((base_cid, reply_to_id))
                    if not target_post_id:
                        log.warning(f"Não achei post de destino para a msg {reply_to_id} do canal {base_cid}")
                        return

                    # Prepara o comentário
                    sender = await ev.get_sender()
                    sname = " ".join(filter(None, [
                        getattr(sender, "first_name", None),
                        getattr(sender, "last_name", None),
                    ])) or (getattr(sender, "username", "alguém"))
                    
                    prefix = f"**{sname}**:\n"
                    
                    await copy_message(
                        DEST_POSTS,
                        ev.message,
                        reply_to=target_post_id,
                        caption_prefix=prefix
                    )
                    log.info(f"Comentário de {sname} (chat {source_cid}) adicionado ao post {target_post_id}")

                # LÓGICA 2: A mensagem veio de um canal BASE (é um post novo)
                else:
                    ent = await cli.get_entity(source_cid)
                    title = getattr(ent, "title", str(source_cid))
                    header = f"📢 *{title}* (`{source_cid}`)"
                    
                    # Envia o cabeçalho como uma mensagem separada
                    await bot_client.send_message(DEST_POSTS, header, parse_mode="Markdown")
                    
                    # Copia a mensagem principal
                    sent_post = await copy_message(DEST_POSTS, ev.message)

                    # Salva o mapeamento se o post foi enviado com sucesso
                    if sent_post:
                        MESSAGE_MAP[(source_cid, ev.message.id)] = sent_post.id
                        log.info(f"Post do canal {source_cid} (msg {ev.message.id}) mapeado para o post {sent_post.id}")

            except Exception as e:
                log.exception(f"[dyn {_uid}] falha geral: {e}")

        cli.add_event_handler(_cb, evb)
        user_handlers[uid] = (_cb, evb)
        log.info(f"[dyn] ligado uid={uid} allowed={chats_list}")
    return cli

# ───────────────────────────── Bot: comandos (sem alterações) ─────────────────────────────
# ... (a seção de comandos do bot permanece a mesma)

# ───────────────────────────── MAIN ─────────────────────────────
async def main():
    # web
    threading.Thread(target=run_flask, daemon=True).start()

    # BOT primeiro
    global bot_client
    bot_client = TelegramClient("admin_bot_session", API_ID, API_HASH)
    await bot_client.start(bot_token=BOT_TOKEN)
    # Aqui iriam os @bot_client.on(...) se você os movesse para uma função

    # inicia dinâmicos
    for uid in list(SESSIONS.keys()):
        try:
            await ensure_dynamic(uid, force=True)
        except Exception as e:
            log.exception(f"dyn {uid} fail on start: {e}")

    log.info("🤖 pronto v2 (comentários em posts)")
    await bot_client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())