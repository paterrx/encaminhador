import os
import json
import logging
import asyncio
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from flask import Flask, jsonify

# ————— Configuração de logging —————
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("encaminhador")

# ————— Paths ABSOLUTOS dentro do volume montado em /data —————
DATA_DIR   = "/data"
SUBS_FILE  = os.path.join(DATA_DIR, "subscriptions.json")
AUDIT_FILE = os.path.join(DATA_DIR, "audit.json")

# Garante que a pasta /data exista
os.makedirs(DATA_DIR, exist_ok=True)

# Inicializa os arquivos se ainda não existirem
if not os.path.exists(SUBS_FILE):
    with open(SUBS_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f)
    logger.info("Criado empty subscriptions.json")
if not os.path.exists(AUDIT_FILE):
    with open(AUDIT_FILE, "w", encoding="utf-8") as f:
        json.dump([], f)
    logger.info("Criado empty audit.json")

# ————— Carrega variáveis de ambiente —————
SOURCE_CHAT_IDS = json.loads(os.environ.get("SOURCE_CHAT_IDS", "[]"))
DEST_CHAT_ID    = int(os.environ["DEST_CHAT_ID"])
API_ID          = int(os.environ["TELEGRAM_API_ID"])
API_HASH        = os.environ["TELEGRAM_API_HASH"]
BOT_TOKEN       = os.environ["BOT_TOKEN"]
ADMIN_SESSION   = os.environ["SESSION_STRING"]

# ————— Flask para keep-alive e endpoints de debug —————
app = Flask("keep_alive")

@app.route("/")
def ping():
    return "OK"

@app.route("/dump_subs")
def dump_subs():
    with open(SUBS_FILE, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))

@app.route("/dump_audit")
def dump_audit():
    with open(AUDIT_FILE, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))

# ————— Funções auxiliares de I/O —————
def load_subs():
    with open(SUBS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_subs(subs):
    with open(SUBS_FILE, "w", encoding="utf-8") as f:
        json.dump(subs, f, indent=2)

def log_audit(entry):
    audit = []
    with open(AUDIT_FILE, "r", encoding="utf-8") as f:
        audit = json.load(f)
    audit.append(entry)
    with open(AUDIT_FILE, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2)

# ————— Instancia o client Telethon —————
bot = TelegramClient(StringSession(ADMIN_SESSION), API_ID, API_HASH)

# ————— Handlers de comando —————
@bot.on(events.NewMessage(pattern=r"^/setsession\s+(\S+)$"))
async def handler_setsession(ev):
    # neste design a própria session fixa do admin NÃO muda
    await ev.reply("Este comando não é mais usado — use BotFather para o userbot.")

@bot.on(events.NewMessage(pattern=r"^/listgroups$"))
async def handler_list(ev):
    subs = load_subs()
    lines = []
    for uid, gids in subs.items():
        for gid in gids:
            lines.append(f"UID {uid} → {gid}")
    if not lines:
        await ev.reply("⚠️ Nenhuma inscrição dinâmica.")
    else:
        await ev.reply("📋 *Seus grupos:*\n" + "\n".join(lines[:50]), parse_mode="Markdown")

@bot.on(events.NewMessage(pattern=r"^/subscribe\s+(-?\d+)$"))
async def handler_subscribe(ev):
    uid = ev.sender_id
    gid = int(ev.pattern_match.group(1))
    subs = load_subs()
    user_list = set(subs.get(str(uid), []))
    if gid in user_list:
        await ev.reply(f"⚠️ `{gid}` já estava inscrito.", parse_mode="Markdown")
        return
    user_list.add(gid)
    subs[str(uid)] = list(user_list)
    save_subs(subs)
    await ev.reply(f"✅ `{uid}` inscrito em `{gid}`.", parse_mode="Markdown")

@bot.on(events.NewMessage(pattern=r"^/unsubscribe\s+(-?\d+)$"))
async def handler_unsub(ev):
    uid = ev.sender_id
    gid = int(ev.pattern_match.group(1))
    subs = load_subs()
    user_list = set(subs.get(str(uid), []))
    if gid not in user_list:
        await ev.reply(f"⚠️ `{gid}` não estava inscrito.", parse_mode="Markdown")
        return
    user_list.remove(gid)
    if user_list:
        subs[str(uid)] = list(user_list)
    else:
        del subs[str(uid)]
    save_subs(subs)
    await ev.reply(f"✅ `{uid}` desinscrito de `{gid}`.", parse_mode="Markdown")

# ————— Handler principal de forwarding com fallbacks —————
@bot.on(events.NewMessage(chats=SOURCE_CHAT_IDS))
async def forward_event(ev):
    chat_id = ev.chat_id
    m      = ev.message
    header = f"🚀 *De:* `{chat_id}`\n"

    # 1) Tentativa de forward puro
    try:
        await m.forward_to(DEST_CHAT_ID)
        log_audit(f"forwarded {m.id} from {chat_id}")
        return
    except Exception:
        pass

    # 2) Tentativa de download+send_file
    if m.media:
        try:
            path = await m.download_media()
            await bot.send_file(DEST_CHAT_ID, path, caption=header + (m.text or ""))
            log_audit(f"downloaded+sent {m.id} from {chat_id}")
            return
        except Exception:
            pass

    # 3) Só texto
    if m.text:
        try:
            await bot.send_message(DEST_CHAT_ID, header + m.text)
            log_audit(f"textsent {m.id} from {chat_id}")
            return
        except Exception:
            pass

    # 4) Falha total
    await bot.send_message(
        DEST_CHAT_ID,
        f"❌ Falha ao encaminhar mensagem `{m.id}` de `{chat_id}` às `{m.date}`"
    )
    log_audit(f"failed {m.id} from {chat_id}")

# ————— Inicia tudo —————
async def main():
    # 1) Sobe Flask
    import threading
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080))),
        daemon=True
    ).start()

    # 2) Sobe Telethon
    await bot.start(bot_token=BOT_TOKEN)
    logger.info("🤖 Bot rodando e Flask keep-alive ativo")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
