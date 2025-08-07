import streamlit as st
import json
import os

DATA_DIR = "/data"
CHANNELS_PATH     = os.path.join(DATA_DIR, "channels.json")
SUBSCRIPTIONS_PATH= os.path.join(DATA_DIR, "subscriptions.json")
AUDIT_PATH        = os.path.join(DATA_DIR, "audit.json")

# --- UTILITÁRIOS DE ARQUIVO ---

def ensure_file(path, default):
    """Se path não existir, cria com o valor default e retorna default."""
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=2)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# --- CARREGA / CRIA ARQUIVOS ---

# 1) Channels: pega da ENV SOURCE_CHAT_IDS
source_ids = os.environ.get("SOURCE_CHAT_IDS", "[]")
try:
    channels_list = json.loads(source_ids)
except:
    channels_list = []
# cria channels.json se não existir
channels_map = {str(cid): cid for cid in channels_list}
# gravar se novo
ensure_file(CHANNELS_PATH, channels_map)

# 2) subscriptions.json começa vazio por usuário
subscriptions = ensure_file(SUBSCRIPTIONS_PATH, {})

# 3) audit.json começa vazio
audit_events = ensure_file(AUDIT_PATH, [])

# --- STREAMLIT UI ---

st.set_page_config(page_title="Dashboard Admin — Encaminhador", layout="wide")
st.sidebar.header("🔧 DEBUG INFO")
st.sidebar.write("DATA_DIR exists?", os.path.isdir(DATA_DIR))
st.sidebar.write("DATA_DIR contents:", os.listdir(DATA_DIR))
st.sidebar.warning(f"`subscriptions.json` não existe\n({SUBSCRIPTIONS_PATH})" if not os.path.exists(SUBSCRIPTIONS_PATH) else "")
st.sidebar.warning(f"`audit.json` não existe\n({AUDIT_PATH})" if not os.path.exists(AUDIT_PATH) else "")

st.title("🚀 Dashboard Admin — Encaminhador")

# Canais Originais
st.subheader("🔒 Canais Originais (fixos)")
for cid in channels_map.values():
    st.write(f"- `{cid}`")

# Inscrições Dinâmicas
st.subheader("✨ Canais Dinâmicos (inscritos pelos usuários)")
if not subscriptions:
    st.info("Nenhuma inscrição dinâmica no momento.")
else:
    for user_id, group_ids in subscriptions.items():
        st.write(f"👤 `{user_id}` → {', '.join(f'`{g}`' for g in group_ids)}")

# Audit Trail
st.subheader("📜 Audit Trail (últimos 50 eventos)")
if not audit_events:
    st.info("Nenhum evento de forwarding/audit registrado ainda.")
else:
    for ev in audit_events[-50:]:
        ts = ev.get("time", "")
        status = ev.get("status", "")
        cid = ev.get("chat_id", "")
        st.write(f"- `{ts}` | chat `{cid}` → {status}")
