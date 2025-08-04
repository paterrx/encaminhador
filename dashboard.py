# dashboard.py
import os, json, requests, streamlit as st

# Se houver subscriptions.json local, é fallback, mas idealmente
# usamos o endpoint HTTP do worker
SUBS_FILE = 'subscriptions.json'
WORKER_URL = os.environ.get('WORKER_URL')

def load_subs():
    # 1) Tenta buscar do worker
    if WORKER_URL:
        try:
            resp = requests.get(f"{WORKER_URL}/dump_subs", timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            st.warning(f"⚠️ Falha ao buscar do worker: {e}")
    # 2) Fallback local
    if os.path.exists(SUBS_FILE):
        try:
            with open(SUBS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

# --- Construção da UI ---
st.set_page_config(page_title="Dashboard Admin – Encaminhador", layout="wide")
st.title("📊 Dashboard Admin — Encaminhador")

# Canais fixos (definidos no ENV SOURCE_CHAT_IDS)
fixed = json.loads(os.environ.get('SOURCE_CHAT_IDS', '[]'))
# Carrega inscrições (dinâmicas) do worker ou local
subs_map = load_subs()
# Extrai IDs únicos de todos os inscritos
dynamic_ids = sorted({gid for lst in subs_map.values() for gid in lst})

st.header("🔒 Canais Originais (fixos)")
if not fixed:
    st.info("Nenhum canal fixo configurado.")
else:
    for cid in fixed:
        st.write(f"• `{cid}`")

st.markdown("---")
st.header("✨ Canais Dinâmicos (inscritos)")
if not dynamic_ids:
    st.info("Nenhuma inscrição de usuário no momento.")
else:
    for cid in dynamic_ids:
        st.write(f"• `{cid}`")
