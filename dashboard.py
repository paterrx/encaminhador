import streamlit as st
import os, json

# --- Paths fixos dentro do volume montado em /data ---
DATA_DIR      = "/data"
SUBS_FILE     = os.path.join(DATA_DIR, "subscriptions.json")
AUDIT_FILE    = os.path.join(DATA_DIR, "audit.json")

# --- Debug para ver o que realmente existe em /data ---
st.sidebar.header("🐞 DEBUG INFO")
st.sidebar.text(f"DATA_DIR exists? {os.path.exists(DATA_DIR)}")
if os.path.exists(DATA_DIR):
    st.sidebar.text(f"DATA_DIR contents: {os.listdir(DATA_DIR)}")
else:
    st.sidebar.error("Pasta /data não encontrada!")

def try_load(path, friendly):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        st.sidebar.success(f"✔️ Carregado {friendly}")
        return data
    except FileNotFoundError:
        st.sidebar.warning(f"⚠️ {friendly} não existe ({path})")
    except json.JSONDecodeError as e:
        st.sidebar.error(f"❌ JSON inválido em {friendly}: {e}")
    except Exception as e:
        st.sidebar.error(f"❌ Erro lendo {friendly}: {e}")
    return None

# Carrega inscrições e audit
subscriptions = try_load(SUBS_FILE, "subscriptions.json")
audit       = try_load(AUDIT_FILE, "audit.json")

# --- Interface principal ---
st.set_page_config(page_title="Dashboard Admin – Encaminhador", layout="wide")
st.title("📊 Dashboard Admin — Encaminhador")

# 1) Canais Originais (fixos)
st.subheader("🔒 Canais Originais (fixos)")
try:
    fixed = json.loads(os.environ.get("SOURCE_CHAT_IDS","[]"))
    for cid in fixed:
        st.markdown(f"- `{cid}`")
except Exception as e:
    st.error(f"Erro lendo SOURCE_CHAT_IDS: {e}")

st.markdown("---")

# 2) Canais Dinâmicos
st.subheader("✨ Canais Dinâmicos (inscritos pelos usuários)")
if subscriptions:
    any_sub = False
    for uid, gids in subscriptions.items():
        for gid in gids:
            st.markdown(f"- `{gid}` (usuário `{uid}`)")
            any_sub = True
    if not any_sub:
        st.info("Nenhuma inscrição dinâmica no momento.")
else:
    st.info("Nenhuma inscrição dinâmica no momento.")

st.markdown("---")

# 3) Audit Trail
st.subheader("📝 Audit Trail (últimos 50 eventos)")
if audit and isinstance(audit, list) and audit:
    for ev in audit[-50:]:
        st.markdown(f"- `{ev}`")
else:
    st.info("Nenhum evento de forwarding/audit registrado ainda.")
