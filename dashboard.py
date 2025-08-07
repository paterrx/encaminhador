import streamlit as st
import os, json

# --- Caminhos fixos dentro do volume montado em /data ---
DATA_DIR       = "/data"
CHANNELS_FILE  = os.path.join(DATA_DIR, "channels.json")       # lista de canais fixos (se existir)
SUBS_FILE      = os.path.join(DATA_DIR, "subscriptions.json")  # inscrições dinâmicas
AUDIT_FILE     = os.path.join(DATA_DIR, "audit.json")          # histórico de forwarding

# --- DEBUG na sidebar ---
st.sidebar.header("🐞 DEBUG INFO")
st.sidebar.text(f"DATA_DIR exists? {os.path.exists(DATA_DIR)}")
if os.path.exists(DATA_DIR):
    st.sidebar.text(f"DATA_DIR contents: {os.listdir(DATA_DIR)}")
else:
    st.sidebar.error("Pasta /data não encontrada!")

def try_load(path, desc):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        st.sidebar.success(f"✔️ Carregado {desc}: {list(data.keys()) if isinstance(data, dict) else type(data)}")
        return data
    except FileNotFoundError:
        st.sidebar.warning(f"⚠️ {desc} não existe ({path})")
    except json.JSONDecodeError as e:
        st.sidebar.error(f"❌ JSON inválido em {desc}: {e}")
    except Exception as e:
        st.sidebar.error(f"❌ Erro lendo {desc}: {e}")
    return None

channels_map = try_load(CHANNELS_FILE, "channels.json")
subscriptions = try_load(SUBS_FILE, "subscriptions.json")
audit_trail = try_load(AUDIT_FILE, "audit.json")

# --- Interface principal ---
st.set_page_config(page_title="Dashboard Admin – Encaminhador", layout="wide")
st.title("📊 Dashboard Admin — Encaminhador")

# 1) Canais Originais (fixos) vindos do ENV SOURCE_CHAT_IDS
st.subheader("🔒 Canais Originais (fixos)")
try:
    raw = os.environ.get("SOURCE_CHAT_IDS", "[]")
    fixed_ids = json.loads(raw)
    for cid in fixed_ids:
        st.markdown(f"- `{cid}`")
except Exception as e:
    st.error(f"Erro parseando SOURCE_CHAT_IDS: {e}")

st.markdown("---")

# 2) Canais Dinâmicos (inscritos pelos usuários)
st.subheader("✨ Canais Dinâmicos (inscritos pelos usuários)")
if subscriptions:
    any_sub = False
    for uid, gids in subscriptions.items():
        for gid in gids:
            st.markdown(f"- `{gid}` (user `{uid}`)")
            any_sub = True
    if not any_sub:
        st.info("Nenhuma inscrição dinâmica no momento.")
else:
    st.info("Nenhuma inscrição dinâmica no momento.")

st.markdown("---")

# 3) Audit Trail
st.subheader("📝 Audit Trail (últimos 50 eventos)")
if audit_trail and isinstance(audit_trail, list) and audit_trail:
    for ev in audit_trail[-50:]:
        st.markdown(f"- `{ev}`")
else:
    st.info("Nenhum evento de forwarding/audit registrado ainda.")
    
