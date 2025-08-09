# dashboard.py
import os, json, requests, streamlit as st

# Config de onde buscar (usa o Flask do main)
WEB_URL = os.environ.get('WORKER_URL', 'http://localhost:8080')

st.set_page_config(page_title="Dashboard Admin — Encaminhador", layout="wide")
st.title("📊 Dashboard Admin — Encaminhador")

# Helpers HTTP (com timeout pra não travar a página)
def get_json(path, default):
    try:
        r = requests.get(f"{WEB_URL}{path}", timeout=5)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return default

# Carregamentos
fixed_ids = json.loads(os.environ.get('SOURCE_CHAT_IDS', '[]'))
subs_map  = get_json('/dump_subs', {})
sessions  = get_json('/dump_sessions', {})   # novo endpoint sanitizado

# ───────────────── Canais Fixos ─────────────────
st.header("🔒 Canais Fixos")
if not fixed_ids:
    st.info("Nenhum canal fixo configurado.")
else:
    for cid in fixed_ids:
        st.code(str(cid))

st.markdown("---")

# ───────────────── Dinâmicos ─────────────────
st.header("✨ Canais Dinâmicos (inscritos pelos usuários)")
dyn = sorted({gid for lst in subs_map.values() for gid in lst}) if subs_map else []
if not dyn:
    st.info("Nenhuma inscrição dinâmica.")
else:
    st.write(dyn)

st.markdown("---")

# ───────────────── Sessões salvas ─────────────────
st.header("🔑 Sessões salvas (por usuário)")
if not sessions:
    st.info("Nenhuma String Session salva.")
else:
    # monta uma grade com informações úteis
    rows = []
    for uid, meta in sessions.items():
        rows.append({
            "User ID": int(uid),
            "Assinaturas?": "Sim" if meta.get("has_subs") else "Não",
            "Preview": meta.get("preview", "…"),
            "Fingerprint": meta.get("fingerprint", ""),
            "Tamanho": meta.get("length", 0),
        })
    # ordena por User ID
    rows.sort(key=lambda r: r["User ID"])
    st.dataframe(rows, use_container_width=True)

st.markdown("---")

# ───────────────── Audit Trail ─────────────────
st.header("🧾 Audit Trail (últimos eventos)")
audit = get_json('/dump_audit', [])
if not audit:
    st.info("Nenhum evento registrado ainda.")
else:
    # Mostra só alguns campos se existirem
    def fmt(ev):
        ts = ev.get('ts')
        kind = ev.get('kind') or ev.get('type') or 'event'
        msg = ev.get('msg') or ev.get('note') or ''
        return f"{ts}  •  {kind}  •  {msg}"
    st.write("\n".join(fmt(e) for e in audit[-50:]))
