import os, json, asyncio, logging
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("encaminhador")

# --- CONFIGURAÇÃO / ARQUIVOS ---
API_ID        = int(os.environ["TELEGRAM_API_ID"])
API_HASH      = os.environ["TELEGRAM_API_HASH"]
DEST_CHAT_ID  = int(os.environ["DEST_CHAT_ID"])
BOT_TOKEN     = os.environ["BOT_TOKEN"]
SESSION       = os.environ["SESSION_STRING"]  # admin session
SOURCE_IDS    = json.loads(os.environ.get("SOURCE_CHAT_IDS","[]"))

DATA_DIR      = "/data"
CHAN_FILE     = os.path.join(DATA_DIR, "channels.json")
SUBS_FILE     = os.path.join(DATA_DIR, "subscriptions.json")
AUDIT_FILE    = os.path.join(DATA_DIR, "audit.json")

# --- GARANTE ARQUIVOS INICIAIS ---
def ensure(path, default):
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=2)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

channels_map  = ensure(CHAN_FILE, {str(cid): cid for cid in SOURCE_IDS})
subscriptions = ensure(SUBS_FILE, {})   # { uid: [chat_id,...] }
audit_events  = ensure(AUDIT_FILE, [])  # list of {time,chat_id,status}

def save_subs():
    with open(SUBS_FILE,"w",encoding="utf-8") as f:
        json.dump(subscriptions, f, indent=2)

def log_audit(chat_id, status):
    evt = {"time": datetime.utcnow().isoformat(), "chat_id": chat_id, "status": status}
    audit_events.append(evt)
    with open(AUDIT_FILE,"w",encoding="utf-8") as f:
        json.dump(audit_events, f, indent=2)

# --- CLIENTS ---
admin_client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
bot_client   = None

async def start_clients():
    await admin_client.start()
    global bot_client
    bot_client = TelegramClient(StringSession(SESSION), API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# --- HANDLERS ---
@bot_client.on(events.NewMessage(chats=list(channels_map.values())))
async def forward_handler(ev):
    cid = ev.chat_id
    log.info(f"🔍 Mensagem em {cid}, SOURCE={list(channels_map.values())}")
    # 1) Forward direto
    try:
        await ev.message.forward_to(DEST_CHAT_ID)
        log_audit(cid, "forwarded")
        return
    except:
        pass

    # 2) Download + send_file
    try:
        path = await ev.message.download_media()
        await admin_client.send_file(DEST_CHAT_ID, path, caption=ev.message.text or "")
        log_audit(cid, "downloaded & sent")
        return
    except:
        pass

    # 3) Texto puro
    try:
        await admin_client.send_message(DEST_CHAT_ID, ev.message.text or "")
        log_audit(cid, "text-only")
        return
    except:
        pass

    # 4) Tudo falhou
    err_msg = f"❌ Falha total ao encaminhar de {cid} em {datetime.utcnow().isoformat()}"
    await admin_client.send_message(DEST_CHAT_ID, err_msg)
    log_audit(cid, "failed-all")

@bot_client.on(events.NewMessage(pattern=r'/listgroups'))
async def listgroups(ev):
    uid = str(ev.sender_id)
    groups = subscriptions.get(uid, [])
    text = "📋 *Seus grupos:*\n" + ("\n".join(f"`{g}`" for g in groups) if groups else "_nenhum_")
    await ev.reply(text, parse_mode="Markdown")

@bot_client.on(events.NewMessage(pattern=r'/subscribe ([-\d]+)'))
async def subscribe(ev):
    uid = str(ev.sender_id)
    gid = ev.pattern_match.group(1)
    lst = subscriptions.setdefault(uid, [])
    if gid in lst:
        await ev.reply(f"⚠️ `{gid}` já inscrito.", parse_mode="Markdown")
        return
    lst.append(gid)
    save_subs()
    await ev.reply(f"✅ `{uid}` inscrito em `{gid}`.", parse_mode="Markdown")

@bot_client.on(events.NewMessage(pattern=r'/unsubscribe ([-\d]+)'))
async def unsubscribe(ev):
    uid = str(ev.sender_id)
    gid = ev.pattern_match.group(1)
    lst = subscriptions.get(uid, [])
    if gid not in lst:
        await ev.reply(f"❌ Você não estava inscrito em `{gid}`.", parse_mode="Markdown")
        return
    lst.remove(gid)
    if not lst:
        subscriptions.pop(uid)
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

# --- MAIN ---
async def main():
    await start_clients()
    log.info("🤖 Bots rodando...")
    await bot_client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
