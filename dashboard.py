import streamlit as st
import json
import os

# ─── Caminhos ────────────────────────────────────────────────────────────────
PROJECT_ROOT  = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH   = os.path.join(PROJECT_ROOT, 'config.json')
CHANNELS_PATH = os.path.join(PROJECT_ROOT, 'data', 'channels.json')
AUDIT_PATH    = os.path.join(PROJECT_ROOT, 'data', 'audit.json')

# ─── Helpers de JSON ─────────────────────────────────────────────────────────
def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return default

def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2)

# ─── Carrega dados ───────────────────────────────────────────────────────────
channels_map = load_json(CHANNELS_PATH, {})                              # { name: id, ... }
monitored    = load_json(CONFIG_PATH, {}).get('telegram_channel_ids', []) # [ id, ... ]
audit_events = load_json(AUDIT_PATH, [])

# ─── UI ─────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Dashboard de Canais & Audit", layout="wide")
st.title("🚀 Gerenciador de Canais")

# Sidebar: canais atualmente monitorados
st.sidebar.header("Canais Monitorados Atualmente")
if monitored:
    for cid in monitored:
        name = next((n for n, i in channels_map.items() if i == cid), None)
        label = f"{name} — `{cid}`" if name else f"`{cid}`"
        st.sidebar.success(label)
else:
    st.sidebar.info("Nenhum canal monitorado ainda.")

st.sidebar.divider()
st.sidebar.markdown(
    "Para alterar a lista, selecione abaixo e clique em **Salvar**.\n\n"
    "Depois, atualize a página manualmente."
)

# Seleção de canais
st.subheader("Selecione os Canais para Monitorar")
channel_names = sorted(channels_map.keys())
default_sel    = [n for n, i in channels_map.items() if i in monitored]
selected_names = st.multiselect("Canais disponíveis", channel_names, default=default_sel)

if st.button("Salvar Alterações"):
    new_ids = [channels_map[name] for name in selected_names]
    save_json(CONFIG_PATH, {'telegram_channel_ids': new_ids})
    st.success("✅ Configurações salvas! Atualize a página para aplicar.")

st.markdown("---")

# Audit Trail
st.subheader("📝 Audit Trail (últimos 50 eventos)")
if not audit_events:
    st.info("Nenhum evento registrado ainda.")
else:
    # mostra apenas alguns campos e os últimos 50
    display = []
    for ev in audit_events[-50:]:
        display.append({
            "Hora"     : ev.get("ts", "")[:19].replace("T"," "),
            "Chat ID"  : ev.get("cid", ""),
            "Status"   : ev.get("status", ""),
        })
    st.table(display)
