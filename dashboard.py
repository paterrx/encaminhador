import os, json, streamlit as st

DATA_DIR   = "/data"
SUBS_FILE  = os.path.join(DATA_DIR,"subscriptions.json")
AUDIT_FILE = os.path.join(DATA_DIR,"audit.json")

def load_or_empty(path,dtype):
    try:
        return json.load(open(path,"r",encoding="utf-8"))
    except:
        return dtype()

subscriptions = load_or_empty(SUBS_FILE, dict)
audit_events   = load_or_empty(AUDIT_FILE, list)
fixed_ids      = json.loads(os.environ.get("SOURCE_CHAT_IDS","[]"))

st.set_page_config(page_title="Dashboard Admin — Encaminhador", layout="wide")
st.title("📊 Dashboard Admin — Encaminhador")

st.subheader("🔒 Canais Fixos")
if fixed_ids:
    for c in fixed_ids:
        st.markdown(f"- `{c}`")
else:
    st.info("Nenhum canal fixo.")

st.markdown("---")
st.subheader("✨ Canais Dinâmicos (inscritos pelos usuários)")
if subscriptions:
    for uid, lst in subscriptions.items():
        st.markdown(f"👤 `{uid}` → " + ", ".join(f"`{g}`" for g in lst))
else:
    st.info("Nenhuma inscrição dinâmica.")

st.markdown("---")
st.subheader("📝 Audit Trail (últimos 50 eventos)")
if audit_events:
    for ev in audit_events[-50:]:
        t = ev.get("time","")
        c = ev.get("chat_id","")
        s = ev.get("status","")
        st.markdown(f"- `{t}` | chat `{c}` → {s}")
else:
    st.info("Nenhum evento registrado ainda.")
