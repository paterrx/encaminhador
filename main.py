import os, json, asyncio, logging
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ─── logging ────────────────────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("encaminhador")

# ─── config via ENV ─────────────────────────────────────────────────────────
API_ID         = int(os.environ["TELEGRAM_API_ID"])
API_HASH       = os.environ["TELEGRAM_API_HASH"]
BOT_TOKEN      = os.environ["BOT_TOKEN"]
DEST_CHAT_ID   = int(os.environ["DEST_CHAT_ID"])
SESSION_STRING = os.environ["SESSION_STRING"]
SOURCE_CHAT_IDS= json.loads(os.environ.get("SOURCE_CHAT_IDS","[]"))

# ─── paths de volume ────────────────────────────────────────────────────────
DATA_DIR    = "/data"
SUBS_FILE   = os.path.join(DATA_DIR, "subscriptions.json")
AUDIT_FILE  = os.path.join(DATA_DIR, "audit.json")

def ensure(path, default):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=2)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

subscriptions = ensure(SUBS_FILE, {})
audit_log      = ensure(AUDIT_FILE, [])

def log_audit(chat_id, status):
    evt = {"time": datetime.utcnow().isoformat(), "chat_id": chat_id, "status": status}
    audit_log.append(evt)
    save(AUDIT_FILE, audit_log)

def save_subs():
    save(SUBS_FILE, subscriptions)

# ─── cliente único ──────────────────────────────────────────────────────────
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
client.start(bot_token=BOT_TOKEN)

# ─── handler geral de todas as mensagens ────────────────────────────────────
@client.on(events.NewMessage)
async def handler(ev):
    cid = ev.chat_id
    m   = ev.message

    # se fixo ou dinâmico
    is_fixed = cid in SOURCE_CHAT_IDS
    is_dyn   = any(cid in lst for lst in subscriptions.values())

    if not is_fixed and not is_dyn:
        return

    # cabeçalho só pra fixos
    header = f"🚀 *De:* `{cid}`\n" if is_fixed else ""

    log.info(f"🔍 Mensagem em {cid} (fixed={is_fixed}, dynamic={is_dyn})")

    # 1) forward nativo
    try:
        await m.forward_to(DEST_CHAT_ID)
        log_audit(cid, "forwarded")
        return
    except:
        pass

    # 2) download + send_file
    if m.media:
        try:
            path = await m.download_media()
            await client.send_file(DEST_CHAT_ID, path, caption=header + (m.text or ""))
            log_audit(cid, "media_sent")
            return
        except:
            pass

    # 3) texto puro
    if m.text:
        try:
            await client.send_message(DEST_CHAT_ID, header + m.text)
            log_audit(cid, "text_sent")
            return
        except:
            pass

    # 4) tudo falhou
    err = f"❌ Falha ao encaminhar `{m.id}` de `{cid}` em {datetime.utcnow().isoformat()}"
    await client.send_message(DEST_CHAT_ID, err)
    log_audit(cid, "failed_all")

# ─── comandos de subscription ─────────────────────────────────────────────────
@client.on(events.NewMessage(pattern=r"^/subscribe\s+(-?\d+)$"))
async def subscribe(ev):
    uid = str(ev.sender_id)
    gid = int(ev.pattern_match.group(1))
    user = set(subscriptions.get(uid, []))
    if gid in user:
        return await ev.reply(f"⚠️ `{gid}` já inscrito.", parse_mode="Markdown")
    user.add(gid)
    subscriptions[uid] = list(user)
    save_subs()
    await ev.reply(f"✅ `{uid}` inscrito em `{gid}`.", parse_mode="Markdown")

@client.on(events.NewMessage(pattern=r"^/unsubscribe\s+(-?\d+)$"))
async def unsubscribe(ev):
    uid = str(ev.sender_id)
    gid = int(ev.pattern_match.group(1))
    user = set(subscriptions.get(uid, []))
    if gid not in user:
        return await ev.reply(f"❌ `{gid}` não estava inscrito.", parse_mode="Markdown")
    user.remove(gid)
    if user:
        subscriptions[uid] = list(user)
    else:
        subscriptions.pop(uid)
    save_subs()
    await ev.reply(f"✅ `{uid}` desinscrito de `{gid}`.", parse_mode="Markdown")

@client.on(events.NewMessage(pattern=r"^/listgroups$"))
async def listgroups(ev):
    uid = str(ev.sender_id)
    gids = subscriptions.get(uid, [])
    text = "📋 *Seus grupos:*\n" + ("\n".join(f"`{g}`" for g in gids) if gids else "_nenhum_")
    await ev.reply(text, parse_mode="Markdown")

@client.on(events.NewMessage(pattern=r"^/(start|help)$"))
async def help_cmd(ev):
    await ev.reply(
        "👋 *Encaminhador Bot*\n\n"
        "`/subscribe GROUP_ID`\n"
        "`/unsubscribe GROUP_ID`\n"
        "`/listgroups`",
        parse_mode="Markdown"
    )

# ─── mantém vivo ─────────────────────────────────────────────────────────────
print("🤖 Bot rodando...")
client.run_until_disconnected()
