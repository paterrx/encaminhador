# dashboard.py
import os, json, streamlit as st

# caminhos
SUBS_FILE = 'subscriptions.json'

# carrega
def load_subs():
    if not os.path.exists(SUBS_FILE):
        return {}
    return json.load(open(SUBS_FILE,'r',encoding='utf-8'))

# UI
st.set_page_config(page_title="Dashboard Admin – Encaminhador", layout="wide")
st.title("📊 Dashboard Admin — Encaminhador")

all_fixed = json.loads(os.environ.get('SOURCE_CHAT_IDS','[]'))
subs_map  = load_subs()
dynamic_ids = sorted({gid for lst in subs_map.values() for gid in lst})

st.header("🔒 Canais Originais (fixos)")
if not all_fixed:
    st.info("Nenhum canal fixo.")
else:
    for cid in all_fixed:
        st.write(f"• `{cid}`")

st.markdown("---")
st.header("✨ Canais Dinâmicos (inscritos)")
if not dynamic_ids:
    st.info("Nenhuma inscrição.")
else:
    st.write(dynamic_ids)
