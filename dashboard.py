import os
import json
import streamlit as st

# ── Configuração ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Dashboard Admin – Encaminhador", layout="wide")
st.title("📊 Dashboard Admin — Encaminhador")

# IDs iniciais vindos da ENV
SOURCE_CHAT_IDS = json.loads(os.environ.get('SOURCE_CHAT_IDS', '[]'))

# Carrega inscrições de usuários
SUBS_FILE = 'subscriptions.json'
if os.path.exists(SUBS_FILE):
    with open(SUBS_FILE, 'r', encoding='utf-8') as f:
        subscriptions = json.load(f)
else:
    subscriptions = {}

# Apura só os IDs únicos dinâmicos
dynamic_ids = sorted({cid for lst in subscriptions.values() for cid in lst})

# ── Seção 1: Canais Iniciais ──────────────────────────────────────────────────
st.header("🔒 Canais Originais (fixos)")
if not SOURCE_CHAT_IDS:
    st.info("Nenhum canal fixo configurado.")
else:
    for cid in SOURCE_CHAT_IDS:
        st.write(f"• `{cid}`")

st.markdown("---")

# ── Seção 2: Canais Dinâmicos ─────────────────────────────────────────────────
st.header("✨ Canais Dinâmicos (inscritos pelos usuários)")
if not dynamic_ids:
    st.info("Nenhuma inscrição de usuário no momento.")
else:
    # multiselect para decidir quais IDs continuam ativos
    selected = st.multiselect(
        "Selecione os canais que devem continuar monitorados:",
        options=dynamic_ids,
        default=dynamic_ids
    )

    if st.button("💾 Salvar alterações"):
        # filtra subscriptions para manter só os selected
        new_subs = {}
        for user_id, lst in subscriptions.items():
            filtered = [cid for cid in lst if cid in selected]
            if filtered:
                new_subs[user_id] = filtered

        # grava de volta
        with open(SUBS_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_subs, f, indent=2)

        st.success("Configurações salvas! Atualize a página para ver o resultado.")

st.sidebar.markdown("---")
st.sidebar.write("Para aplicar mudanças, recarregue (F5) esta página.")
