"""IHM web (bibliotheque standard uniquement) : visualisation des tags,
pilotage manuel, ajout / suppression de variables et details de connexion."""

from __future__ import annotations

import json
import logging
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import generators

log = logging.getLogger("web")

# Correspondance type interne -> data_type Telegraf
TELEGRAF_TYPES = {
    "int16": "INT16", "uint16": "UINT16",
    "int32": "INT32", "uint32": "UINT32",
    "float32": "FLOAT32-IEEE", "float64": "FLOAT64-IEEE",
}


def byte_order(words: int, word_order: str) -> str:
    big = not str(word_order).lower().startswith("little")
    if words <= 1:
        return "AB" if big else "BA"
    if words == 2:
        return "ABCD" if big else "CDAB"
    return "ABCDEFGH" if big else "GHEFCDAB"


def telegraf_config(state: dict) -> str:
    """Genere la section [[inputs.modbus]] correspondant a tous les tags."""
    mb = state.get("servers", {}).get("modbus") or {}
    host = mb.get("host") or "0.0.0.0"
    if host in ("0.0.0.0", ""):
        host = "IP_DU_PC"
    order = mb.get("word_order", "big")

    lines = [
        "# Genere par le simulateur DataTest.",
        "# A coller dans /etc/telegraf/telegraf.conf",
        "",
        "[[inputs.modbus]]",
        '  name = "simulateur"',
        f'  controller = "tcp://{host}:{mb.get("port", 502)}"',
        f'  device_id = {mb.get("unit_id", 1)}',
        '  timeout = "1s"',
        '  interval = "5s"',
        '  configuration_type = "register"',
        "  input_registers = []",
        "  discrete_inputs = []",
        "",
    ]

    coils = [t for t in state["tags"] if t["modbus"] and t["dtype"] == "bool"]
    regs = [t for t in state["tags"] if t["modbus"] and t["dtype"] != "bool"]

    if coils:
        lines.append("  # --- Booleens (coils, FC1) ---")
        for t in coils:
            lines += [
                "  [[inputs.modbus.coils]]",
                f'    name = "{t["name"]}"',
                f'    address = [{t["address"]}]',
                "",
            ]
    else:
        lines += ["  coils = []", ""]

    if regs:
        lines.append("  # --- Valeurs numeriques (holding registers, FC3) ---")
        for t in regs:
            addrs = ", ".join(str(t["address"] + i) for i in range(t["words"]))
            scale = 1.0 / t["scale"] if t.get("scale") else 1.0
            lines += [
                "  [[inputs.modbus.holding_registers]]",
                f'    name = "{t["name"]}"',
                f"    address = [{addrs}]",
                f'    byte_order = "{byte_order(t["words"], order)}"',
                f'    data_type = "{TELEGRAF_TYPES.get(t["dtype"], "UINT16")}"',
                f"    scale = {scale:g}",
                "",
            ]
    return "\n".join(lines) + "\n" + telegraf_s7(state)


S7_CODE = {"bool": "X", "int16": "I", "uint16": "W", "int32": "DI",
           "uint32": "DW", "float32": "R", "float64": "LR"}


def telegraf_s7(state: dict) -> str:
    """Genere la section [[inputs.s7comm]] correspondant a tous les tags."""
    s7 = state.get("servers", {}).get("s7") or {}
    if not s7:
        return ""
    host = s7.get("host") or ""
    if host in ("0.0.0.0", ""):
        host = "IP_DU_PC"
    db = s7.get("db_number", 1)

    fields = []
    for t in state["tags"]:
        if not t.get("s7") or t.get("s7_offset") is None:
            continue
        if t["dtype"] == "bool":
            addr = f"DB{db}.X{t['s7_offset']}.{t['s7_bit']}"
        else:
            addr = f"DB{db}.{S7_CODE.get(t['dtype'], 'W')}{t['s7_offset']}"
        fields.append(f'    {{ name="{t["name"]}", address="{addr}" }},')

    if not fields:
        return ""
    return "\n".join([
        "",
        "# --- S7comm (le PC se fait passer pour un automate Siemens) ---",
        "[[inputs.s7comm]]",
        f'  server = "{host}:{s7.get("port", 102)}"',
        f'  rack = {s7.get("rack", 0)}',
        f'  slot = {s7.get("slot", 1)}',
        '  timeout = "10s"',
        '  interval = "5s"',
        "",
        "  [[inputs.s7comm.metric]]",
        '    name = "simulateur_s7"',
        "    fields = [",
        *fields,
        "    ]",
        "",
    ])


PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Simulateur de donnees</title>
<style>
 :root {
   color-scheme: light dark;
   --bg:#f4f6f8; --surface:#ffffff; --surface-2:#f8fafc; --raised:#ffffff;
   --bd:#e2e6ec; --bd-strong:#cdd4de;
   --text:#151a21; --dim:#69737f;
   --accent:#2563eb; --accent-soft:#2563eb14;
   --ok:#0f7b52; --ok-soft:#0f7b5218;
   --warn:#b45309; --warn-soft:#b4530918;
   --danger:#c02a24; --danger-soft:#c02a2414;
   --shadow:0 1px 2px #0f172a0d, 0 8px 24px #0f172a0a;
   --mono: ui-monospace, "Cascadia Mono", Consolas, monospace;
 }
 @media (prefers-color-scheme: dark) {
  :root {
   --bg:#0d1117; --surface:#161b22; --surface-2:#1a2029; --raised:#1c232c;
   --bd:#2a313b; --bd-strong:#3a434f;
   --text:#e6edf3; --dim:#8b949e;
   --accent:#589bff; --accent-soft:#589bff1f;
   --ok:#3fb950; --ok-soft:#3fb95020;
   --warn:#d29922; --warn-soft:#d2992220;
   --danger:#f85149; --danger-soft:#f8514920;
   --shadow:0 1px 2px #0006, 0 8px 24px #00000040;
  }
 }
 * { box-sizing:border-box; }
 body { font-family:system-ui,"Segoe UI",Roboto,sans-serif; margin:0;
        padding:1.4rem clamp(.8rem,3vw,2rem) 3rem; background:var(--bg);
        color:var(--text); font-size:14px; line-height:1.45; }

 /* ---- en-tete ---- */
 .topbar { display:flex; align-items:flex-start; justify-content:space-between;
           gap:1rem; flex-wrap:wrap; margin-bottom:1rem; }
 .brand { display:flex; align-items:center; gap:.75rem; }
 .logo { width:2.2rem; height:2.2rem; border-radius:9px; flex:none;
         display:grid; place-items:center; font-size:1rem; color:#fff;
         background:linear-gradient(135deg,var(--accent),#7c3aed);
         box-shadow:var(--shadow); }
 h1 { font-size:1.05rem; margin:0; letter-spacing:-.01em; }
 .sub { color:var(--dim); font-size:.8rem; }
 .status { display:flex; align-items:center; gap:.45rem; font-size:.78rem;
           color:var(--dim); background:var(--surface); border:1px solid var(--bd);
           border-radius:999px; padding:.3rem .7rem; box-shadow:var(--shadow); }
 .dot { width:.5rem; height:.5rem; border-radius:50%; background:var(--ok);
        box-shadow:0 0 0 3px var(--ok-soft); }
 .dot.ko { background:var(--danger); box-shadow:0 0 0 3px var(--danger-soft); }

 /* ---- bandeau des serveurs ---- */
 .endpoints { display:flex; gap:.5rem; flex-wrap:wrap; margin-bottom:1rem; }
 .ep { background:var(--surface); border:1px solid var(--bd); border-radius:9px;
       padding:.45rem .7rem; box-shadow:var(--shadow); min-width:0; }
 .ep b { display:block; font-size:.68rem; text-transform:uppercase;
         letter-spacing:.06em; color:var(--dim); font-weight:600; }
 .ep span { font-family:var(--mono); font-size:.78rem; }

 /* ---- barre d'actions ---- */
 .bar { display:flex; gap:.45rem; flex-wrap:wrap; align-items:center;
        margin-bottom:.9rem; }
 button { cursor:pointer; border:1px solid var(--bd-strong); background:var(--surface);
          color:var(--text); border-radius:8px; padding:.4rem .75rem;
          font-size:.8rem; font-family:inherit; font-weight:500;
          transition:background .12s, border-color .12s, color .12s; }
 button:hover { border-color:var(--accent); color:var(--accent);
                background:var(--accent-soft); }
 button:active { transform:translateY(1px); }
 button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
 button.primary:hover { filter:brightness(1.08); color:#fff; }
 button.danger { border-color:var(--danger); color:var(--danger); }
 button.danger:hover { background:var(--danger-soft); color:var(--danger);
                       border-color:var(--danger); }
 button.mini { padding:.15rem .45rem; font-size:.7rem; border-radius:6px; }
 .grow { flex:1; }

 /* ---- tableau ---- */
 .card { background:var(--surface); border:1px solid var(--bd); border-radius:12px;
         box-shadow:var(--shadow); overflow:hidden; }
 .tablewrap { overflow-x:auto; }
 table { border-collapse:separate; border-spacing:0; width:100%; font-size:.85rem; }
 thead th { position:sticky; top:0; z-index:2; background:var(--surface-2);
            border-bottom:1px solid var(--bd); text-align:left;
            padding:.6rem .75rem; font-size:.68rem; font-weight:600;
            text-transform:uppercase; letter-spacing:.06em; color:var(--dim);
            white-space:nowrap; }
 td { padding:.55rem .75rem; border-bottom:1px solid var(--bd);
      vertical-align:middle; }
 tr.tag { cursor:pointer; transition:background .12s; }
 tr.tag:hover { background:var(--accent-soft); }
 tr.tag.open { background:var(--surface-2); }
 tbody tr:last-child td { border-bottom:none; }
 .tagname { font-weight:600; }
 .tagdesc { color:var(--dim); font-size:.75rem; }
 code { font-family:var(--mono); font-size:.75rem; }
 .chip { display:inline-block; font-family:var(--mono); font-size:.72rem;
         background:var(--surface-2); border:1px solid var(--bd);
         border-radius:6px; padding:.1rem .4rem; white-space:nowrap; }
 .gen { color:var(--dim); font-size:.75rem; font-family:var(--mono);
        display:block; max-width:22rem; overflow:hidden; text-overflow:ellipsis;
        white-space:nowrap; }

 /* ---- valeurs ---- */
 td.val { white-space:nowrap; width:11rem; }
 .num { font-variant-numeric:tabular-nums; font-weight:650; font-size:.95rem;
        letter-spacing:-.01em; }
 .unit { color:var(--dim); font-weight:400; font-size:.75rem; margin-left:.3rem; }
 .spark { display:block; width:100px; height:24px; margin-top:.1rem;
          color:var(--accent); opacity:.65; }
 .spark polyline { fill:none; stroke:currentColor; stroke-width:1.5;
                   vector-effect:non-scaling-stroke; stroke-linejoin:round; }
 .pill { display:inline-flex; align-items:center; gap:.35rem; font-size:.72rem;
         font-weight:600; letter-spacing:.04em; border-radius:999px;
         padding:.15rem .55rem; border:1px solid transparent; }
 .pill::before { content:""; width:.45rem; height:.45rem; border-radius:50%;
                 background:currentColor; }
 .pill.on { color:var(--ok); background:var(--ok-soft); border-color:var(--ok); }
 .pill.off { color:var(--dim); background:var(--surface-2); border-color:var(--bd-strong); }
 .badge { font-size:.65rem; text-transform:uppercase; letter-spacing:.05em;
          font-weight:700; color:var(--warn); background:var(--warn-soft);
          border:1px solid var(--warn); border-radius:5px; padding:0 .3rem;
          margin-left:.35rem; }

 /* ---- formulaires ---- */
 input, select, textarea { padding:.35rem .5rem; border-radius:7px;
         border:1px solid var(--bd-strong); background:var(--raised);
         color:var(--text); font-family:inherit; font-size:.82rem; }
 input:focus, select:focus, textarea:focus { outline:2px solid var(--accent-soft);
         border-color:var(--accent); }
 input.cmd { width:6.5rem; font-family:var(--mono); }
 input[type=checkbox] { accent-color:var(--accent); width:.95rem; height:.95rem; }

 /* ---- mode selection ---- */
 .sel-cell { display:none; width:2.2rem; }
 body.selecting .sel-cell { display:table-cell; }
 body.selecting tr.tag:hover { background:var(--accent-soft); }

 /* ---- panneau de detail ---- */
 tr.detail > td { background:var(--surface-2); padding:1rem 1.1rem 1.2rem; }
 .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
         gap:.7rem; }
 .panel { background:var(--raised); border:1px solid var(--bd); border-radius:10px;
          padding:.7rem .8rem; }
 .panel h4 { margin:0 0 .5rem; font-size:.68rem; text-transform:uppercase;
             letter-spacing:.06em; color:var(--dim); font-weight:700; }
 dl { margin:0; display:grid; grid-template-columns:auto 1fr; gap:.2rem .7rem;
      font-size:.78rem; align-items:baseline; }
 dt { color:var(--dim); white-space:nowrap; }
 dd { margin:0; font-family:var(--mono); word-break:break-all; }
 .snips { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
          gap:.7rem; margin-top:.8rem; }
 .snip { position:relative; }
 .snip h4 { margin:0 0 .3rem; font-size:.68rem; text-transform:uppercase;
            letter-spacing:.06em; color:var(--dim); font-weight:700; }
 .snip button { position:absolute; top:1.35rem; right:.4rem; }
 pre { font-family:var(--mono); font-size:.74rem; background:var(--raised);
       border:1px solid var(--bd); padding:.6rem .7rem; border-radius:8px;
       overflow-x:auto; margin:0; }
 .hint { color:var(--dim); font-size:.78rem; margin:.8rem .2rem 0; }
 .detail .hint { margin:.7rem 0 .8rem; }

 /* ---- boite de dialogue ---- */
 dialog { border:1px solid var(--bd); border-radius:14px; padding:0;
          max-width:580px; width:94%; background:var(--surface); color:var(--text);
          box-shadow:0 20px 60px #0f172a35; }
 dialog::backdrop { background:#0f172a66; backdrop-filter:blur(2px); }
 .dlg-head { padding:1rem 1.2rem .8rem; border-bottom:1px solid var(--bd); }
 .dlg-head h3 { margin:0; font-size:.95rem; }
 .dlg-body { padding:1rem 1.2rem; max-height:65vh; overflow-y:auto; }
 .dlg-foot { padding:.8rem 1.2rem; border-top:1px solid var(--bd);
             display:flex; justify-content:flex-end; gap:.5rem;
             background:var(--surface-2); border-radius:0 0 13px 13px; }
 .field { display:grid; grid-template-columns:10.5rem 1fr; gap:.6rem;
          align-items:center; margin-bottom:.55rem; }
 .field label { font-size:.8rem; color:var(--dim); }
 .field > input, .field > select, .field > textarea { width:100%; }
 .sep { border:none; border-top:1px solid var(--bd); margin:.9rem 0 .8rem; }
 .opts label { font-size:.8rem; display:inline-flex; align-items:center;
               gap:.3rem; margin-right:.8rem; }
 .err { color:var(--danger); font-size:.8rem; min-height:1.2em; }
 .chev { color:var(--dim); width:1.1rem; text-align:center;
         transition:transform .15s; display:inline-block; }
 tr.tag.open .chev { transform:rotate(90deg); }
 @media (max-width:640px) {
   .field { grid-template-columns:1fr; gap:.2rem; }
   .gen { max-width:12rem; }
 }
</style></head><body>

<header class="topbar">
  <div class="brand">
    <div class="logo">◆</div>
    <div>
      <h1>Simulateur de valeurs fictives</h1>
      <div class="sub" id="hdr">connexion...</div>
    </div>
  </div>
  <div class="status"><span class="dot" id="dot"></span><span id="status_txt">—</span></div>
</header>

<div class="endpoints" id="endpoints"></div>

<div class="bar">
  <button class="primary" onclick="openAdd()">+&nbsp; Ajouter une variable</button>
  <button id="btn_sel" onclick="toggleSelect(true)">Supprimer des variables</button>
  <button id="btn_del" class="danger" style="display:none"
          onclick="deleteSelected()">Supprimer la selection (0)</button>
  <button id="btn_cancel" style="display:none"
          onclick="toggleSelect(false)">Annuler</button>
  <span class="grow"></span>
  <button onclick="window.open('/api/telegraf','_blank')">Config Telegraf</button>
  <button onclick="saveCfg()">Enregistrer dans config.yaml</button>
  <span id="saved" class="sub"></span>
</div>

<div class="card"><div class="tablewrap">
<table><thead><tr>
 <th class="sel-cell"><input type="checkbox" id="sel_all" title="tout cocher"
     onclick="toggleAll(this.checked)"></th>
 <th style="width:1.6rem"></th><th>Tag</th><th>Type</th><th>Generateur</th>
 <th>Modbus</th><th>Valeur</th><th>Commande</th>
</tr></thead><tbody id="rows"></tbody></table>
</div></div>

<p class="hint">Cliquer sur une ligne pour voir les details de connexion des
  quatre protocoles. Les valeurs forcees (badge orange) ignorent le generateur
  jusqu'a « Liberer ». Un client Modbus, OPC UA, BACnet ou S7 peut aussi ecrire
  directement.</p>

<dialog id="dlg">
  <div class="dlg-head"><h3>Nouvelle variable</h3></div>
  <div class="dlg-body">
    <div class="field"><label>Nom</label>
      <input id="f_name" placeholder="ma_variable" autocomplete="off"></div>
    <div class="field"><label>Description</label>
      <input id="f_desc" placeholder="optionnel"></div>
    <div class="field"><label>Unite</label>
      <input id="f_unit" placeholder="optionnel, ex. degC"></div>
    <div class="field"><label>Type de donnee</label>
      <select id="f_dtype" onchange="fillGenerators()"></select></div>
    <div class="field"><label>Generateur</label>
      <select id="f_gen" onchange="fillParams()"></select></div>
    <div id="f_params"></div>
    <hr class="sep">
    <div class="field"><label>Adresse Modbus</label>
      <input id="f_addr" placeholder="vide = automatique"></div>
    <div class="field"><label>Echelle</label>
      <input id="f_scale" value="1" title="valeur transmise = valeur * echelle"></div>
    <div class="field"><label>Options</label><div class="opts">
      <label><input type="checkbox" id="f_writable" checked> ecriture autorisee</label><br>
      <label><input type="checkbox" id="f_modbus" checked> Modbus</label>
      <label><input type="checkbox" id="f_opcua" checked> OPC UA</label>
      <label><input type="checkbox" id="f_bacnet" checked> BACnet</label>
      <label><input type="checkbox" id="f_s7" checked> S7</label>
    </div></div>
    <div class="err" id="f_err"></div>
  </div>
  <div class="dlg-foot">
    <button onclick="document.getElementById('dlg').close()">Annuler</button>
    <button class="primary" onclick="submitAdd()">Ajouter</button>
  </div>
</dialog>

<script>
let catalog = [], dtypes = [], editing = null, signature = '', open_rows = new Set();
let selected = new Set(), selecting = false, tagNames = [];
const TG = {int16:'INT16', uint16:'UINT16', int32:'INT32', uint32:'UINT32',
            float32:'FLOAT32-IEEE', float64:'FLOAT64-IEEE'};
const S7_CODE = {bool:'X', int16:'I', uint16:'W', int32:'DI', uint32:'DW',
                 float32:'R', float64:'LR'};
const S7_TYPE = {bool:'BOOL', int16:'INT', uint16:'WORD', int32:'DINT',
                 uint32:'DWORD', float32:'REAL', float64:'LREAL'};

const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

async function post(url, body) {
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(body)});
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || 'erreur');
  return data;
}

function fmtValue(t) {
  if (t.dtype === 'bool')
    return '<span class="pill ' + (t.value ? 'on' : 'off') + '">'
         + (t.value ? 'ON' : 'OFF') + '</span>';
  const v = (t.value === null) ? '--'
          : (t.dtype.startsWith('float') ? Number(t.value).toFixed(3) : t.value);
  return '<span class="num">' + v + '</span>'
       + (t.unit ? '<span class="unit">' + esc(t.unit) + '</span>' : '');
}

/* ---- courbe de tendance (30 dernieres secondes) ---- */
const HIST = {}, HIST_MAX = 60, SP_W = 100, SP_H = 24, SP_PAD = 3;

function pushHist(t) {
  const v = t.dtype === 'bool' ? (t.value ? 1 : 0) : Number(t.value);
  if (!isFinite(v)) return;
  const h = HIST[t.name] || (HIST[t.name] = []);
  h.push(v);
  if (h.length > HIST_MAX) h.shift();
}

function sparkPoints(name, isBool) {
  const h = HIST[name] || [];
  if (h.length < 2) return '';
  let lo = Math.min(...h), hi = Math.max(...h);
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }        // signal plat : trait median
  const step = SP_W / (HIST_MAX - 1);
  const y = v => SP_H - SP_PAD - ((v - lo) / (hi - lo)) * (SP_H - 2 * SP_PAD);
  const pts = [];
  h.forEach((v, i) => {
    const x = i * step;
    if (isBool && i) pts.push(x.toFixed(1) + ',' + y(h[i - 1]).toFixed(1));
    pts.push(x.toFixed(1) + ',' + y(v).toFixed(1));
  });
  return pts.join(' ');
}

/* ---- copie d'un extrait ---- */
async function copySnip(ev, id) {
  ev.stopPropagation();
  const btn = ev.currentTarget;
  const texte = document.getElementById(id).textContent;
  let ok = false;
  try { await navigator.clipboard.writeText(texte); ok = true; }
  catch (e) {
    // navigator.clipboard n'existe qu'en contexte securise (https ou localhost)
    const ta = document.createElement('textarea');
    ta.value = texte; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { ok = document.execCommand('copy'); } catch (e2) { ok = false; }
    ta.remove();
  }
  btn.textContent = ok ? 'copie !' : 'echec';
  setTimeout(() => btn.textContent = 'copier', 1500);
}

function byteOrder(words, order) {
  const big = !String(order || 'big').startsWith('little');
  if (words <= 1) return big ? 'AB' : 'BA';
  if (words === 2) return big ? 'ABCD' : 'CDAB';
  return big ? 'ABCDEFGH' : 'GHEFCDAB';
}

function s7Addr(t, s7) {
  if (t.s7_offset === null || t.s7_offset === undefined) return '(en attente)';
  const db = 'DB' + s7.db_number + '.';
  if (t.dtype === 'bool') return db + 'X' + t.s7_offset + '.' + t.s7_bit;
  return db + (S7_CODE[t.dtype] || '?') + t.s7_offset;
}

function s7Snippet(t, s7) {
  if (!t.s7 || !s7.host) return '# ce tag n est pas publie en S7';
  return '[[inputs.s7comm.metric]]\\n  fields = [\\n'
       + '    { name="' + t.name + '", address="' + s7Addr(t, s7) + '" },\\n  ]';
}

function telegrafSnippet(t, mb) {
  if (!t.modbus) return '# ce tag n est pas publie en Modbus';
  if (t.dtype === 'bool')
    return '[[inputs.modbus.coils]]\\n  name = "' + t.name + '"\\n'
         + '  address = [' + t.address + ']';
  const addrs = [...Array(t.words).keys()].map(i => t.address + i).join(', ');
  const scale = t.scale ? (1 / t.scale) : 1;
  return '[[inputs.modbus.holding_registers]]\\n  name = "' + t.name + '"\\n'
       + '  address = [' + addrs + ']\\n'
       + '  byte_order = "' + byteOrder(t.words, mb.word_order) + '"\\n'
       + '  data_type = "' + (TG[t.dtype] || 'UINT16') + '"\\n'
       + '  scale = ' + scale;
}

function detailHtml(t, srv) {
  const mb = srv.modbus || {}, ua = srv.opcua || {}, bn = srv.bacnet || {},
        s7 = srv.s7 || {};
  const hostname = location.hostname;
  const mbHost = (!mb.host || mb.host === '0.0.0.0') ? hostname : mb.host;
  const endpoint = (ua.endpoint || '').replace('0.0.0.0', hostname);
  const nodeId = 'ns=' + ua.namespace_index + ';s=' + ua.folder + '.' + t.name;
  const params = Object.entries(t.generator_params || {})
      .filter(([k]) => k !== 'type')
      .map(([k, v]) => '<dt>' + esc(k) + '</dt><dd>' + esc(JSON.stringify(v)) + '</dd>')
      .join('') || '<dt class="muted">aucun</dt><dd></dd>';

  let modbusBlock = '<dl><dt class="muted">non publie</dt><dd></dd></dl>';
  if (t.modbus) {
    const isBool = t.dtype === 'bool';
    modbusBlock = '<dl>'
      + '<dt>adresse</dt><dd>' + mbHost + ':' + mb.port + '</dd>'
      + '<dt>unit id</dt><dd>' + mb.unit_id + '</dd>'
      + '<dt>zone</dt><dd>' + (isBool ? 'coil' : 'holding register') + '</dd>'
      + '<dt>registre</dt><dd>' + t.address
        + (t.words > 1 ? ' a ' + (t.address + t.words - 1) : '')
        + (isBool ? '' : ' (' + t.words + ')') + '</dd>'
      + '<dt>lecture</dt><dd>' + (isBool ? 'FC1' + (mb.mirror_discrete_inputs ? ' / FC2' : '')
                                         : 'FC3' + (mb.mirror_input_registers ? ' / FC4' : '')) + '</dd>'
      + '<dt>ecriture</dt><dd>' + (t.writable ? (isBool ? 'FC5 / FC15' : 'FC6 / FC16') : 'interdite') + '</dd>'
      + (isBool ? '' : '<dt>ordre des mots</dt><dd>' + byteOrder(t.words, mb.word_order) + '</dd>')
      + (t.scale !== 1 ? '<dt>echelle</dt><dd>x' + t.scale + '</dd>' : '')
      + '</dl>';
  }

  let uaBlock = '<dl><dt class="muted">non publie</dt><dd></dd></dl>';
  if (t.opcua) {
    uaBlock = '<dl>'
      + '<dt>endpoint</dt><dd>' + esc(endpoint) + '</dd>'
      + '<dt>NodeId</dt><dd>' + esc(nodeId) + '</dd>'
      + '<dt>namespace</dt><dd>' + esc(ua.uri) + '</dd>'
      + '<dt>type</dt><dd>' + t.dtype + '</dd>'
      + '<dt>ecriture</dt><dd>' + (t.writable ? 'autorisee' : 'interdite') + '</dd>'
      + '<dt>securite</dt><dd>' + (ua.anonymous ? 'anonyme, sans chiffrement' : 'authentifiee') + '</dd>'
      + '</dl>';
  }

  let bnBlock = '<dl><dt class="muted">non publie</dt><dd></dd></dl>';
  if (t.bacnet && bn.host) {
    const short = t.bacnet_type === 'binary-value' ? 'BV' : 'AV';
    bnBlock = '<dl>'
      + '<dt>adresse</dt><dd>' + bn.host + ':' + bn.port + ' (UDP)</dd>'
      + '<dt>device id</dt><dd>' + bn.device_id + '</dd>'
      + '<dt>objet</dt><dd>' + t.bacnet_type + ',' + t.bacnet_instance
        + ' (' + short + t.bacnet_instance + ')</dd>'
      + '<dt>object-name</dt><dd>' + esc(t.name) + '</dd>'
      + '<dt>propriete</dt><dd>present-value</dd>'
      + '<dt>ecriture</dt><dd>' + (t.writable ? 'acceptee (WriteProperty)' : 'interdite') + '</dd>'
      + '</dl>';
  }

  let s7Block = '<dl><dt class="muted">non publie</dt><dd></dd></dl>';
  if (t.s7 && s7.host) {
    const s7Host = (!s7.host || s7.host === '0.0.0.0') ? hostname : s7.host;
    s7Block = '<dl>'
      + '<dt>adresse</dt><dd>' + s7Host + ':' + s7.port + '</dd>'
      + '<dt>rack / slot</dt><dd>' + s7.rack + ' / ' + s7.slot + '</dd>'
      + '<dt>adresse DB</dt><dd>' + s7Addr(t, s7) + '</dd>'
      + '<dt>type S7</dt><dd>' + (S7_TYPE[t.dtype] || '?') + '</dd>'
      + '<dt>ecriture</dt><dd>' + (t.writable ? 'acceptee (client S7)' : 'interdite') + '</dd>'
      + '</dl>';
  }

  const snip = (titre, id, texte) =>
      '<div class="snip"><h4>' + titre + '</h4>'
    + '<button class="mini" onclick="copySnip(event,\\'' + id + '\\')">copier</button>'
    + '<pre id="' + id + '">' + esc(texte) + '</pre></div>';

  return '<td class="sel-cell"></td><td></td><td colspan="6"><div class="grid">'
    + '<div class="panel"><h4>Generateur — ' + esc(t.generator_type) + '</h4><dl>'
      + params + '</dl></div>'
    + '<div class="panel"><h4>Modbus TCP</h4>' + modbusBlock + '</div>'
    + '<div class="panel"><h4>OPC UA</h4>' + uaBlock + '</div>'
    + '<div class="panel"><h4>BACnet/IP</h4>' + bnBlock + '</div>'
    + '<div class="panel"><h4>S7comm</h4>' + s7Block + '</div>'
    + '</div>'
    + '<div class="snips">'
    + snip('Telegraf — Modbus', 'sn_mb_' + t.name, telegrafSnippet(t, mb))
    + snip('Telegraf — S7comm', 'sn_s7_' + t.name, s7Snippet(t, s7))
    + '</div>'
    + '<p class="hint">« ecriture acceptee » decrit ce que le simulateur '
    + 'autorise depuis un client du protocole concerne. Les plugins Telegraf '
    + '<code>inputs.*</code> sont des entrees : ils ne font que lire.</p>'
    + '<button class="danger" onclick="removeTag(event,\\'' + t.name + '\\')">'
    + 'Supprimer cette variable</button>'
    + '</td>';
}

function rowHtml(t) {
  let cmd;
  if (t.dtype === 'bool') {
    cmd = '<button onclick="act(event,\\'/api/toggle\\',\\'' + t.name + '\\')">Basculer</button>';
  } else {
    cmd = '<input class="cmd" id="in_' + t.name + '" onclick="event.stopPropagation()"'
        + ' onfocus="editing=\\'' + t.name + '\\'" onblur="editing=null"'
        + ' onkeydown="if(event.key===\\'Enter\\'){setVal(\\'' + t.name + '\\',this.value);'
        + 'this.value=\\'\\';this.blur();}">';
  }
  cmd += ' <span id="rel_' + t.name + '"></span>';
  const addr = t.modbus ? (t.area + ' ' + t.address) : '—';
  return '<td class="sel-cell" onclick="event.stopPropagation()">'
    + '<input type="checkbox" id="cb_' + t.name + '"'
    + (selected.has(t.name) ? ' checked' : '')
    + ' onchange="toggleOne(\\'' + t.name + '\\', this.checked)"></td>'
    + '<td><span class="chev">&#9656;</span></td>'
    + '<td><div class="tagname">' + esc(t.name) + '</div>'
      + (t.description ? '<div class="tagdesc">' + esc(t.description) + '</div>' : '')
      + '</td>'
    + '<td><span class="chip">' + t.dtype + '</span></td>'
    + '<td><span class="gen" title="' + esc(t.generator) + '">'
      + esc(t.generator) + '</span></td>'
    + '<td><span class="chip">' + addr + '</span></td>'
    + '<td class="val"><span id="v_' + t.name + '"></span>'
      + '<svg class="spark" viewBox="0 0 ' + SP_W + ' ' + SP_H + '"'
      + ' preserveAspectRatio="none"><polyline id="sp_' + t.name + '"/></svg></td>'
    + '<td onclick="event.stopPropagation()">' + cmd + '</td>';
}

function renderTable(s) {
  const body = document.getElementById('rows');
  body.innerHTML = '';
  for (const t of s.tags) {
    const tr = document.createElement('tr');
    tr.className = 'tag';
    tr.innerHTML = rowHtml(t);
    tr.onclick = () => toggleRow(t.name);
    body.appendChild(tr);
    const d = document.createElement('tr');
    d.className = 'detail';
    d.id = 'd_' + t.name;
    d.style.display = open_rows.has(t.name) ? '' : 'none';
    d.innerHTML = detailHtml(t, s.servers || {});
    body.appendChild(d);
    tr.classList.toggle('open', open_rows.has(t.name));
  }
}

function toggleRow(name) {
  const d = document.getElementById('d_' + name);
  const open = d.style.display === 'none';
  d.style.display = open ? '' : 'none';
  if (open) open_rows.add(name); else open_rows.delete(name);
  d.previousElementSibling.classList.toggle('open', open);
}

function updateValues(s) {
  for (const t of s.tags) {
    const cell = document.getElementById('v_' + t.name);
    if (!cell) continue;
    cell.innerHTML = fmtValue(t)
      + (t.forced ? '<span class="badge">force</span>' : '');
    pushHist(t);
    const sp = document.getElementById('sp_' + t.name);
    if (sp) sp.setAttribute('points', sparkPoints(t.name, t.dtype === 'bool'));
    const rel = document.getElementById('rel_' + t.name);
    const wanted = t.forced
      ? '<button onclick="act(event,\\'/api/release\\',\\'' + t.name + '\\')">Liberer</button>' : '';
    if (rel.innerHTML !== wanted) rel.innerHTML = wanted;
    if (t.dtype !== 'bool' && editing !== t.name) {
      const inp = document.getElementById('in_' + t.name);
      if (inp) inp.placeholder = t.dtype.startsWith('float')
        ? Number(t.value).toFixed(3) : t.value;
    }
  }
}

function duree(sec) {
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60),
        x = Math.floor(sec % 60);
  return h ? h + 'h' + String(m).padStart(2, '0') : (m ? m + 'min ' + x + 's' : x + 's');
}

function renderEndpoints(srv) {
  const host = location.hostname;
  const sub = v => (!v || v === '0.0.0.0') ? host : v;
  const eps = [];
  if (srv.modbus) eps.push(['Modbus TCP',
      sub(srv.modbus.host) + ':' + srv.modbus.port + '  ·  unit ' + srv.modbus.unit_id]);
  if (srv.opcua) eps.push(['OPC UA',
      (srv.opcua.endpoint || '').replace('0.0.0.0', host)]);
  if (srv.bacnet) eps.push(['BACnet/IP',
      sub(srv.bacnet.host) + ':' + srv.bacnet.port + '  ·  device ' + srv.bacnet.device_id]);
  if (srv.s7) eps.push(['S7comm',
      sub(srv.s7.host) + ':' + srv.s7.port + '  ·  DB' + srv.s7.db_number
      + '  ·  rack ' + srv.s7.rack + '/' + srv.s7.slot]);
  const html = eps.map(([n, v]) =>
      '<div class="ep"><b>' + n + '</b><span>' + esc(v) + '</span></div>').join('');
  const box = document.getElementById('endpoints');
  if (box.innerHTML !== html) box.innerHTML = html;
}

async function refresh() {
  let s;
  try { s = await (await fetch('/api/state')).json(); }
  catch (e) {
    document.getElementById('dot').classList.add('ko');
    document.getElementById('status_txt').textContent = 'serveur injoignable';
    return;
  }
  document.getElementById('dot').classList.remove('ko');
  document.getElementById('status_txt').textContent =
    'en service · ' + duree(s.uptime);
  document.getElementById('hdr').textContent =
    s.tags.length + ' variables · cycle ' + s.scan_ms + ' ms · '
    + s.cycles.toLocaleString('fr-FR') + ' cycles · ' + s.config_path;
  renderEndpoints(s.servers || {});
  const sig = s.tags.map(t => t.name + ':' + t.dtype + ':' + t.address + ':'
                            + t.bacnet_instance + ':' + t.s7_offset + ':'
                            + t.s7_bit + ':' + t.generator).join('|');
  if (sig !== signature) {
    signature = sig;
    tagNames = s.tags.map(t => t.name);
    // un tag disparu ne doit pas rester dans la selection
    for (const n of [...selected]) if (!tagNames.includes(n)) selected.delete(n);
    renderTable(s);
    updateSelCount();
  }
  updateValues(s);
}

async function act(ev, url, name) { ev.stopPropagation(); await post(url, {name}); refresh(); }
async function setVal(name, value) { await post('/api/set', {name, value}); refresh(); }

// ---- suppression en lot ---------------------------------------------------
function toggleSelect(on) {
  selecting = on;
  document.body.classList.toggle('selecting', on);
  document.getElementById('btn_sel').style.display = on ? 'none' : '';
  document.getElementById('btn_del').style.display = on ? '' : 'none';
  document.getElementById('btn_cancel').style.display = on ? '' : 'none';
  if (!on) {
    selected.clear();
    document.querySelectorAll('#rows input[type=checkbox]').forEach(c => c.checked = false);
    document.getElementById('sel_all').checked = false;
  }
  updateSelCount();
}

function toggleOne(name, on) {
  if (on) selected.add(name); else selected.delete(name);
  updateSelCount();
}

function toggleAll(on) {
  selected = on ? new Set(tagNames) : new Set();
  for (const n of tagNames) {
    const cb = document.getElementById('cb_' + n);
    if (cb) cb.checked = on;
  }
  updateSelCount();
}

function updateSelCount() {
  document.getElementById('btn_del').textContent =
    'Supprimer la selection (' + selected.size + ')';
  const all = document.getElementById('sel_all');
  all.checked = tagNames.length > 0 && selected.size === tagNames.length;
  all.indeterminate = selected.size > 0 && selected.size < tagNames.length;
}

async function deleteSelected() {
  const names = [...selected];
  if (!names.length) { alert('Aucune variable cochee.'); return; }
  if (!confirm('Supprimer definitivement ' + names.length + ' variable(s) ?\\n\\n'
               + names.join(', '))) return;
  const echecs = [];
  for (const n of names) {
    try { await post('/api/remove', {name: n}); open_rows.delete(n); }
    catch (e) { echecs.push(n + ' : ' + e.message); }
  }
  toggleSelect(false);
  await refresh();
  if (echecs.length) alert('Echec sur :\\n' + echecs.join('\\n'));
}

async function removeTag(ev, name) {
  ev.stopPropagation();
  if (!confirm('Supprimer la variable « ' + name + ' » ?')) return;
  open_rows.delete(name);
  await post('/api/remove', {name});
  refresh();
}

async function saveCfg() {
  const el = document.getElementById('saved');
  try { const r = await post('/api/save', {}); el.textContent = 'enregistre dans ' + r.path; }
  catch (e) { el.textContent = 'echec : ' + e.message; }
  setTimeout(() => el.textContent = '', 6000);
}

// ---- formulaire d'ajout --------------------------------------------------
function openAdd() {
  document.getElementById('f_err').textContent = '';
  document.getElementById('dlg').showModal();
}

function fillGenerators() {
  const dtype = document.getElementById('f_dtype').value;
  const kind = dtype === 'bool' ? 'bool' : 'numeric';
  const sel = document.getElementById('f_gen');
  const prev = sel.value;
  sel.innerHTML = catalog.filter(g => g.kind === kind || g.kind === 'both')
    .map(g => '<option value="' + g.type + '">' + esc(g.label) + '</option>').join('');
  if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
  fillParams();
}

function fillParams() {
  const dtype = document.getElementById('f_dtype').value;
  const gen = catalog.find(g => g.type === document.getElementById('f_gen').value);
  const box = document.getElementById('f_params');
  if (!gen) { box.innerHTML = ''; return; }
  box.innerHTML = gen.params.map(p => {
    const id = 'p_' + p.name;
    let input;
    if (p.type === 'bool')
      input = '<input type="checkbox" id="' + id + '"' + (p.default ? ' checked' : '') + '>';
    else if (p.type === 'choice')
      input = '<select id="' + id + '">' + p.choices.map(c =>
        '<option' + (c === p.default ? ' selected' : '') + '>' + c + '</option>').join('') + '</select>';
    else if (p.type === 'steps')
      input = '<textarea id="' + id + '" rows="4" style="width:100%" placeholder="'
            + (dtype === 'bool' ? 'true, 4' : '10, 5') + '">'
            + p.default.map(s => s.value + ', ' + s.duration).join('\\n') + '</textarea>';
    else if (p.type === 'text')
      input = '<input id="' + id + '" style="width:100%" value="' + esc(p.default) + '">';
    else
      input = '<input id="' + id + '" value="' + p.default + '">';
    return '<div class="field"><label>' + esc(p.label) + '</label>' + input + '</div>';
  }).join('');
}

function collectParams(dtype) {
  const gen = catalog.find(g => g.type === document.getElementById('f_gen').value);
  const out = {type: gen.type};
  for (const p of gen.params) {
    const el = document.getElementById('p_' + p.name);
    if (!el) continue;
    if (p.type === 'bool') out[p.name] = el.checked;
    else if (p.type === 'choice' || p.type === 'text') out[p.name] = el.value;
    else if (p.type === 'steps') {
      out[p.name] = el.value.split('\\n').map(l => l.trim()).filter(Boolean).map(l => {
        const parts = l.split(',');
        const raw = (parts[0] || '').trim();
        const dur = parseFloat((parts[1] || '1').trim()) || 1;
        let v;
        if (dtype === 'bool') v = /^(true|vrai|on|1)$/i.test(raw);
        else v = parseFloat(raw) || 0;
        return {value: v, duration: dur};
      });
    } else {
      const n = parseFloat(el.value);
      out[p.name] = isNaN(n) ? p.default : n;
    }
  }
  return out;
}

async function submitAdd() {
  const err = document.getElementById('f_err');
  const dtype = document.getElementById('f_dtype').value;
  const addr = document.getElementById('f_addr').value.trim();
  const spec = {
    name: document.getElementById('f_name').value.trim(),
    description: document.getElementById('f_desc').value.trim(),
    unit: document.getElementById('f_unit').value.trim(),
    dtype: dtype,
    generator: collectParams(dtype),
    scale: parseFloat(document.getElementById('f_scale').value) || 1,
    writable: document.getElementById('f_writable').checked,
    modbus: document.getElementById('f_modbus').checked,
    opcua: document.getElementById('f_opcua').checked,
    bacnet: document.getElementById('f_bacnet').checked,
    s7: document.getElementById('f_s7').checked,
  };
  if (addr !== '') spec.address = parseInt(addr, 10);
  try {
    const t = await post('/api/add', spec);
    document.getElementById('dlg').close();
    document.getElementById('f_name').value = '';
    document.getElementById('f_desc').value = '';
    open_rows.add(t.name);
    refresh();
  } catch (e) { err.textContent = e.message; }
}

async function boot() {
  const c = await (await fetch('/api/catalog')).json();
  catalog = c.generators; dtypes = c.dtypes;
  document.getElementById('f_dtype').innerHTML =
    dtypes.map(d => '<option' + (d === 'float32' ? ' selected' : '') + '>' + d + '</option>').join('');
  fillGenerators();
  refresh();
  setInterval(refresh, 500);
}
boot();
</script></body></html>
"""


def _clean(obj: Any) -> Any:
    """Remplace les valeurs non serialisables en JSON (NaN, inf) par null."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


class _Handler(BaseHTTPRequestHandler):
    engine = None       # injecte par start_web
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silence les logs d'acces
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: Any) -> None:
        self._send(code, json.dumps(_clean(payload)).encode("utf-8"), "application/json")

    def do_GET(self):
        if self.path.startswith("/api/state"):
            self._json(200, self.engine.snapshot())
        elif self.path.startswith("/api/catalog"):
            from .config import REGISTERS
            self._json(200, {
                "generators": generators.catalog(),
                "dtypes": list(REGISTERS.keys()),
            })
        elif self.path.startswith("/api/telegraf"):
            text = telegraf_config(self.engine.snapshot())
            self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")
        elif self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "JSON invalide"})

        name = body.get("name")
        try:
            if self.path.startswith("/api/set"):
                result = self.engine.set_value(name, body.get("value"))
            elif self.path.startswith("/api/toggle"):
                result = self.engine.toggle(name)
            elif self.path.startswith("/api/release"):
                result = self.engine.release(name)
            elif self.path.startswith("/api/add"):
                result = self.engine.add_tag(body)
            elif self.path.startswith("/api/remove"):
                result = self.engine.remove_tag(name)
            elif self.path.startswith("/api/save"):
                result = {"path": self.engine.save()}
            else:
                return self._json(404, {"error": "not found"})
        except KeyError as exc:
            return self._json(404, {"error": str(exc).strip("'")})
        except (ValueError, TypeError) as exc:
            return self._json(400, {"error": str(exc)})
        except OSError as exc:
            return self._json(500, {"error": f"ecriture impossible : {exc}"})
        self._json(200, result)


def start_web(engine, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("Handler", (_Handler,), {"engine": engine})
    httpd = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=httpd.serve_forever, name="webui", daemon=True).start()
    log.info("IHM web disponible sur http://%s:%s",
             "localhost" if host == "0.0.0.0" else host, port)
    return httpd
