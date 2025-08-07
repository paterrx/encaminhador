# main.py
import os, json, logging, asyncio, threading
from flask import Flask, jsonify
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ——— Config de logs ———
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("encaminhador")

# ——— Paths ABSOLUTOS dentro do volume /data ———
DATA_DIR   = "/data"
SUBS_FILE  = os.path.join(DATA_DIR, "subscriptions.json")
AUDIT_FILE = os.path.join(DATA_DIR, "audit.json")

# ——— Garantir que /data existe e inicializar os JSONs ———
os.makedirs(DATA_DIR, exist_ok=True)

if not os.path.exists(SUBS_FILE):
    with open(SUBS_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f)
    logger.info("🆕 Criou subscriptions.json vazio")

if not os.path.exists(AUDIT_FILE):
    with open(AUDIT_FILE, "w", encoding="utf-8") as f:
        json.dump([], f)
    logger.info("🆕 Criou audit.json vazio")

# ——— Carrega ENV Vars ———
SOURCE_CHAT_IDS = json.loads(os.environ.get("SOURCE_CHAT_IDS","[]"))
DEST_CHAT_ID    = int(os.environ["DEST_CHAT_ID"])
API_ID          = int(os.environ["TELEGRAM_API_ID"])
API_HASH        = os.environ["TELEGRAM_API_HASH"]
BOT_TOKEN       = os.environ["BOT_TOKEN"]
ADMIN_SESSION   = os.environ["SESSION_STRING"]

# ——— Flask keep-alive e debug endpoints ———
app = Flask("keep_alive")

@app.route("/")
def ping():
    return "OK"

@app.route("/dump_subs")
def dump_subs():
    return jsonify(json.load(open(SUBS_FILE, "r", encoding="utf-8")))

@app.route("/dump_audit")
def dump_audit():
    return jsonify(json.load(open(AUDIT_FILE, "r", encoding="utf-8")))

# ——— I/O helpers ———
def load_subs():
    return json.load(open(SUBS_FILE, "r", encoding="utf-8"))

def save_subs(subs):
    json.dump(subs, open(SUBS_FILE, "w", encoding="utf-8"), indent=2)

def log_audit(entry):
    a = json.load(open(AUDIT_FILE, "r", encoding="utf-8"))
    a.append(entry)
    json.dump(a, open(AUDIT_FILE, "w", encoding="utf-8"), indent=2)

# ——— Telethon client ———
bot = TelegramClient(StringSession(ADMIN_SESSION), API_ID, API_HASH)

# ——— Comandos de subscribe/unsubscribe/list ———
@bot.on(events.NewMessage(pattern=r"^/listgroups$"))
async def cmd_list(ev):
    subs = load_subs()
    lines = []
    for uid, gids in subs.items():
        for gid in gids:
            lines.append(f"UID {uid} → {gid}")
    await ev.reply(
        "📋 *Seus grupos:*\n" + ("\n".join(lines) if lines else "— nenhum —"),
        parse_mode="Markdown"
    )

@bot.on(events.NewMessage(pattern=r"^/subscribe\s+(-?\d+)$"))
async def cmd_sub(ev):
    uid, gid = str(ev.sender_id), int(ev.pattern_match.group(1))
    subs = load_subs()
    user = set(subs.get(uid, []))
    if gid in user:
        return await ev.reply(f"⚠️ `{gid}` já inscrito.", parse_mode="Markdown")
    user.add(gid); subs[uid] = list(user); save_subs(subs)
    await ev.reply(f"✅ `{uid}` inscrito em `{gid}`.", parse_mode="Markdown")

@bot.on(events.NewMessage(pattern=r"^/unsubscribe\s+(-?\d+)$"))
async def cmd_unsub(ev):
    uid, gid = str(ev.sender_id), int(ev.pattern_match.group(1))
    subs = load_subs()
    user = set(subs.get(uid, []))
    if gid not in user:
        return await ev.reply(f"⚠️ `{gid}` não estava inscrito.", parse_mode="Markdown")
    user.remove(gid)
    if user: subs[uid] = list(user)
    else: del subs[uid]
    save_subs(subs)
    await ev.reply(f"✅ `{uid}` desinscrito de `{gid}`.", parse_mode="Markdown")

# ——— Handler de forwarding com 4 fallbacks + audit ———
@bot.on(events.NewMessage(chats=SOURCE_CHAT_IDS))
async def forward(ev):
    chat_id, m = ev.chat_id, ev.message
    hdr = f"🚀 *De:* `{chat_id}`\n"
    # 1) forward nativo
    try:
        await m.forward_to(DEST_CHAT_ID)
        log_audit(f"forwarded {m.id} from {chat_id}")
        return
    except: pass
    # 2) download+send_file
    if m.media:
        try:
            path = await m.download_media()
            await bot.send_file(DEST_CHAT_ID, path, caption=hdr + (m.text or ""))
            log_audit(f"downloaded {m.id} from {chat_id}")
            return
        except: pass
    # 3) só texto
    if m.text:
        try:
            await bot.send_message(DEST_CHAT_ID, hdr + m.text)
            log_audit(f"textsent {m.id} from {chat_id}")
            return
        except: pass
    # 4) falha total
    await bot.send_message(
        DEST_CHAT_ID,
        f"❌ Falha ao encaminhar `{m.id}` de `{chat_id}` às `{m.date}`"
    )
    log_audit(f"failed {m.id} from {chat_id}")

# ——— Boot do Flask + Telethon ———
async def main():
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0",
                               port=int(os.environ.get("PORT",8080))),
        daemon=True
    ).start()
    await bot.start(bot_token=BOT_TOKEN)
    logger.info("🤖 Bot rodando e Flask ativo")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
