import os, json, asyncio, logging
from datetime import datetime
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession

# ─── logging ────────────────────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("encaminhador")

# ─── config via ENV ─────────────────────────────────────────────────────────
API_ID          = int(os.environ["TELEGRAM_API_ID"])
API_HASH        = os.environ["TELEGRAM_API_HASH"]
BOT_TOKEN       = os.environ["BOT_TOKEN"]
DEST_CHAT_ID    = int(os.environ["DEST_CHAT_ID"])
SESSION_STRING  = os.environ["SESSION_STRING"]
SOURCE_CHAT_IDS = json.loads(os.environ.get("SOURCE_CHAT_IDS","[]"))
raw_admins      = os.environ.get("ADMIN_IDS","[]")
try:
    parsed = json.loads(raw_admins)
    ADMIN_IDS = {parsed} if isinstance(parsed,int) else set(parsed if isinstance(parsed,list) else [])
except:
    ADMIN_IDS = set()

# ─── dados em /data ─────────────────────────────────────────────────────────
DATA_DIR   = "/data"
SESS_FILE  = os.path.join(DATA_DIR, "sessions.json")
SUBS_FILE  = os.path.join(DATA_DIR, "subscriptions.json")
AUDIT_FILE = os.path.join(DATA_DIR, "audit.json")

def ensure(path, default):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path,"w",encoding="utf-8") as f:
            json.dump(default,f,indent=2)
    with open(path,"r",encoding="utf-8") as f:
        return json.load(f)

def save(path, data):
    with open(path,"w",encoding="utf-8") as f:
        json.dump(data,f,indent=2)

sessions      = ensure(SESS_FILE, {})
subscriptions = ensure(SUBS_FILE, {})
audit_log     = ensure(AUDIT_FILE, [])

def log_audit(chat_id, status):
    evt = {"time":datetime.utcnow().isoformat(),"chat_id":chat_id,"status":status}
    audit_log.append(evt)
    save(AUDIT_FILE,audit_log)

def save_subs():
    save(SUBS_FILE,subscriptions)
def save_sess():
    save(SESS_FILE,sessions)

# ─── TELETHON CLIENT ÚNICO ───────────────────────────────────────────────────
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
client.start(bot_token=BOT_TOKEN)
log.info("🤖 Bot iniciado!")

# ─── ADMIN: set session / subs ───────────────────────────────────────────────
@client.on(events.NewMessage(func=lambda e: e.is_private and e.sender_id in ADMIN_IDS))
async def admin_handler(ev):
    txt = ev.raw_text.strip()
    uid = str(ev.sender_id)
    reply = ev.reply

    # /admin_set_session USER_ID SESSION
    if txt.startswith("/admin_set_session "):
        try:
            _, user_id, sess = txt.split(" ",2)
            sessions[user_id] = sess
            save_sess()
            return await reply(f"✅ Session de `{user_id}` registrada.", parse_mode="Markdown")
        except:
            return await reply("❌ Uso: `/admin_set_session USER_ID SESSION`", parse_mode="Markdown")

    # /admin_subscribe USER_ID GROUP_ID
    if txt.startswith("/admin_subscribe "):
        try:
            _, user_id, gid_str = txt.split(" ",2)
            gid = int(gid_str)
            lst = subscriptions.setdefault(user_id,[])
            if gid in lst:
                return await reply("⚠️ Já inscrito.", parse_mode="Markdown")
            lst.append(gid)
            save_subs()
            return await reply(f"✅ `{user_id}` inscrito em `{gid}`.", parse_mode="Markdown")
        except:
            return await reply("❌ Uso: `/admin_subscribe USER_ID GROUP_ID`", parse_mode="Markdown")

    # /admin_unsubscribe USER_ID GROUP_ID
    if txt.startswith("/admin_unsubscribe "):
        try:
            _, user_id, gid_str = txt.split(" ",2)
            gid = int(gid_str)
            lst = subscriptions.get(user_id,[])
            if gid not in lst:
                return await reply("❌ Não inscrito.", parse_mode="Markdown")
            lst.remove(gid)
            if lst: subscriptions[user_id]=lst
            else: subscriptions.pop(user_id)
            save_subs()
            return await reply(f"✅ `{user_id}` desinscrito de `{gid}`.", parse_mode="Markdown")
        except:
            return await reply("❌ Uso: `/admin_unsubscribe USER_ID GROUP_ID`", parse_mode="Markdown")

# ─── HANDLERS PÚBLICOS ───────────────────────────────────────────────────────
@client.on(events.NewMessage(func=lambda e: e.is_private))
async def ui_handler(ev):
    txt   = ev.raw_text.strip()
    uid   = str(ev.sender_id)
    reply = ev.reply

    if txt in ("/start","/help"):
        return await reply(
            "👋 *Encaminhador Bot*\n\n"
            "`/myid`\n"
            "`/setsession SUA_SESSION`\n"
            "`/listgroups`\n"
            "`/subscribe GROUP_ID`\n"
            "`/unsubscribe GROUP_ID`",
            parse_mode="Markdown"
        )

    if txt == "/myid":
        return await reply(f"🆔 Seu ID: `{ev.sender_id}`", parse_mode="Markdown")

    if txt.startswith("/setsession "):
        sess = txt.split(" ",1)[1].strip()
        sessions[uid] = sess
        save_sess()
        return await reply("✅ Session salva! Agora use `/listgroups`.", parse_mode="Markdown")

    # precisa ter session
    sess = sessions.get(uid)
    if not sess:
        return await reply("❌ First `/setsession SUA_SESSION`", parse_mode="Markdown")

    # lista grupos do usuário
    if txt == "/listgroups":
        # recria um client temporário
        tmp = TelegramClient(StringSession(sess), API_ID, API_HASH)
        await tmp.connect()
        diags = await tmp.get_dialogs()
        await tmp.disconnect()
        lines = [f"`{d.id}` – {d.title or 'sem título'}"
                 for d in diags if d.is_group or d.is_channel]
        text = "📋 *Seus grupos:*\n" + ("\n".join(lines[:50]) or "_nenhum_")
        return await reply(text, parse_mode="Markdown")

    # subscribe
    if txt.startswith("/subscribe "):
        try:
            gid = int(txt.split(" ",1)[1])
        except:
            return await reply("❌ ID inválido.", parse_mode="Markdown")
        lst = subscriptions.setdefault(uid,[])
        if gid in lst:
            return await reply("⚠️ Já inscrito.", parse_mode="Markdown")
        lst.append(gid)
        save_subs()
        return await reply(f"✅ Inscrito em `{gid}`.", parse_mode="Markdown")

    # unsubscribe
    if txt.startswith("/unsubscribe "):
        try:
            gid = int(txt.split(" ",1)[1])
        except:
            return await reply("❌ ID inválido.", parse_mode="Markdown")
        lst = subscriptions.get(uid,[])
        if gid not in lst:
            return await reply("❌ Não inscrito.", parse_mode="Markdown")
        lst.remove(gid)
        if lst: subscriptions[uid]=lst
        else: subscriptions.pop(uid)
        save_subs()
        return await reply(f"✅ Desinscrito de `{gid}`.", parse_mode="Markdown")

    # fallback
    await reply("❓ Comando não reconhecido. `/help`", parse_mode="Markdown")

# ─── FORWARD / FALLBACK Único (fixos + dinâmicos) ────────────────────────────
@client.on(events.NewMessage)
async def forward_handler(ev):
    cid = ev.chat_id
    m   = ev.message
    is_fixed = cid in SOURCE_CHAT_IDS
    is_dyn   = any(cid in lst for lst in subscriptions.values())
    if not (is_fixed or is_dyn):
        return

    header = f"🚀 *De:* `{cid}`\n\n" if is_fixed else ""
    log.info(f"🔍 Mensagem em {cid} fixed={is_fixed} dyn={is_dyn}")

    # 1) forward
    try:
        await m.forward_to(DEST_CHAT_ID)
        log_audit(cid,"forwarded")
        return
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds+1)
    except:
        pass

    # 2) mídia->download->send_file
    if m.media:
        try:
            path = await m.download_media()
            await client.send_file(DEST_CHAT_ID, path, caption=header + (m.text or ""))
            log_audit(cid,"media_sent")
            return
        except:
            pass

    # 3) texto puro
    if m.text:
        try:
            await client.send_message(DEST_CHAT_ID, header + m.text)
            log_audit(cid,"text_sent")
            return
        except:
            pass

    # 4) tudo falhou
    msg = (f"❌ Falha ao encaminhar id `{m.id}` de `{cid}` "
           f"em {datetime.utcnow().isoformat()}")
    await client.send_message(DEST_CHAT_ID,msg)
    log_audit(cid,"failed_all")

# ─── rodar ───────────────────────────────────────────────────────────────────
client.run_until_disconnected()
