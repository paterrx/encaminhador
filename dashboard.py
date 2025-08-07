# dashboard.py
import streamlit as st, os, json

DATA_DIR   = "/data"
SUBS_FILE  = os.path.join(DATA_DIR, "subscriptions.json")
AUDIT_FILE = os.path.join(DATA_DIR, "audit.json")

# Debug
st.sidebar.header("🐞 DEBUG INFO")
st.sidebar.text(f"DATA_DIR exists? {os.path.exists(DATA_DIR)}")
if os.path.exists(DATA_DIR):
    st.sidebar.text(f"DATA_DIR contents: {os.listdir(DATA_DIR)}")
else:
    st.sidebar.error("❌ /data não encontrada!")

def try_load(path, name):
    try:
        with open(path,"r",encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        st.sidebar.warning(f"⚠️ {name} não existe ({path})")
    except Exception as e:
        st.sidebar.error(f"❌ Erro em {name}: {e}")
    return None

subscriptions = try_load(SUBS_FILE, "subscriptions.json")
audit         = try_load(AUDIT_FILE,  "audit.json")

st.set_page_config(page_title="Dashboard Encaminhador", layout="wide")
st.title("📊 Dashboard Admin — Encaminhador")

# Fixos
st.subheader("🔒 Canais Originais (fixos)")
for cid in json.loads(os.environ.get("SOURCE_CHAT_IDS","[]")):
    st.markdown(f"- `{cid}`")

st.markdown("---")
# Dinâmicos
st.subheader("✨ Canais Dinâmicos")
if subscriptions:
    count=0
    for uid,gids in subscriptions.items():
        for gid in gids:
            st.markdown(f"- `{gid}` (usuário `{uid}`)")
            count+=1
    if not count:
        st.info("Nenhuma inscrição dinâmica no momento.")
else:
    st.info("Nenhuma inscrição dinâmica no momento.")

st.markdown("---")
# Audit
st.subheader("📝 Audit Trail (últimos 50)")
if isinstance(audit,list) and audit:
    for ev in audit[-50:]:
        st.markdown(f"- `{ev}`")
else:
    st.info("Nenhum evento registrado ainda.")
