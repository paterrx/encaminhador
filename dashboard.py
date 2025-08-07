import os
import json
import requests
import streamlit as st

# URL do seu serviço Flask (verifique WORKER_URL em Vars do Railway)
WEB_URL = os.environ.get('WORKER_URL', 'http://localhost:5000')
REQUEST_TIMEOUT = 5  # segundos

# --- Carrega inscrições dinâmicas via API Flask ---
try:
    resp = requests.get(f"{WEB_URL}/dump_subs", timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    SUBS = resp.json()
except Exception:
    SUBS = {}

# --- Carrega audit trail via API Flask ---
try:
    resp = requests.get(f"{WEB_URL}/dump_audit", timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    AUDIT = resp.json()
except Exception:
    AUDIT = []

# --- Canais fixos da ENV ---
try:
    FIXED = json.loads(os.environ.get('SOURCE_CHAT_IDS', '[]'))
except json.JSONDecodeError:
    FIXED = []

# --- UI Streamlit ---
st.set_page_config(
    page_title="Dashboard Admin — Encaminhador",
    layout="wide"
)
st.title("🚀 Dashboard Admin — Encaminhador")

st.header("🔒 Canais Fixos")
if not FIXED:
    st.info("Nenhum canal fixo.")
else:
    for cid in FIXED:
        st.write(f"- `{cid}`")

st.markdown("---")
st.header("✨ Canais Dinâmicos (inscritos pelos usuários)")
dynamic_ids = sorted({g for lst in SUBS.values() for g in lst})
if not dynamic_ids:
    st.info("Nenhuma inscrição dinâmica no momento.")
else:
    for cid in dynamic_ids:
        st.write(f"- `{cid}`")

st.markdown("---")
st.header("📋 Audit Trail (últimos 50 eventos)")
if not AUDIT:
    st.info("Nenhum evento registrado ainda.")
else:
    # exibe apenas os últimos 50
    for ev in AUDIT[-50:]:
        timestamp = ev.get('when', '')
        tipo      = ev.get('type', '')
        cid       = ev.get('cid', '')
        status    = ev.get('ok', '')
        st.write(f"- {timestamp} · **{tipo}** · `{cid}` · `{status}`")
