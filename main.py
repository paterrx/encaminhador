# main.py
import os, json, asyncio, logging
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# — LOGGING —---------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("encaminhador")

# — CONFIGURAÇÃO —----------------------------
API_ID        = int(os.environ["TELEGRAM_API_ID"])
API_HASH      = os.environ["TELEGRAM_API_HASH"]
BOT_TOKEN     = os.environ["BOT_TOKEN"]
DEST_CHAT_ID  = int(os.environ["DEST_CHAT_ID"])
SESSION_STR   = os.environ["SESSION_STRING"]
SOURCE_CHAT_IDS = json.loads(os.environ.get("SOURCE_CHAT_IDS","[]"))

# — DIRETÓRIOS e ARQUIVOS —-------------------
DATA_DIR   = "/data"
CHAN_FILE  = os.path.join(DATA_DIR, "channels.json")
SUBS_FILE  = os.path.join(DATA_DIR, "subscriptions.json")
AUDIT_FILE = os.path.join(DATA_DIR, "audit.json")

def ensure_json(path, default):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=2)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# Inicializa ou carrega os JSONs
channels_map  = ensure_json(CHAN_FILE,  {str(cid): cid for cid in SOURCE_CHAT_IDS})
subscriptions = ensure_json(SUBS_FILE,  {})
audit_log     = ensure_json(AUDIT_FILE, [])

def save_subs():
    save_json(SUBS_FILE, subscriptions)

def log_audit(chat_id, status):
    evt = {
        "time": datetime.utcnow().isoformat(),
        "chat_id": chat_id,
        "status": status
    }
    audit_log.append(evt)
    save_json(AUDIT_FILE, audit_log)

# — CLIENT ÚNICO —----------------------------
client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# — HANDLERS —-------------------------------
@client.on(events.NewMessage(chats=list(channels_map.values())))
async def on_new_message(ev):
    cid = ev.chat_id
    log.info(f"🔍 Nova mensagem em {cid}")
    # 1) tentar forward
    try:
        await ev.message.forward_to(DEST_CHAT_ID)
        log_audit(cid, "forwarded")
        return
    except Exception:
        pass

    # 2) tentar baixar a mídia + reenviar
    try:
        path = await ev.message.download_media()
        await client.send_file(DEST_CHAT_ID, path, caption=ev.message.text or "")
        log_audit(cid, "media_downloaded")
        return
    except Exception:
        pass

    # 3) texto puro
    try:
        await client.send_message(DEST_CHAT_ID, ev.message.text or "")
        log_audit(cid, "text_only")
        return
    except Exception:
        pass

    # 4) falha total
    err = (
        f"❌ Falha total ao encaminhar de {cid} "
        f"às {datetime.utcnow().isoformat()}"
    )
    await client.send_message(DEST_CHAT_ID, err)
    log_audit(cid, "failed_all")

@client.on(events.NewMessage(pattern=r'^/(start|help)$'))
async def on_help(ev):
    texto = (
        "👋 *Bem-vindo ao Encaminhador!*\n\n"
        "📋 **Comandos disponíveis:**\n"
        "`/listgroups` — listar seus grupos ativos\n"
        "`/subscribe GROUP_ID` — assinar um grupo\n"
        "`/unsubscribe GROUP_ID` — cancelar assinatura\n"
    )
    await ev.reply(texto, parse_mode="Markdown")

@client.on(events.NewMessage(pattern=r'^/listgroups$'))
async def on_list(ev):
    uid = str(ev.sender_id)
    grps = subscriptions.get(uid, [])
    body = "📋 *Seus grupos:*\n"
    body += ("\n".join(f"`{g}`" for g in grps)) if grps else "_nenhum_"
    await ev.reply(body, parse_mode="Markdown")

@client.on(events.NewMessage(pattern=r'^/subscribe ([-\d]+)$'))
async def on_subscribe(ev):
    uid = str(ev.sender_id)
    gid = ev.pattern_match.group(1)
    lst = subscriptions.setdefault(uid, [])
    if gid in lst:
        await ev.reply(f"⚠️ Você já está inscrito em `{gid}`.", parse_mode="Markdown")
        return
    lst.append(gid)
    save_subs()
    await ev.reply(f"✅ Inscrito em `{gid}`.", parse_mode="Markdown")

@client.on(events.NewMessage(pattern=r'^/unsubscribe ([-\d]+)$'))
async def on_unsub(ev):
    uid = str(ev.sender_id)
    gid = ev.pattern_match.group(1)
    lst = subscriptions.get(uid, [])
    if gid not in lst:
        await ev.reply(f"❌ Você não estava inscrito em `{gid}`.", parse_mode="Markdown")
        return
    lst.remove(gid)
    if not lst: subscriptions.pop(uid)
    save_subs()
    await ev.reply(f"✅ Cancelada inscrição em `{gid}`.", parse_mode="Markdown")

# — EXECUÇÃO —------------------------------
async def main():
    log.info("🤖 Bot iniciado!")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
