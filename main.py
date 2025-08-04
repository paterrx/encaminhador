# main.py
# Encaminhador com comandos de administrador para gerenciar sessões e inscrições arbitrárias
import os, json, asyncio, threading
from flask import Flask, jsonify
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetFullChannelRequest

# ── Configuração via ENV vars ─────────────────────────────────────────────
API_ID          = int(os.environ['TELEGRAM_API_ID'])
API_HASH        = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN       = os.environ['BOT_TOKEN']
DEST_CHAT_ID    = int(os.environ['DEST_CHAT_ID'])
SESSION_STRING  = os.environ['SESSION_STRING']
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS','[]'))
ADMIN_IDS       = set(json.loads(os.environ.get('ADMIN_IDS','[]')))
# e.g. ADMIN_IDS = {786880968}

# ── Persistência ────────────────────────────────────────────────────────
SESS_FILE = 'sessions.json'
SUBS_FILE = 'subscriptions.json'

def load(f):
    try:
        with open(f,'r',encoding='utf-8') as r:
            return json.load(r)
    except:
        return {}

def save(f,d):
    with open(f,'w',encoding='utf-8') as w:
        json.dump(d, w, indent=2)

sessions      = load(SESS_FILE)
subscriptions = load(SUBS_FILE)

# ── Flask keep-alive + debug ─────────────────────────────────────────────
app = Flask('keep_alive')

@app.route('/')
def home(): return 'OK'

@app.route('/dump_subs')
def dump_subs(): return jsonify(subscriptions)

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)))

# ── BotFather userbot ────────────────────────────────────────────────────
bot = TelegramClient('bot_session', API_ID, API_HASH)

@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def handler(ev):
    uid, text, reply = ev.sender_id, ev.raw_text.strip(), ev.reply

    # ==== comandos ADMIN ====  
    # /admin_set_session <user_id> <session_string>
    if text.startswith('/admin_set_session '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, u2, sess = text.split(' ',2)
            sessions[u2] = sess
            save(SESS_FILE, sessions)
            return await reply(f'✅ Session de `{u2}` registrada.')
        except:
            return await reply('❌ Uso: /admin_set_session USER_ID SESSION')

    # /admin_subscribe <user_id> <group_id>
    if text.startswith('/admin_subscribe '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, u2, gid = text.split(' ',2)
            gid = int(gid)
            lst = subscriptions.setdefault(u2, [])
            if gid in lst:
                return await reply(f'⚠️ `{u2}` já inscrito em `{gid}`.')
            lst.append(gid)
            save(SUBS_FILE, subscriptions)
            return await reply(f'✅ `{u2}` inscrito em `{gid}`.')
        except:
            return await reply('❌ Uso: /admin_subscribe USER_ID GROUP_ID')

    # /admin_unsubscribe <user_id> <group_id>
    if text.startswith('/admin_unsubscribe '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, u2, gid = text.split(' ',2)
            gid = int(gid)
            lst = subscriptions.get(u2, [])
            if gid not in lst:
                return await reply(f'❌ `{u2}` não inscrito em `{gid}`.')
            lst.remove(gid)
            save(SUBS_FILE, subscriptions)
            return await reply(f'🗑️ `{u2}` desinscrito de `{gid}`.')
        except:
            return await reply('❌ Uso: /admin_unsubscribe USER_ID GROUP_ID')

    # ==== comandos PÚBLICOS ====  
    if text in ('/start','/help'):
        await reply(
            "**👋 Bem-vindo ao Encaminhador!**\n\n"
            "1️⃣ Use `/myid` para descobrir seu ID.\n"
            "2️⃣ Coloque sua session: `/setsession SUA_SESSION`.\n"
            "3️⃣ Liste: `/listgroups`.\n"
            "4️⃣ Assine: `/subscribe GROUP_ID`.\n"
            "5️⃣ Cancele: `/unsubscribe GROUP_ID`.",
            parse_mode='Markdown'
        )
        return

    if text == '/myid':
        return await reply(f'🆔 Seu user_id: `{uid}`', parse_mode='Markdown')

    if text.startswith('/setsession '):
        s = text.split(' ',1)[1].strip()
        sessions[str(uid)] = s
        save(SESS_FILE, sessions)
        await reply('✅ Session registrada! Use `/listgroups`.')
        await ensure_client(uid)
        return

    client = await ensure_client(uid)
    if not client:
        return await reply('❌ Primeiro use `/setsession`.')

    if text == '/listgroups':
        dlg = await client.get_dialogs()
        lines = [f"{d.title or 'Sem título'} — `{d.id}`" for d in dlg if d.is_group or d.is_channel]
        await reply('📋 *Seus grupos:*\n'+ '\n'.join(lines[:50]), parse_mode='Markdown')
        return

    if text.startswith('/subscribe '):
        try:
            gid = int(text.split(' ',1)[1])
        except:
            return await reply('❌ ID inválido.')
        lst = subscriptions.setdefault(str(uid), [])
        if gid in lst:
            return await reply('⚠️ Já inscrito.')
        lst.append(gid)
        save(SUBS_FILE, subscriptions)
        return await reply(f'✅ Inscrito em `{gid}`.')

    if text.startswith('/unsubscribe '):
        try:
            gid = int(text.split(' ',1)[1])
        except:
            return await reply('❌ ID inválido.')
        lst = subscriptions.get(str(uid), [])
        if gid not in lst:
            return await reply('❌ Não inscrito.')
        lst.remove(gid)
        save(SUBS_FILE, subscriptions)
        return await reply(f'🗑️ Desinscrito de `{gid}`.')

    await reply('❓ Comando não reconhecido. Use `/help`.', parse_mode='Markdown')

# ── Admin client (3 canais iniciais) ────────────────────────────────────
admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

@admin_client.on(events.NewMessage(chats=SOURCE_CHAT_IDS))
async def forward_initial(ev):
    ch = await admin_client.get_entity(ev.chat_id)
    title = getattr(ch,'title',None) or str(ev.chat_id)
    await admin_client.send_message(DEST_CHAT_ID, f"📢 *{title}* (`{ev.chat_id}`)", parse_mode='Markdown')
    msg = ev.message
    try:
        await msg.forward_to(DEST_CHAT_ID)
    except:
        if msg.media:
            path = await msg.download_media()
            await admin_client.send_file(DEST_CHAT_ID, path, caption=msg.text or '')
        else:
            await admin_client.send_message(DEST_CHAT_ID, msg.text or '')

# ── Clientes por usuário ─────────────────────────────────────────────
user_clients = {}

async def ensure_client(uid):
    key = str(uid)
    if key in user_clients: return user_clients[key]
    sess = sessions.get(key)
    if not isinstance(sess,str) or not sess: return None
    try:
        cli = TelegramClient(StringSession(sess), API_ID, API_HASH)
    except ValueError:
        sessions.pop(key,None); save(SESS_FILE,sessions)
        await bot.send_message(uid, '🚫 Session inválida. Faça `/setsession` de novo.')
        return None
    await cli.start()
    user_clients[key] = cli

    @cli.on(events.NewMessage)
    async def forward_user(ev):
        if ev.chat_id not in subscriptions.get(key,[]): return
        ch = await cli.get_entity(ev.chat_id)
        title = getattr(ch,'title',None) or str(ev.chat_id)
        await bot.send_message(DEST_CHAT_ID, f"📢 *{title}* (`{ev.chat_id}`)", parse_mode='Markdown')
        m = ev.message
        try:
            await m.forward_to(DEST_CHAT_ID)
        except:
            if m.media:
                p = await m.download_media()
                await bot.send_file(DEST_CHAT_ID,p,caption=m.text or '')
            else:
                await bot.send_message(DEST_CHAT_ID,m.text or '')
        try:
            full = await cli(GetFullChannelRequest(channel=ev.chat_id))
            linked = getattr(full.full_chat,'linked_chat_id',None)
            if linked:
                cms = await cli.get_messages(linked,limit=20)
                for cm in cms:
                    await bot.send_message(DEST_CHAT_ID,f"💬 Comentário de {title} (`{linked}`)", parse_mode='Markdown')
                    if cm.media:
                        await bot.send_file(DEST_CHAT_ID,cm.media,caption=cm.text or '')
                    else:
                        await bot.send_message(DEST_CHAT_ID,cm.text or '')
        except:
            pass

    asyncio.create_task(cli.run_until_disconnected())
    return cli

# ── Execução ───────────────────────────────────────────────────────────
async def main():
    threading.Thread(target=run_flask,daemon=True).start()
    await asyncio.gather(admin_client.start(), bot.start(bot_token=BOT_TOKEN))
    print('🤖 Bots rodando...')
    await asyncio.gather(admin_client.run_until_disconnected(), bot.run_until_disconnected())

if __name__=='__main__':
    asyncio.run(main())
