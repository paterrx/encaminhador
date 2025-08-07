import os
import json
import streamlit as st

# ─── Paths persistidos em volume Docker (/data) ───────────────────────────────
DATA_DIR    = '/data'
SUBS_FILE   = os.path.join(DATA_DIR, 'subscriptions.json')
AUDIT_FILE  = os.path.join(DATA_DIR, 'audit.json')

# ─── Fixed channels vêm da env SOURCE_CHAT_IDS ───────────────────────────────
try:
    FIXED_CHANNELS = json.loads(os.environ.get('SOURCE_CHAT_IDS', '[]'))
except json.JSONDecodeError:
    FIXED_CHANNELS = []

# ─── load helpers ─────────────────────────────────────────────────────────────
def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return default

# ─── Dados ────────────────────────────────────────────────────────────────────
subscriptions = load_json(SUBS_FILE, {})  # { user_id_str: [chat_id, ...], ... }
audit_events  = load_json(AUDIT_FILE, [])

# Dinâmicos: união de todos os chat_ids inscritos pelos usuários
dynamic_set = set()
for lst in subscriptions.values():
    for cid in lst:
        dynamic_set.add(cid)
DYNAMIC_CHANNELS = sorted(dynamic_set)

# ─── UI ──────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Dashboard Admin – Encaminhador", layout="wide")
st.title("📊 Dashboard Admin — Encaminhador")

# Fixed
st.subheader("🔒 Canais Originais (fixos)")
if FIXED_CHANNELS:
    for cid in FIXED_CHANNELS:
        st.markdown(f"- `{cid}`")
else:
    st.info("Nenhum canal fixo configurado em SOURCE_CHAT_IDS.")

st.markdown("---")

# Dinâmicos
st.subheader("✨ Canais Dinâmicos (inscritos pelos usuários)")
if DYNAMIC_CHANNELS:
    for cid in DYNAMIC_CHANNELS:
        st.markdown(f"- `{cid}`")
else:
    st.info("Nenhuma inscrição dinâmica no momento.")

st.markdown("---")

# Audit Trail
st.subheader("📝 Audit Trail (últimos 50 eventos)")
if not audit_events:
    st.info("Nenhum evento de forwarding/audit registrado ainda.")
else:
    # Só exibimos três campos pra resumir: hora, chat_id, status
    display = []
    for ev in audit_events[-50:]:
        ts = ev.get("ts", "")[:19].replace("T", " ")
        display.append({
            "Hora"    : ts,
            "Chat ID" : ev.get("cid", ""),
            "Status"  : ev.get("status", "")
        })
    st.table(display)
