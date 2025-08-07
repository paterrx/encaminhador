import os, json, asyncio, logging
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# — LOGGING —
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("encaminhador")

# — CONFIGURAÇÃO —
API_ID        = int(os.getenv("TELEGRAM_API_ID"))
API_HASH      = os.getenv("TELEGRAM_API_HASH")
BOT_TOKEN     = os.getenv("BOT_TOKEN")
DEST_CHAT_ID  = int(os.getenv("DEST_CHAT_ID"))
SESSION       = os.getenv("SESSION_STRING")
SOURCE_IDS    = json.loads(os.getenv("SOURCE_CHAT_IDS","[]"))

# — ARQUIVOS e DIRETÓRIOS —
DATA_DIR   = "/data"
CHAN_FILE  = os.path.join(DATA_DIR, "channels.json")
SUBS_FILE  = os.path.join(DATA_DIR, "subscriptions.json")
AUDIT_FILE = os.path.join(DATA_DIR, "audit.json")

def ensure_file(path, default):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=2)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# Inicializa JSONs
channels_map  = ensure_file(CHAN_FILE, {str(cid): cid for cid in SOURCE_IDS})
subscriptions = ensure_file(SUBS_FILE, {})
audit_events  = ensure_file(AUDIT_FILE, [])

def save_subs():
    save_file(SUBS_FILE, subscriptions)

def log_audit(chat_id, status):
    evt = {"time": datetime.utcnow().isoformat(), "chat_id": chat_id, "status": status}
    audit_events.append(evt)
    save_file(AUDIT_FILE, audit_events)

# — CLIENTS —
admin_client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
bot_client   = TelegramClient(StringSession(SESSION), API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# — HANDLERS —
@bot_client.on(events.NewMessage(chats=list(channels_map.values())))
async def forward_handler(ev):
    cid = ev.chat_id
    log.info(f"🔍 Mensagem em {cid}")
    # 1) Forward
    try:
        await ev.message.forward_to(DEST_CHAT_ID)
        log_audit(cid, "forwarded")
        return
    except:
        pass
    # 2) download + send
    try:
        path = await ev.message.download_media()
        await admin_client.send_file(DEST_CHAT_ID, path, caption=ev.message.text or "")
        log_audit(cid, "downloaded")
        return
    except:
        pass
    # 3) só texto
    try:
        await admin_client.send_message(DEST_CHAT_ID, ev.message.text or "")
        log_audit(cid, "text-only")
        return
    except:
        pass
    # 4) falha total
    err = f"❌ Falha total de encaminhamento de {cid} em {datetime.utcnow().isoformat()}"
    await admin_client.send_message(DEST_CHAT_ID, err)
    log_audit(cid, "failed-all")

@bot_client.on(events.NewMessage(pattern=r'/listgroups'))
async def listgroups(ev):
    uid = str(ev.sender_id)
    groups = subscriptions.get(uid, [])
    text = "📋 *Seus grupos:*\n" + ("\n".join(f"`{g}`" for g in groups) if groups else "_nenhum_")
    await ev.reply(text, parse_mode="Markdown")

@bot_client.on(events.NewMessage(pattern=r'/subscribe ([-\d]+)'))
async def subscribe(ev):
    uid = str(ev.sender_id); gid = ev.pattern_match.group(1)
    lst = subscriptions.setdefault(uid, [])
    if gid in lst:
        await ev.reply(f"⚠️ `{gid}` já inscrito.", parse_mode="Markdown"); return
    lst.append(gid); save_subs()
    await ev.reply(f"✅ `{uid}` inscrito em `{gid}`.", parse_mode="Markdown")

@bot_client.on(events.NewMessage(pattern=r'/unsubscribe ([-\d]+)'))
async def unsubscribe(ev):
    uid = str(ev.sender_id); gid = ev.pattern_match.group(1)
    lst = subscriptions.get(uid, [])
    if gid not in lst:
        await ev.reply(f"❌ Você não estava inscrito em `{gid}`.", parse_mode="Markdown"); return
    lst.remove(gid)
    if not lst: subscriptions.pop(uid)
    save_subs()
    await ev.reply(f"✅ `{uid}` cancelou `{gid}`.", parse_mode="Markdown")

@bot_client.on(events.NewMessage(pattern=r'/start|/help'))
async def send_help(ev):
    text = (
        "👋 *Bem-vindo ao Encaminhador!*\n\n"
        "💡 Comandos:\n"
        "`/listgroups` — listar seus grupos\n"
        "`/subscribe GROUP_ID` — assinar um grupo\n"
        "`/unsubscribe GROUP_ID` — cancelar assinatura\n"
    )
    await ev.reply(text, parse_mode="Markdown")

# — MAIN —
async def main():
    await admin_client.start()
    log.info("🤖 Bots rodando...")
    await bot_client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
