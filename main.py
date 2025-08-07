import os
import json
import asyncio
import threading
import logging
from typing import Set, Dict, List

from flask import Flask, jsonify
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl import functions, types  # para resolver chat vinculado

# ── ENV ──────────────────────────────────────────────────────────────────────
API_ID          = int(os.environ['TELEGRAM_API_ID'])
API_HASH        = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN       = os.environ['BOT_TOKEN']
DEST_CHAT_ID    = int(os.environ['DEST_CHAT_ID'])
SESSION_STRING  = os.environ['SESSION_STRING']           # session do admin (canais fixos)
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS','[]'))

_raw_admins = os.environ.get('ADMIN_IDS','[]')
try:
    parsed = json.loads(_raw_admins)
    ADMIN_IDS = {parsed} if isinstance(parsed, int) else set(parsed)
except:
    ADMIN_IDS = set()

DATA_DIR  = '/data'
SESS_FILE = os.path.join(DATA_DIR, 'sessions.json')      # { user_id: session_string }
SUBS_FILE = os.path.join(DATA_DIR, 'subscriptions.json') # { user_id: [base_ids...] }

# ── LOG ──────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('encaminhador')

# ── I/O ──────────────────────────────────────────────────────────────────────
def load_file(path, default):
    try:
        with open(path,'r',encoding='utf-8') as f:
            return json.load(f)
    except:
        return default

def save_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path,'w',encoding='utf-8') as f:
        json.dump(data, f, indent=2)

sessions: Dict[str, str]       = load_file(SESS_FILE, {})
subscriptions: Dict[str, List[int]] = load_file(SUBS_FILE, {})

# ── FLASK ────────────────────────────────────────────────────────────────────
app = Flask('keep_alive')

@app.route('/')
def home(): return 'OK'

@app.route('/dump_subs')
def dump_subs(): return jsonify(subscriptions)

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)), debug=False)

# ── BOT (BotFather) ─────────────────────────────────────────────────────────
bot = TelegramClient('bot_session', API_ID, API_HASH)

# Cache de “IDs expandidos” por usuário:
# ex.: {'6209...': { base_id: {base_id, linked_id}, ... }}
LINK_CACHE: Dict[str, Dict[int, Set[int]]] = {}

async def expand_ids_for_user(cli: TelegramClient, uid_key: str, base_id: int) -> Set[int]:
    """
    Para um base_id (canal ou chat), retorna um set com o próprio base_id
    e o ID do chat vinculado (discussion) se existir.
    Usa cache em LINK_CACHE[uid_key][base_id].
    """
    # cache hit
    user_cache = LINK_CACHE.setdefault(uid_key, {})
    if base_id in user_cache:
        return set(user_cache[base_id])

    expanded: Set[int] = {base_id}
    try:
        entity = await cli.get_entity(base_id)
        if isinstance(entity, (types.Channel, types.Chat)):
            # Para canais, tentar pegar o FullChannel e ver linked_chat_id
            if isinstance(entity, types.Channel):
                full = await cli(functions.channels.GetFullChannel(channel=entity))
                linked = getattr(full.full_chat, 'linked_chat_id', None)
                if linked:
                    expanded.add(-100 * 10**10 + linked if linked > 0 and linked < 10**10 else linked)
                    # obs: normalmente linked_chat_id já vem no formato -100..., então só add
            # Para supergrupos (megagroup), às vezes estão como Channel também (megagroup=True)
            # Se fosse um Chat normal (raríssimo p/ grandes), não há “linked” via API.

    except Exception as e:
        log.info(f"[expand] uid={uid_key} base_id={base_id} sem info de vinculo ({type(e).__name__})")

    user_cache[base_id] = expanded
    return expanded

async def compute_allowed_ids(cli: TelegramClient, uid_key: str) -> Set[int]:
    """
    Soma todos os IDs base inscritos + seus vinculados (expand).
    """
    allowed: Set[int] = set()
    base_list = subscriptions.get(uid_key, [])
    for base_id in base_list:
        ids = await expand_ids_for_user(cli, uid_key, base_id)
        allowed.update(ids)
    return allowed

@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def ui_handler(ev):
    uid, txt, reply = ev.sender_id, ev.raw_text.strip(), ev.reply

    # --- ADMIN SET SESSION ---
    if txt.startswith('/admin_set_session '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, sess = txt.split(' ',2)
            sessions[user_id] = sess
            save_file(SESS_FILE, sessions)
            await reply(f'✅ Session de `{user_id}` registrada.')
            await ensure_client(int(user_id))  # sobe listener já
        except:
            return await reply('❌ Uso: `/admin_set_session USER_ID SESSION`')
        return

    # --- ADMIN SUBSCRIBE ---
    if txt.startswith('/admin_subscribe '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, gid_s = txt.split(' ',2)
            gid = int(gid_s)
            lst = subscriptions.setdefault(user_id, [])
            if gid in lst:
                await reply('⚠️ Já inscrito.')
            else:
                lst.append(gid)
                save_file(SUBS_FILE, subscriptions)
                await reply(f'✅ `{user_id}` inscrito em `{gid}`.')
                # Recalcular link-cache na próxima mensagem automaticamente.
                await ensure_client(int(user_id))
        except:
            return await reply('❌ Uso: `/admin_subscribe USER_ID GROUP_ID`')
        return

    # --- ADMIN UNSUBSCRIBE ---
    if txt.startswith('/admin_unsubscribe '):
        if uid not in ADMIN_IDS:
            return await reply('🚫 Sem permissão.')
        try:
            _, user_id, gid_s = txt.split(' ',2)
            gid = int(gid_s)
            lst = subscriptions.get(user_id, [])
            if gid not in lst:
                await reply('❌ Não inscrito.')
            else:
                lst.remove(gid)
                save_file(SUBS_FILE, subscriptions)
                # Limpa cache expandido desse base_id
                if user_id in LINK_CACHE and gid in LINK_CACHE[user_id]:
                    LINK_CACHE[user_id].pop(gid, None)
                await reply(f'🗑️ `{user_id}` desinscrito de `{gid}`.')
        except:
            return await reply('❌ Uso: `/admin_unsubscribe USER_ID GROUP_ID`')
        return

    # --- HELP / START ---
    if txt in ('/start','/help'):
        return await reply(
            "👋 *Bem-vindo ao Encaminhador!*\n\n"
            "1️⃣ `/myid`\n"
            "2️⃣ `/setsession SUA_SESSION`\n"
            "3️⃣ `/listgroups`\n"
            "4️⃣ `/subscribe GROUP_ID`\n"
            "5️⃣ `/unsubscribe GROUP_ID`\n\n"
            "⚙️ Admin: `/admin_set_session`, `/admin_subscribe`, `/admin_unsubscribe`",
            parse_mode='Markdown'
        )

    if txt == '/myid':
        return await reply(f'🆔 Seu ID: `{uid}`', parse_mode='Markdown')

    if txt.startswith('/setsession '):
        sess = txt.split(' ',1)[1].strip()
        sessions[str(uid)] = sess
        save_file(SESS_FILE, sessions)
        await reply('✅ Session salva! Agora use `/listgroups`.')
        await ensure_client(uid)
        return

    # Garantir client do usuário comum
    client = await ensure_client(uid)
    if not client:
        return await reply('❌ Use `/setsession SUA_SESSION` antes.')

    if txt == '/listgroups':
        diags = await client.get_dialogs()
        lines = []
        for d in diags:
            if getattr(d.entity, 'megagroup', False) or getattr(d.entity, 'broadcast', False):
                lines.append(f"- `{d.id}` — {d.title or 'Sem título'}")
        payload = "📋 *Seus grupos:*\n" + "\n".join(lines[:50])
        return await reply(payload, parse_mode='Markdown')

    if txt.startswith('/subscribe '):
        try:
            gid = int(txt.split(' ',1)[1])
        except:
            return await reply('❌ ID inválido.')
        key = str(uid)
        lst = subscriptions.setdefault(key, [])
        if gid in lst:
            return await reply('⚠️ Já inscrito.')
        lst.append(gid)
        save_file(SUBS_FILE, subscriptions)
        # limpa cache pro usuário para recalcular expandidos
        LINK_CACHE.pop(key, None)
        await reply(f'✅ Inscrito em `{gid}`.')
        await ensure_client(uid)
        return

    if txt.startswith('/unsubscribe '):
        try:
            gid = int(txt.split(' ',1)[1])
        except:
            return await reply('❌ ID inválido.')
        key = str(uid)
        lst = subscriptions.get(key, [])
        if gid not in lst:
            return await reply('❌ Não inscrito.')
        lst.remove(gid)
        save_file(SUBS_FILE, subscriptions)
        if key in LINK_CACHE and gid in LINK_CACHE[key]:
            LINK_CACHE[key].pop(gid, None)
        return await reply(f'🗑️ Desinscrito de `{gid}`.')

    return await reply('❓ Comando não reconhecido. `/help`.', parse_mode='Markdown')

# ── ADMIN (fixos) ────────────────────────────────────────────────────────────
admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

@admin_client.on(events.NewMessage)
async def forward_initial(ev):
    cid = ev.chat_id
    log.info(f"🔍 [fixed] got message in fixed chat={cid}")
    if cid not in SOURCE_CHAT_IDS:
        return

    m     = ev.message
    chat  = await admin_client.get_entity(cid)
    title = getattr(chat, 'title', None) or str(cid)

    await admin_client.send_message(
        DEST_CHAT_ID, f"🏷️ *{title}* (`{cid}`)", parse_mode='Markdown'
    )

    try:
        await m.forward_to(DEST_CHAT_ID)
        return
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds+1)
    except Exception as e:
        log.exception(e)

    try:
        if m.media:
            path = await m.download_media()
            await admin_client.send_file(DEST_CHAT_ID, path, caption=m.text or '')
        else:
            await admin_client.send_message(DEST_CHAT_ID, m.text or '')
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds+1)
    except Exception as e:
        log.exception(e)

# ── DINÂMICOS ────────────────────────────────────────────────────────────────
user_clients: Dict[str, TelegramClient] = {}

async def ensure_client(uid: int):
    key = str(uid)
    sess = sessions.get(key)
    if not sess:
        return None
    if key in user_clients:
        return user_clients[key]

    cli = TelegramClient(StringSession(sess), API_ID, API_HASH)
    await cli.start()
    user_clients[key] = cli

    @cli.on(events.NewMessage)
    async def forward_user(ev):
        # Log de tudo que chega nessa session:
        log.info(f"🔍 [dynamic] user={key} got message from chat={ev.chat_id}")

        # Conjunto de IDs permitidos (base + vinculados)
        try:
            allowed = await compute_allowed_ids(cli, key)
        except Exception as e:
            log.exception(f"[dynamic] compute_allowed_ids falhou (user={key})")
            allowed = set(subscriptions.get(key, []))  # fallback simples

        if ev.chat_id not in allowed:
            log.info(f"⛔ [dynamic-skip] user={key} chat={ev.chat_id} not in allowed={sorted(allowed)}")
            return

        m     = ev.message
        chat  = await cli.get_entity(ev.chat_id)
        title = getattr(chat, 'title', None) or str(ev.chat_id)

        await bot.send_message(DEST_CHAT_ID, f"📢 *{title}* (`{ev.chat_id}`)", parse_mode='Markdown')

        try:
            await m.forward_to(DEST_CHAT_ID)
            return
        except errors.FloodWaitError as e:
            await asyncio.sleep(e.seconds+1)
        except Exception as e:
            log.exception(e)

        try:
            if m.media:
                path = await m.download_media()
                await bot.send_file(DEST_CHAT_ID, path, caption=m.text or '')
            else:
                await bot.send_message(DEST_CHAT_ID, m.text or '')
        except errors.FloodWaitError as e:
            await asyncio.sleep(e.seconds+1)
        except Exception as e:
            log.exception(e)

    asyncio.create_task(cli.run_until_disconnected())
    return cli

# ── ENTRYPOINT ───────────────────────────────────────────────────────────────
async def main():
    threading.Thread(target=run_flask, daemon=True).start()

    await asyncio.gather(
        admin_client.start(),
        bot.start(bot_token=BOT_TOKEN)
    )

    # Sobe listeners de TODO mundo conhecido (sessions + subscriptions)
    for uid_str in set(list(sessions.keys()) + list(subscriptions.keys())):
        try:
            await ensure_client(int(uid_str))
        except Exception:
            log.exception(f"falha ao iniciar listener {uid_str}")

    log.info('🤖 Bots rodando...')
    await asyncio.gather(
        admin_client.run_until_disconnected(),
        bot.run_until_disconnected()
    )

if __name__ == '__main__':
    asyncio.run(main())
