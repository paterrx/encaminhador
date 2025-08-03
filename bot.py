import os, json, threading
from flask import Flask
from telegram import Update, Chat
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, filters, ContextTypes
)

# --- Persistência em JSON ---
DATA_FILE = 'subscriptions.json'
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, 'r') as f:
        subs = json.load(f)
else:
    subs = {}

def save_subs():
    with open(DATA_FILE, 'w') as f:
        json.dump(subs, f, indent=2)

# --- Helpers ---
def is_admin(user_id: int) -> bool:
    admin_id = os.getenv('ADMIN_ID')
    if not admin_id:
        return False
    try:
        return user_id == int(admin_id)
    except ValueError:
        return False

HELP_TEXT = {
    'setdest':      "📌 Use /setdest dentro do grupo *paterra Tips* para defini-lo como destino.",
    'addsource':    "➕ Use /addsource dentro de cada grupo-fonte onde quer encaminhar.",
    'listsources':  "📋 Use /listsources em DM para ver suas fontes pessoais e as fixas (admin).",
    'removesource': "🗑️ Use /removesource <ID_grupo> em DM para retirar uma fonte que você adicionou."
}

# --- Handlers de comando ---

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Bem-vindo ao Encaminhador!*\n\n"
        "1️⃣ Adicione-me no grupo *paterra Tips* e envie /setdest lá.\n"
        "2️⃣ Adicione-me em cada grupo-fonte e envie /addsource lá.\n"
        "3️⃣ Tudo que chegar nesses grupos será enviado para *paterra Tips*.\n\n"
        "Use /help para o guia de comandos."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lines = ["*Guia de comandos:*"]
    for cmd, desc in HELP_TEXT.items():
        lines.append(f"/{cmd} — {desc}")
    lines.append("\nUse /start para o passo-a-passo completo.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def setdest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    uid  = update.effective_user.id
    if not is_admin(uid):
        return await update.message.reply_text("❌ Só o admin pode usar /setdest.")
    if chat.type not in (Chat.GROUP, Chat.SUPERGROUP):
        return await update.message.reply_text(HELP_TEXT['setdest'], parse_mode="Markdown")
    subs['admin'] = subs.get('admin', {})
    subs['admin']['dest']   = chat.id
    subs['admin']['always'] = subs['admin'].get('always', [])
    save_subs()
    await update.message.reply_text(
        f"✅ Destino definido: *{chat.title}* (`{chat.id}`)",
        parse_mode="Markdown"
    )

async def addsource(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    uid  = str(update.effective_user.id)
    info = subs.get('admin')
    if not info or 'dest' not in info:
        return await update.message.reply_text("❌ Admin ainda não definiu destino.")
    if chat.type not in (Chat.GROUP, Chat.SUPERGROUP):
        return await update.message.reply_text(HELP_TEXT['addsource'], parse_mode="Markdown")

    gid = chat.id
    title = chat.title or str(gid)

    # admin adiciona fonte fixa
    if is_admin(update.effective_user.id):
        if gid in info['always']:
            return await update.message.reply_text("⚠️ Já é fonte fixa.")
        info['always'].append(gid)
        save_subs()
        return await update.message.reply_text(
            f"📌 Fonte fixa adicionada: *{title}* (`{gid}`)",
            parse_mode="Markdown"
        )

    # usuário comum adiciona na própria lista
    user_key = f"user:{uid}"
    subs.setdefault(user_key, {'sources': []})
    user_list = subs[user_key]['sources']
    if gid in user_list:
        return await update.message.reply_text("⚠️ Você já adicionou este grupo.")
    user_list.append(gid)
    save_subs()
    await update.message.reply_text(
        f"✅ Fonte pessoal adicionada: *{title}* (`{gid}`)",
        parse_mode="Markdown"
    )

async def listsources(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = str(update.effective_user.id)
    info = subs.get('admin', {})
    fixed = info.get('always', [])
    user  = subs.get(f"user:{uid}", {}).get('sources', [])
    text = (
        "*Suas fontes pessoais:*\n" +
        ("\n".join(f"`{g}`" for g in user) or "_nenhuma_") +
        "\n\n*Fontes fixas (admin):*\n" +
        ("\n".join(f"`{g}`" for g in fixed) or "_nenhuma_")
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def removesource(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    args = ctx.args
    if not args:
        return await update.message.reply_text(
            HELP_TEXT['removesource'], parse_mode="Markdown"
        )
    gid = int(args[0])
    user_key = f"user:{uid}"
    user_list = subs.get(user_key, {}).get('sources', [])
    if gid not in user_list:
        return await update.message.reply_text("❌ Você não está inscrito neste grupo.")
    user_list.remove(gid)
    save_subs()
    await update.message.reply_text(
        f"🗑️ Você desinscreveu o grupo `{gid}`.", parse_mode="Markdown"
    )

# --- Captura e encaminha mensagens ---
async def forward_messages(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    info = subs.get('admin', {})
    dest = info.get('dest')
    if not dest:
        return
    sources = set(info.get('always', []))
    sources.update(subs.get(f"user:{update.effective_user.id}", {}).get('sources', []))
    if chat_id not in sources:
        return
    await update.message.copy(chat_id=dest)

# --- Montagem e execução ---
def main():
    token = os.environ['BOT_TOKEN']
    app   = ApplicationBuilder().token(token).build()

    # Registra handlers
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('setdest', setdest))
    app.add_handler(CommandHandler('addsource', addsource))
    app.add_handler(CommandHandler('listsources', listsources))
    app.add_handler(CommandHandler('removesource', removesource))
    app.add_handler(
        MessageHandler(filters.ALL & (~filters.COMMAND), forward_messages)
    )

    # Keep-alive HTTP server
    flask_app = Flask('keep_alive')
    threading.Thread(
        target=lambda: flask_app.run(
            host='0.0.0.0',
            port=int(os.environ.get('PORT', 5000))
        ),
        daemon=True
    ).start()

    app.run_polling()

if __name__ == '__main__':
    main()
