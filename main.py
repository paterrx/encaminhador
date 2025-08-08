# main.py
import os
import json
import asyncio
import logging
from typing import Dict, Optional, Tuple

from flask import Flask, jsonify
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetFullChannelRequest

# ─────────────────────────────────────────────────────────────────────────────
# ENV OBRIGATÓRIAS
API_ID         = int(os.environ['TELEGRAM_API_ID'])
API_HASH       = os.environ['TELEGRAM_API_HASH']
BOT_TOKEN      = os.environ['BOT_TOKEN']

# Canal principal de posts (onde entram as apostas)
DEST_CHAT_ID   = int(os.environ['DEST_CHAT_ID'])

# Canal SEPARADO para replicar comentários (vamos publicar um "post-espelho"
# aqui e os comentários irão para o chat vinculado desse canal).
DEST_COMMENTS_ID = int(os.environ['DEST_COMMENTS_ID'])

# Session string do admin (sua conta) para ler os grupos fixos
SESSION_STRING = os.environ.get('SESSION_STRING', '')

# Canais fixos (lista JSON de ids -100...)
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS', '[]'))

# Admins (lista JSON de ids)
try:
    ADMIN_IDS = set(json.loads(os.environ.get('ADMIN_IDS', '[]')))
except Exception:
    ADMIN_IDS = set()

# ─────────────────────────────────────────────────────────────────────────────
# Persistência em volume /data
DATA_DIR   = '/data'
SESS_FILE  = os.path.join(DATA_DIR, 'sessions.json')
SUBS_FILE  = os.path.join(DATA_DIR, 'subscriptions.json')
MAP_FILE   = os.path.join(DATA_DIR, 'shadow_map.json')  # (src_chat/src_msg) -> dest_shadow_msg

def _load(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default

def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

sessions: Dict[str, str]       = _load(SESS_FILE, {})
subscriptions: Dict[str, list] = _load(SUBS_FILE, {})
shadow_map: Dict[str, int]     = _load(MAP_FILE, {})

def map_key(chat_id: int, msg_id: int) -> str:
    return f"{chat_id}/{msg_id}"

# ─────────────────────────────────────────────────────────────────────────────
# Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("encaminhador")

# ─────────────────────────────────────────────────────────────────────────────
# Flask util
app = Flask('keep_alive')

@app.route('/')
def index():
    return "ok"

@app.route('/dump_subs')
def dump_subs():
    return jsonify(subscriptions)

@app.route('/dump_shadow')
def dump_shadow():
    return jsonify(shadow_map)

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False)

# ─────────────────────────────────────────────────────────────────────────────
# Clients
bot          = TelegramClient('bot_session', API_ID, API_HASH)
admin_client: Optional[TelegramClient] = None
if SESSION_STRING:
    admin_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# Descoberta de chats vinculados (origem e destino)
fixed_discuss_by_base: Dict[int, Optional[int]] = {}     # base_channel_id -> discuss_id ou None
dest_comments_discuss_id: Optional[int] = None           # chat vinculado do canal DEST_COMMENTS_ID

async def discover_linked_discuss(cli: TelegramClient, channel_id: int) -> Optional[int]:
    """
    Retorna o chat vinculado (discussion) de um canal, se existir.
    """
    try:
        ent = await cli.get_entity(channel_id)
        full = await cli(GetFullChannelRequest(ent))
        # Para canais, normalmente fica em: full.full_chat.linked_chat_id
        linked = getattr(full.full_chat, 'linked_chat_id', None)
        return int(linked) if linked else None
    except Exception as e:
        log.warning(f"[discover] não foi possível obter linked_chat_id de {channel_id}: {e}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
# Helpers de envio (encaminha/copia com fallback) e criação de post-espelho

async def _send_header_and_copy(send_client: TelegramClient,
                                dest_chat_id: int,
                                src_client: TelegramClient,
                                src_msg,
                                header: str) -> int:
    """
    Envia header + mensagem (forward com fallback) para dest_chat_id.
    Retorna o message_id enviado.
    """
    # Cabeçalho
    await send_client.send_message(dest_chat_id, header, parse_mode='Markdown')

    # 1) forward
    try:
        sent = await src_msg.forward_to(dest_chat_id)
        return sent.id
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
    except Exception:
        pass

    # 2) download + reenvio
    if src_msg.media:
        path = await src_client.download_media(src_msg)
        sent = await send_client.send_file(dest_chat_id, path, caption=src_msg.text or '')
        return sent.id
    else:
        sent = await send_client.send_message(dest_chat_id, src_msg.text or '')
        return sent.id

async def post_to_both_and_map(src_client: TelegramClient,
                               src_chat_id: int,
                               src_msg) -> Tuple[int, int]:
    """
    Publica o post no canal principal (DEST_CHAT_ID) e cria um espelho
    no canal de comentários (DEST_COMMENTS_ID). Salva o mapeamento
    (src_chat/src_msg.id -> dest_shadow_msg_id).
    Retorna (id_no_dest_principal, id_no_canal_de_comentários).
    """
    # Descobre títulos
    try:
        ent = await src_client.get_entity(src_chat_id)
        title = getattr(ent, 'title', None) or str(src_chat_id)
    except Exception:
        title = str(src_chat_id)

    header = f"📢 *{title}* (`{src_chat_id}`)"

    # envia para o canal principal
    main_id = await _send_header_and_copy(bot, DEST_CHAT_ID, src_client, src_msg, header)

    # cria espelho no canal de comentários
    shadow_id = await _send_header_and_copy(bot, DEST_COMMENTS_ID, src_client, src_msg, header)

    # persiste mapeamento para usar nas réplicas de comentários
    shadow_map[map_key(src_chat_id, src_msg.id)] = shadow_id
    _save(MAP_FILE, shadow_map)

    return main_id, shadow_id

async def replicate_comment_to_shadow(send_client: TelegramClient,
                                      comment_msg,
                                      author_name: str,
                                      shadow_top_id: int):
    """
    Replica um comentário para o CHAT VINCULADO do canal de comentários,
    **respondendo** ao post-espelho (shadow_top_id).
    """
    if not dest_comments_discuss_id:
        return

    prefix = f"👤 {author_name}\n"
    if comment_msg.media:
        path = await send_client.download_media(comment_msg)
        await bot.send_file(dest_comments_discuss_id, path,
                            caption=prefix + (comment_msg.text or ''),
                            reply_to=shadow_top_id)
    else:
        await bot.send_message(dest_comments_discuss_id,
                               prefix + (comment_msg.text or ''),
                               reply_to=shadow_top_id)

def get_top_id_from_comment(msg) -> Optional[int]:
    """
    Extrai o reply_to_top_id de uma mensagem de discussão.
    """
    # Telethon pode expor em msg.reply_to or msg.reply_to_top_id dependendo da versão
    try:
        if getattr(msg, 'reply_to_top_id', None):
            return int(msg.reply_to_top_id)
        if getattr(msg, 'reply_to', None) and getattr(msg.reply_to, 'reply_to_top_id', None):
            return int(msg.reply_to.reply_to_top_id)
    except Exception:
        pass
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Handlers: FIXOS (admin_client)

if admin_client:
    @admin_client.on(events.NewMessage)
    async def handle_fixed(ev):
        cid = ev.chat_id
        m   = ev.message

        # Mensagem vinda de um canal fixo?
        if cid in SOURCE_CHAT_IDS:
            await post_to_both_and_map(admin_client, cid, m)
            return

        # Mensagem vinda de um CHAT VINCULADO a algum fixo?
        # (descobrimos no startup e guardamos em fixed_discuss_by_base)
        if cid in fixed_discuss_by_base.values():
            # precisamos descobrir qual base gerou esse chat
            base_id = next((base for base, d in fixed_discuss_by_base.items() if d == cid), None)
            if not base_id:
                return

            top_id = get_top_id_from_comment(m)
            if not top_id:
                return

            key = map_key(base_id, top_id)
            shadow_id = shadow_map.get(key)

            # Se ainda não temos espelho (primeiro comentário chegou antes de postarmos),
            # buscamos o post original e criamos espelho agora.
            if not shadow_id:
                try:
                    original = await admin_client.get_messages(base_id, ids=top_id)
                    _, shadow_id = await post_to_both_and_map(admin_client, base_id, original)
                except Exception as e:
                    log.warning(f"[fixed-comment] não consegui criar espelho p/ {base_id}/{top_id}: {e}")
                    return

            # nome do autor
            try:
                s = await ev.get_sender()
                author = (getattr(s, 'first_name', '') + ' ' + (getattr(s, 'last_name', '') or '')).strip() or (s.username or '—')
            except Exception:
                author = '—'

            await replicate_comment_to_shadow(admin_client, m, author, shadow_id)

# ─────────────────────────────────────────────────────────────────────────────
# Dinâmicos (sessions fornecidas pelos usuários)

user_clients: Dict[str, TelegramClient] = {}
user_discuss_by_base: Dict[str, Dict[int, Optional[int]]] = {}  # por usuário

async def ensure_client(uid: int) -> Optional[TelegramClient]:
    key = str(uid)
    if key in user_clients:
        return user_clients[key]

    sess = sessions.get(key)
    if not sess:
        return None

    cli = TelegramClient(StringSession(sess), API_ID, API_HASH)
    await cli.start()
    user_clients[key] = cli
    user_discuss_by_base[key] = {}

    # Inscreve handler das mensagens desse user
    @cli.on(events.NewMessage)
    async def forward_user(ev):
        cid = ev.chat_id
        m   = ev.message

        # Grupos base desse user
        bases = set(subscriptions.get(key, []))

        # Descobre/guarda discuss de cada base (lazy)
        disc_map = user_discuss_by_base.get(key, {})
        if cid in bases and cid not in disc_map:
            disc_map[cid] = await discover_linked_discuss(cli, cid)
            user_discuss_by_base[key] = disc_map

        # Calcula o conjunto "allowed" (base + discuss)
        allowed = set(bases)
        for b, d in disc_map.items():
            if d:
                allowed.add(d)

        # Se veio do base → postar/copy e mapear
        if cid in bases:
            await post_to_both_and_map(cli, cid, m)
            return

        # Se veio do discuss vinculado → replicar comentário
        if cid in allowed and cid not in bases:
            # achar base_id dono desse discuss
            base_id = next((b for b, d in disc_map.items() if d == cid), None)
            if not base_id:
                return

            top_id = get_top_id_from_comment(m)
            if not top_id:
                return

            key_map = map_key(base_id, top_id)
            shadow_id = shadow_map.get(key_map)

            if not shadow_id:
                try:
                    original = await cli.get_messages(base_id, ids=top_id)
                    _, shadow_id = await post_to_both_and_map(cli, base_id, original)
                except Exception as e:
                    log.warning(f"[dyn-comment] não consegui criar espelho p/ {base_id}/{top_id}: {e}")
                    return

            try:
                s = await ev.get_sender()
                author = (getattr(s, 'first_name', '') + ' ' + (getattr(s, 'last_name', '') or '')).strip() or (s.username or '—')
            except Exception:
                author = '—'

            await replicate_comment_to_shadow(cli, m, author, shadow_id)

    return cli

# ─────────────────────────────────────────────────────────────────────────────
# BOT (comandos)

@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def bot_commands(ev):
    uid = ev.sender_id
    txt = (ev.raw_text or '').strip()
    reply = ev.respond

    def admin_only():
        return uid in ADMIN_IDS

    # /start
    if txt in ('/start', '/help'):
        return await reply(
            "👋 *Encaminhador Bot*\n\n"
            "1) `/myid`\n"
            "2) `/setsession SUA_SESSION`\n"
            "3) `/listgroups`\n"
            "4) `/subscribe GROUP_ID`\n"
            "5) `/unsubscribe GROUP_ID`\n\n"
            "👑 *Admin*\n"
            "• `/admin_set_session USER_ID SESSION`\n"
            "• `/admin_subscribe USER_ID GROUP_ID`\n"
            "• `/admin_unsubscribe USER_ID GROUP_ID`\n"
            "• `/admin_who_from_session SESSION`  ← *descobre dono da session*",
            parse_mode='Markdown'
        )

    if txt == '/myid':
        return await reply(f"🆔 `{uid}`", parse_mode='Markdown')

    # Usuário: setar própria session
    if txt.startswith('/setsession '):
        sess = txt.split(' ', 1)[1].strip()
        sessions[str(uid)] = sess
        _save(SESS_FILE, sessions)
        await ensure_client(uid)
        return await reply("✅ Session salva! Use `/listgroups`.")

    # Usuário: listar grupos (da PRÓPRIA session)
    if txt == '/listgroups':
        cli = await ensure_client(uid)
        if not cli:
            return await reply("⚠️ Antes, envie `/setsession SUA_SESSION`.")
        dialogs = await cli.get_dialogs()
        lines = []
        for d in dialogs:
            if getattr(d.entity, 'megagroup', False) or getattr(d.entity, 'broadcast', False):
                lines.append(f"- `{d.entity.id}` — {getattr(d.entity, 'title', '')}")
        if not lines:
            return await reply("Sem grupos/canais visíveis nessa sessão.")
        return await reply("*Seus grupos:*\n" + "\n".join(lines[:100]), parse_mode='Markdown')

    # Usuário: subscribe / unsubscribe
    if txt.startswith('/subscribe '):
        try:
            gid = int(txt.split(' ', 1)[1])
        except Exception:
            return await reply("ID inválido.")
        lst = subscriptions.setdefault(str(uid), [])
        if gid in lst:
            return await reply("⚠️ Já inscrito.")
        lst.append(gid)
        _save(SUBS_FILE, subscriptions)
        await ensure_client(uid)  # garante handler
        return await reply("✅ Inscrito.")

    if txt.startswith('/unsubscribe '):
        try:
            gid = int(txt.split(' ', 1)[1])
        except Exception:
            return await reply("ID inválido.")
        lst = subscriptions.get(str(uid), [])
        if gid not in lst:
            return await reply("⚠️ Não estava inscrito.")
        lst.remove(gid)
        _save(SUBS_FILE, subscriptions)
        return await reply("🗑️ Removido.")

    # ── Admin only
    if txt.startswith('/admin_set_session '):
        if not admin_only():
            return await reply("🚫 Sem permissão.")
        try:
            _, user_id, sess = txt.split(' ', 2)
            sessions[user_id] = sess
            _save(SESS_FILE, sessions)
            await ensure_client(int(user_id))
            return await reply(f"✅ Session de `{user_id}` registrada.", parse_mode='Markdown')
        except Exception:
            return await reply("Uso: /admin_set_session USER_ID SESSION")

    if txt.startswith('/admin_subscribe '):
        if not admin_only():
            return await reply("🚫 Sem permissão.")
        try:
            _, user_id, gid = txt.split(' ', 2)
            gid = int(gid)
            lst = subscriptions.setdefault(user_id, [])
            if gid in lst:
                return await reply("⚠️ Já inscrito.")
            lst.append(gid)
            _save(SUBS_FILE, subscriptions)
            await ensure_client(int(user_id))
            return await reply("✅ Inscrito.")
        except Exception:
            return await reply("Uso: /admin_subscribe USER_ID GROUP_ID")

    if txt.startswith('/admin_unsubscribe '):
        if not admin_only():
            return await reply("🚫 Sem permissão.")
        try:
            _, user_id, gid = txt.split(' ', 2)
            gid = int(gid)
            lst = subscriptions.get(user_id, [])
            if gid not in lst:
                return await reply("⚠️ Não estava inscrito.")
            lst.remove(gid)
            _save(SUBS_FILE, subscriptions)
            return await reply("🗑️ Removido.")
        except Exception:
            return await reply("Uso: /admin_unsubscribe USER_ID GROUP_ID")

    # Descobrir dono da session (id/username)
    if txt.startswith('/admin_who_from_session '):
        if not admin_only():
            return await reply("🚫 Sem permissão.")
        try:
            sess = txt.split(' ', 1)[1].strip()
            tmp = TelegramClient(StringSession(sess), API_ID, API_HASH)
            await tmp.start()
            me = await tmp.get_me()
            out = f"✅ ID: `{me.id}`\n@{me.username or '-'}"
            await tmp.disconnect()
            return await reply(out, parse_mode='Markdown')
        except Exception as e:
            return await reply(f"❌ Não foi possível: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN

async def main():
    # Flask em background
    asyncio.create_task(asyncio.to_thread(run_flask))

    # Inicia bot + admin
    await bot.start(bot_token=BOT_TOKEN)

    if admin_client:
        await admin_client.start()

        # Descobre discuss de cada canal fixo
        for base in SOURCE_CHAT_IDS:
            fixed_discuss_by_base[base] = await discover_linked_discuss(admin_client, base)

    # Descobre discuss do canal de comentários (destino)
    global dest_comments_discuss_id
    dest_comments_discuss_id = await discover_linked_discuss(bot, DEST_COMMENTS_ID)
    log.info(f"[dest] discuss_id para DEST_COMMENTS_ID={DEST_COMMENTS_ID} → {dest_comments_discuss_id}")

    log.info("🤖 Bot iniciado!")
    await asyncio.gather(
        bot.run_until_disconnected(),
        admin_client.run_until_disconnected() if admin_client else asyncio.Future()
    )

if __name__ == '__main__':
    asyncio.run(main())
