# dashboard.py
# Dashboard Streamlit para admin do Encaminhador
# Mostra canais fixos e inscrições de usuários, permite remover com clique

import os
import json
import streamlit as st
from telethon import TelegramClient
from telethon.sessions import StringSession

# ── Configurações via ENV vars ────────────────────────────────────────────────
API_ID          = int(os.environ['TELEGRAM_API_ID'])
API_HASH        = os.environ['TELEGRAM_API_HASH']
SESSION_STRING  = os.environ['SESSION_STRING']
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS', '[]'))
# Exemplo: SOURCE_CHAT_IDS='[-1002460735067,-1002455542600,-1002794084735]'

# ── Inicializa Telethon client do admin ───────────────────────────────────────
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
client.start()

# ── Configurações de layout ───────────────────────────────────────────────────
st.set_page_config(page_title="Dashboard Admin - Encaminhador", layout="wide")
st.title("📊 Dashboard Admin — Encaminhador")

# ── Seção 1: canais fixos originais ─────────────────────────────────────────
st.header("Canais Originais (fixos)")
for gid in SOURCE_CHAT_IDS:
    try:
        chat = client.get_entity(gid)
        title = chat.title or chat.username or str(gid)
    except Exception as e:
        title = f"<Erro ao obter título: {e}>"
    st.write(f"• **{title}** — `{gid}`")

st.divider()

# ── Seção 2: inscrições de usuários ─────────────────────────────────────────
st.header("Inscrições de Usuários")
subs_file = 'subscriptions.json'

# Carrega JSON de inscrições
if not os.path.exists(subs_file):
    st.info("Nenhuma inscrição de usuário ainda.")
else:
    with open(subs_file, 'r', encoding='utf-8') as f:
        subs = json.load(f)

    if not subs:
        st.info("Nenhuma inscrição de usuário ainda.")
    else:
        # Para cada usuário, lista grupos inscritos
        for uid, lst in subs.items():
            st.subheader(f"👤 Usuário `{uid}`")
            for gid in lst:
                cols = st.columns([8, 1])
                try:
                    chat = client.get_entity(gid)
                    name = chat.title or chat.username or str(gid)
                except:
                    name = str(gid)
                cols[0].markdown(f"• **{name}** — `{gid}`")
                # Botão para remover inscrição
                if cols[1].button("❌", key=f"{uid}-{gid}"):
                    new_lst = [g for g in lst if g != gid]
                    if new_lst:
                        subs[uid] = new_lst
                    else:
                        subs.pop(uid)
                    # Salva com encoding
                    with open(subs_file, 'w', encoding='utf-8') as wf:
                        json.dump(subs, wf, indent=2)
                    # Recarrega a página
                    st.experimental_rerun()  # type: ignore[attr-defined]

# ── Barra lateral ─────────────────────────────────────────────────────────────
st.sidebar.markdown("---")
if st.sidebar.button("🔄 Recarregar"):
    st.experimental_rerun()  # type: ignore[attr-defined]
