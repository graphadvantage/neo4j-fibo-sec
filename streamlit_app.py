"""
FIBO ↔ SEC CFR Title 17 — GOVERNS Explorer
Streamlit app with 4-layer Sankey overview + drill-down to sections and paragraphs.

Layers (left → right):
  CFR Part → CFR Section → FIBO Class → FIBO Parent

Run with:
    streamlit run streamlit_app.py
"""

import json
import streamlit as st
import streamlit.components.v1 as components
import plotly.graph_objects as go
import pandas as pd
from collections import Counter, defaultdict
from neo4j import GraphDatabase, READ_ACCESS

# ── Icon (PiTrendUpBold via Phosphor / courtesy of Gemini) ────────────────────
_GRAPH_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
    'fill="{color}" viewBox="0 0 256 256">'
    '<path d="M232,176a32.06,32.06,0,0,1-22.63,54.63h-.05A32,32,0,0,1,181,192.42'
    'l-49.33-22a31.9,31.9,0,0,1-31.28,0L51.05,192.42a32,32,0,1,1-13-21.28l49.33-'
    '22a31.9,31.9,0,0,1,31.28,0l4.63,2.06L143.6,110.4a32,32,0,1,1,24.8,0l20.3,'
    '40.61L209.37,154A32,32,0,0,1,232,176ZM160,80a16,16,0,1,0-16,16A16,16,0,0,0,'
    '160,80ZM56,216a16,16,0,1,0-16-16A16,16,0,0,0,56,216Zm144,0a16,16,0,1,0-16-'
    '16A16,16,0,0,0,200,216Z"></path></svg>'
)

def _graph_icon(size: int = 22, color: str = "#00acee") -> str:
    return _GRAPH_ICON.format(w=size, h=size, color=color)


# ── Config ────────────────────────────────────────────────────────────────────
NEO4J_URI  = st.secrets["NEO4J_URI"]
NEO4J_USER = st.secrets["NEO4J_USER"]
NEO4J_PWD  = st.secrets["NEO4J_PASSWORD"]
NEO4J_DB   = "neo4j"

st.set_page_config(
    page_title="SEC CFR Title 17 → FIBO Governance Explorer",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Neo4j connection ───────────────────────────────────────────────────────────
@st.cache_resource
def get_driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PWD))


def _session():
    return get_driver().session(database=NEO4J_DB, default_access_mode=READ_ACCESS)


driver = get_driver()


# ── Data loaders ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_sankey_data(top_n_fibo: int, top_n_sections: int, min_score: float):
    """
    Returns raw (part, section, fibo, fibo_parent) rows for the top N FIBO
    classes and top N sections connected to them.
    """
    with _session() as s:

        # Top N FIBO classes by rollup GOVERNS count
        top_fibo = s.run(
            """
            MATCH (:Section)-[r:GOVERNS {method:'vector_rollup'}]->(f:FIBO:Class)
            WHERE r.similarity_score >= $min_score
            RETURN f.prefLabel AS label, count(r) AS cnt
            ORDER BY cnt DESC LIMIT $n
            """,
            n=top_n_fibo,
            min_score=min_score,
        ).data()
        fibo_labels = [r["label"] for r in top_fibo]

        # Top N sections linked to those FIBO classes
        top_secs = s.run(
            """
            MATCH (sec:Section)-[r:GOVERNS {method:'vector_rollup'}]->(f:FIBO:Class)
            WHERE f.prefLabel IN $fibo_labels
              AND r.similarity_score >= $min_score
            RETURN coalesce(sec.notation, sec.prefLabel) AS label, count(r) AS cnt
            ORDER BY cnt DESC LIMIT $n
            """,
            fibo_labels=fibo_labels,
            n=top_n_sections,
            min_score=min_score,
        ).data()
        sec_labels = [r["label"] for r in top_secs]

        # 4-level detail rows — key fields for linking, label fields for display
        rows = s.run(
            """
            MATCH (sec:Section)-[r:GOVERNS {method:'vector_rollup'}]->(f:FIBO:Class)
            WHERE f.prefLabel IN $fibo_labels
              AND coalesce(sec.notation, sec.prefLabel) IN $sec_labels
              AND r.similarity_score >= $min_score
            MATCH (sec)-[:BROADER]->(part:Part)
            OPTIONAL MATCH (f)-[:SUBCLASS_OF]->(fp:Class)
            RETURN
              coalesce(part.notation, part.prefLabel)  AS part,
              coalesce(part.prefLabel, part.notation)  AS part_label,
              coalesce(sec.notation,  sec.prefLabel)   AS section,
              coalesce(sec.prefLabel, sec.notation)    AS sec_label,
              f.prefLabel                               AS fibo,
              coalesce(fp.prefLabel, '[FIBO Root]')    AS parent,
              r.similarity_score                        AS score
            """,
            fibo_labels=fibo_labels,
            sec_labels=sec_labels,
            min_score=min_score,
        ).data()

    return fibo_labels, rows


@st.cache_data(ttl=300)
def load_fibo_sections(fibo_label: str, min_score: float):
    """Sections linked to a FIBO class via rollup GOVERNS, with their Part."""
    with _session() as s:
        return s.run(
            """
            MATCH (sec:Section)-[r:GOVERNS {method:'vector_rollup'}]->(f:FIBO:Class)
            WHERE f.prefLabel = $label
              AND r.similarity_score >= $min_score
            MATCH (sec)-[:BROADER]->(part:Part)
            RETURN coalesce(sec.notation, sec.prefLabel)  AS notation,
                   coalesce(sec.prefLabel, sec.notation)  AS title,
                   coalesce(part.notation, part.prefLabel) AS part,
                   coalesce(part.prefLabel, part.notation) AS part_label,
                   r.similarity_score                      AS score
            ORDER BY score DESC
            """,
            label=fibo_label,
            min_score=min_score,
        ).data()


# ── Tab 2 data loaders ────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_coverage_stats():
    """Summary counts for FIBO and SEC coverage."""
    with _session() as s:
        fibo = s.run("""
            MATCH (f:FIBO:Class)
            RETURN count(f) AS total,
                   sum(CASE WHEN ()-[:GOVERNS]->(f) THEN 1 ELSE 0 END) AS governed
        """).single()
        sec = s.run("""
            MATCH (sec:Section)
            RETURN count(sec) AS total,
                   sum(CASE WHEN (sec)-[:GOVERNS]->() THEN 1 ELSE 0 END) AS linked
        """).single()
    return dict(fibo), dict(sec)


@st.cache_data(ttl=300)
def load_ungoverned_fibo():
    """FIBO classes with no incoming GOVERNS, grouped by immediate parent module."""
    with _session() as s:
        return s.run("""
            MATCH (f:FIBO:Class)
            WHERE NOT ()-[:GOVERNS]->(f)
            OPTIONAL MATCH (f)-[:SUBCLASS_OF]->(parent:Class)
            RETURN coalesce(parent.prefLabel, '[No parent]') AS module,
                   f.prefLabel                               AS concept,
                   f.uri                                     AS uri
            ORDER BY module, concept
        """).data()


@st.cache_data(ttl=300)
def load_unlinked_sections():
    """CFR Sections with no outgoing GOVERNS, grouped by Part."""
    with _session() as s:
        return s.run("""
            MATCH (sec:Section)
            WHERE NOT (sec)-[:GOVERNS]->()
            MATCH (sec)-[:BROADER]->(part:Part)
            RETURN coalesce(part.notation, part.prefLabel)  AS part_key,
                   coalesce(part.prefLabel, part.notation)  AS part_name,
                   coalesce(sec.notation,  sec.prefLabel)   AS notation,
                   coalesce(sec.prefLabel, sec.notation)    AS title
            ORDER BY part_key, notation
        """).data()


@st.cache_data(ttl=300)
def load_section_paragraphs(notation: str, fibo_label: str):
    """Paragraphs in a section, with paragraph-level similarity score where available."""
    with _session() as s:
        return s.run(
            """
            MATCH (sec:Section)-[:HAS_TEXT]->(:Text)-[:HAS_PARA]->(p:Paragraph)
            WHERE coalesce(sec.notation, sec.prefLabel) = $notation
            OPTIONAL MATCH (p)-[r:GOVERNS]->(f:FIBO:Class {prefLabel: $label})
            RETURN p.marker AS marker,
                   p.text   AS text,
                   p.depth  AS depth,
                   r.similarity_score AS score
            ORDER BY p.depth, p.marker
            """,
            notation=notation,
            label=fibo_label,
        ).data()


@st.cache_data(ttl=300)
def load_governs_subgraph(fibo_label: str, min_score: float):
    """
    Fetch subgraph for NVL visualization:
      • FIBO class selected + 2 levels of SUBCLASS_OF ancestors
      • Top-20 Sections (by similarity) that GOVERNS this FIBO class
      • BROADER Part for each section
    Returns (nvl_nodes, nvl_rels) in NVL-base format.
    """
    _SKIP = {"labelVector", "embedding"}

    def _clean(props: dict) -> dict:
        out = {}
        for k, v in props.items():
            if k in _SKIP:
                continue
            if isinstance(v, (str, int, float, bool, type(None))):
                out[k] = v
            elif isinstance(v, list):
                out[k] = [str(x) for x in v[:5]]
            else:
                out[k] = str(v)
        return out

    def _color(labels):
        if "Part"      in labels: return "#c2410c"
        if "Section"   in labels: return "#fb923c"
        if "Text"      in labels: return "#fdba74"
        if "Paragraph" in labels: return "#fed7aa"
        if "FIBO"      in labels and "Class" in labels: return "#16a34a"
        if "Class"     in labels: return "#14532d"
        return "#6b7280"

    seen_nodes: dict = {}
    seen_rels:  dict = {}

    def _trunc(s: str, n: int = 24) -> str:
        s = str(s)
        return s if len(s) <= n else s[:n - 1] + "…"

    def _caption(lbl: list, props: dict, eid: str) -> str:
        """Node-type-aware short display label."""
        if "Section" in lbl:
            return "§" + (props.get("notation") or props.get("prefLabel") or "?")
        if "Part" in lbl:
            return "Part " + (props.get("notation") or props.get("prefLabel") or "?")
        raw = (props.get("prefLabel")
               or props.get("notation")
               or str(props.get("uri", eid)).split("/")[-1])
        return _trunc(raw)

    def _add_node(n):
        if n is None:
            return
        eid = n.element_id
        if eid in seen_nodes:
            return
        lbl   = list(n.labels)
        props = _clean(dict(n.items()))
        seen_nodes[eid] = {
            "id":       eid,
            "color":    _color(lbl),
            "captions": [{"value": _caption(lbl, props, eid), "labels": lbl}],
            "properties": props,
        }

    def _add_rel(r):
        if r is None:
            return
        eid = r.element_id
        if eid in seen_rels:
            return
        seen_rels[eid] = {
            "id":       eid,
            "from":     r.start_node.element_id,
            "to":       r.end_node.element_id,
            "captions": [{"value": r.type}],
        }

    with _session() as s:
        # FIBO class + 2-level SUBCLASS_OF ancestors
        for rec in s.run(
            """
            MATCH (f:FIBO:Class {prefLabel: $label})
            OPTIONAL MATCH (f)-[r1:SUBCLASS_OF]->(p:Class)
            OPTIONAL MATCH (p)-[r2:SUBCLASS_OF]->(gp:Class)
            RETURN f, r1, p, r2, gp
            """,
            label=fibo_label,
        ):
            _add_node(rec["f"])
            if rec["p"]:
                _add_node(rec["p"])
                _add_rel(rec["r1"])
            if rec["gp"]:
                _add_node(rec["gp"])
                _add_rel(rec["r2"])

        # Top-20 sections + GOVERNS + Part hierarchy + associated Text text
        for rec in s.run(
            """
            MATCH (sec:Section)-[r:GOVERNS {method:'vector_rollup'}]->(f:FIBO:Class {prefLabel: $label})
            WHERE r.similarity_score >= $min_score
            MATCH (sec)-[rb:BROADER]->(part:Part)
            OPTIONAL MATCH (sec)-[:HAS_TEXT]->(t:Text)
            RETURN sec, r, f, part, rb, t.text AS sec_text
            ORDER BY r.similarity_score DESC LIMIT 20
            """,
            label=fibo_label,
            min_score=min_score,
        ):
            _add_node(rec["part"])
            # Inject the Text node's text into the Section node properties
            # so the sidebar can display it directly.
            sec = rec["sec"]
            if sec is not None:
                eid = sec.element_id
                if eid not in seen_nodes:
                    lbl   = list(sec.labels)
                    props = _clean(dict(sec.items()))
                    if rec["sec_text"]:
                        props["text"] = rec["sec_text"]
                    seen_nodes[eid] = {
                        "id":       eid,
                        "color":    _color(lbl),
                        "captions": [{"value": _caption(lbl, props, eid), "labels": lbl}],
                        "properties": props,
                    }
            _add_node(rec["f"])
            _add_rel(rec["r"])
            _add_rel(rec["rb"])

    return list(seen_nodes.values()), list(seen_rels.values())


# ── Helpers ───────────────────────────────────────────────────────────────────
def even_y(n: int) -> list[float]:
    """Distribute n nodes evenly between 0.05 and 0.95."""
    if n == 1:
        return [0.5]
    return [0.05 + 0.90 * i / (n - 1) for i in range(n)]


@st.cache_data(ttl=None)
def _load_nvl_packages() -> dict | None:
    """
    Recursively load @neo4j-nvl/base and every @neo4j-nvl/* peer it imports
    from the local npm install.

    base.mjs is NOT a self-contained bundle — it has bare-specifier imports
    like `from '@neo4j-nvl/layout-workers'` that the browser can't resolve.
    We collect every such package here so the HTML can create blob URLs for
    each dep and patch base.mjs at runtime before importing it.

    Returns  { "@neo4j-nvl/base": "<base64>", "@neo4j-nvl/layout-workers": ... }
    or None if the npm install has not been done yet.
    """
    import re, os, base64

    app_dir  = os.path.dirname(os.path.abspath(__file__))
    nvl_root = os.path.join(app_dir, "node_modules", "@neo4j-nvl")
    if not os.path.isdir(nvl_root):
        nvl_root = os.path.join(".", "node_modules", "@neo4j-nvl")
    if not os.path.isdir(nvl_root):
        return None

    def _main(pkg_name: str) -> str:
        """Return the path to the ESM entry file of an @neo4j-nvl/* package."""
        pkg_dir = os.path.join(nvl_root, pkg_name)
        try:
            with open(os.path.join(pkg_dir, "package.json")) as f:
                data = json.load(f)
            # Prefer ESM 'module' field over CJS 'main'
            rel = data.get("module") or data.get("main") or "dist/index.mjs"
        except (FileNotFoundError, json.JSONDecodeError):
            rel = "dist/index.mjs"
        return os.path.join(pkg_dir, rel)

    def _read(path: str) -> str | None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except (FileNotFoundError, OSError):
            return None

    def _bare_imports(src: str) -> set[str]:
        """Find all bare @neo4j-nvl/* specifiers referenced in a JS source."""
        return set(re.findall(r'''(?:from|import)\s*['"](@neo4j-nvl/[^'"]+)['"]''', src))

    # Seed with base.mjs
    base_src = _read(os.path.join(nvl_root, "base", "dist", "base.mjs"))
    if not base_src:
        return None

    packages: dict[str, str] = {"@neo4j-nvl/base": base_src}
    queue = list(_bare_imports(base_src))
    seen  = {"@neo4j-nvl/base"}

    while queue:
        spec = queue.pop(0)
        if spec in seen:
            continue
        seen.add(spec)
        pkg_name = spec.removeprefix("@neo4j-nvl/")
        src = _read(_main(pkg_name))
        if src:
            packages[spec] = src
            for dep in _bare_imports(src):
                if dep not in seen:
                    queue.append(dep)

    return {
        spec: base64.b64encode(src.encode("utf-8")).decode("ascii")
        for spec, src in packages.items()
    }


def build_nvl_html(nvl_nodes: list, nvl_rels: list, height: int = 540) -> str:
    """
    Self-contained HTML that renders an NVL force-directed graph.

    Uses esm.sh CDN which resolves @neo4j-nvl/base and all its transitive
    dependencies (@neo4j-nvl/layout-workers, etc.) server-side, returning a
    single browser-compatible ESM bundle — no local npm install required.

    Node click → slide-in detail panel showing node text / properties.
    """
    nodes_json = json.dumps(nvl_nodes, ensure_ascii=False)
    rels_json  = json.dumps(nvl_rels,  ensure_ascii=False)

    # esm.sh rewrites all bare specifier imports to CDN URLs automatically.
    # ClickInteraction handles mouse events — the NVL constructor's 5th-arg
    # ExternalCallbacks does NOT fire onNodeClick; interaction-handlers does.
    nvl_loader = (
        "const { NVL } = await import("
        "'https://esm.sh/@neo4j-nvl/base@1.1.0?bundle&target=es2020');\n"
        "const { ClickInteraction, PanInteraction, DragNodeInteraction } = await import("
        "'https://esm.sh/@neo4j-nvl/interaction-handlers@1.1.0?bundle&target=es2020');\n"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: 100%; height: {height}px; overflow: hidden;
    background: #0f172a; color: #e2e8f0;
    font-family: system-ui, -apple-system, sans-serif;
  }}

  /* graph canvas — always full size (detail panel overlays it) */
  #graph-el {{
    position: absolute; top: 0; left: 0;
    width: 100%; height: {height}px;
  }}

  /* loading / error overlays */
  #loading, #err {{
    position: absolute; top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    pointer-events: none; text-align: center;
  }}
  #loading {{ color: #64748b; font-size: 13px; }}
  #err     {{ color: #ef4444; font-size: 13px; display: none; max-width: 320px; }}

  /* zoom controls — top-right corner */
  #zoom-btns {{
    position: absolute; top: 10px; right: 10px;
    display: flex; flex-direction: column; gap: 4px; z-index: 20;
  }}
  .zbtn {{
    width: 32px; height: 32px; border-radius: 6px;
    background: #1e293b; border: 1px solid #334155; color: #94a3b8;
    font-size: 16px; cursor: pointer; display: flex;
    align-items: center; justify-content: center;
  }}
  .zbtn:hover {{ background: #334155; color: #f1f5f9; }}

  /* detail panel — slides in from the LEFT */
  #detail {{
    position: absolute; top: 0; left: 0;
    width: 300px; height: {height}px;
    background: #1e293b; border-right: 2px solid #334155;
    padding: 14px 16px; overflow-y: auto;
    display: flex; flex-direction: column; gap: 10px;
    transform: translateX(-100%);
    transition: transform 0.2s ease;
    z-index: 10;
  }}
  #detail.open {{ transform: translateX(0); }}

  /* panel header row */
  #detail-header {{
    display: flex; align-items: center; justify-content: space-between;
  }}
  #close-btn {{
    background: none; border: none;
    color: #64748b; cursor: pointer; font-size: 18px; line-height: 1;
  }}
  #close-btn:hover {{ color: #f1f5f9; }}

  #d-badge {{
    display: inline-block; padding: 3px 10px; border-radius: 9999px;
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .06em; background: #334155; color: #94a3b8;
    width: fit-content;
  }}
  #d-name {{
    font-size: 15px; font-weight: 600; color: #f1f5f9;
    line-height: 1.35; word-break: break-word;
  }}
  #d-text {{
    font-size: 12px; color: #cbd5e1; line-height: 1.6;
    white-space: pre-wrap; word-break: break-word;
    max-height: 260px; overflow-y: auto;
    background: #0f172a; border-radius: 6px;
    padding: 8px; border: 1px solid #334155;
  }}
  #d-props {{ display: flex; flex-direction: column; gap: 6px; }}
  .prop-k {{ font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: .05em; }}
  .prop-v {{ font-size: 12px; color: #94a3b8; word-break: break-word; }}
</style>
</head>
<body>
  <div id="graph-el"></div>
  <div id="loading">Loading graph…</div>
  <div id="err"></div>

  <!-- zoom controls -->
  <div id="zoom-btns">
    <button class="zbtn" id="btn-zoomin"  title="Zoom in">＋</button>
    <button class="zbtn" id="btn-zoomout" title="Zoom out">－</button>
    <button class="zbtn" id="btn-fit"     title="Fit to screen">⊡</button>
  </div>

  <!-- left detail panel -->
  <div id="detail">
    <div id="detail-header">
      <span id="d-badge">Node</span>
      <button id="close-btn" title="Close">✕</button>
    </div>
    <div id="d-name">—</div>
    <div id="d-props"></div>
    <div id="d-text" style="display:none"></div>
  </div>

<script type="module">
(async () => {{
try {{
  {nvl_loader}

  const NODES = {nodes_json};
  const RELS  = {rels_json};

  // Node lookup by id — NVL click events may not preserve custom fields
  // (properties, captions.labels), so we resolve them from this map.
  const NODE_MAP = Object.fromEntries(NODES.map(n => [n.id, n]));

  const graphEl  = document.getElementById('graph-el');
  const detailEl = document.getElementById('detail');
  const badgeEl  = document.getElementById('d-badge');
  const nameEl   = document.getElementById('d-name');
  const textEl   = document.getElementById('d-text');
  const propsEl  = document.getElementById('d-props');

  const SKIP = new Set(['labelVector', 'embedding']);

  // ── Hoist nvl ref ─────────────────────────────────────────────────────────
  let nvl = null;

  // Toggle selection: deselectAll() clears every node, then updateElementsInGraph
  // patches only the `selected` flag on the target node (other properties unchanged).
  function setSelected(clickedId) {{
    if (!nvl) return;
    nvl.deselectAll();
    if (clickedId) {{
      nvl.updateElementsInGraph([{{ id: clickedId, selected: true }}], []);
    }}
  }}

  // ── Node type label (mirrors TSX Drawer.Header) ───────────────────────────
  function nodeTypeLabel(labels) {{
    if (labels.includes('Section'))   return 'CFR Section';
    if (labels.includes('Part'))      return 'CFR Part';
    if (labels.includes('Text'))      return 'Section Text';
    if (labels.includes('Paragraph')) return 'Paragraph';
    if (labels.includes('FIBO') && labels.includes('Class')) return 'FIBO Class';
    if (labels.includes('Class'))     return 'FIBO Concept';
    return 'Node';
  }}

  // ── Show node in left panel (mirrors TSX handleExpand + Drawer.Body) ──────
  function showNode(node) {{
    const p      = node.properties || {{}};
    const labels = node.captions?.[0]?.labels ?? [];

    // Header — node type + full name
    badgeEl.textContent = nodeTypeLabel(labels);
    nameEl.textContent  = p.prefLabel || p.notation
                          || node.captions?.[0]?.value || node.id;

    // Body — text content (regulatory text or FIBO definition)
    const body = p.text || p.definition || p.description || null;
    if (body) {{
      textEl.textContent   = body;
      textEl.style.display = 'block';
    }} else {{
      textEl.style.display = 'none';
    }}

    // Extra properties (notation, score, etc.)
    const SHOWN = new Set(['prefLabel', 'text', 'definition', 'description', 'notation']);
    propsEl.innerHTML = '';
    for (const [k, v] of Object.entries(p)) {{
      if (SKIP.has(k) || SHOWN.has(k) || v == null || v === '') continue;
      const d = document.createElement('div');
      d.innerHTML =
        `<div class="prop-k">${{k}}</div>`+
        `<div class="prop-v">${{String(v).slice(0, 400)}}</div>`;
      propsEl.appendChild(d);
    }}

    detailEl.classList.add('open');
    setSelected(node.id);
  }}

  document.getElementById('close-btn').addEventListener('click', () => {{
    if (nvl) nvl.deselectAll();
    detailEl.classList.remove('open');
  }});

  // ── Init NVL after one animation frame (container needs definite px dims) ─
  requestAnimationFrame(() => {{
    document.getElementById('loading').style.display = 'none';

    nvl = new NVL(
      graphEl,
      NODES,
      RELS,
      {{
        layout: 'd3Force',
        initialZoom: 0.5,
        maxZoom: 10,
        relationshipThreshold: 0,
        selectedBorderColor: '#ffffff',   // white outline on selected node
      }}
    );

    // ClickInteraction wires click events; PanInteraction wires canvas drag.
    const ci = new ClickInteraction(nvl);
    ci.updateCallback('onNodeClick',   (node, _h, _e) => showNode(NODE_MAP[node.id] || node));
    ci.updateCallback('onCanvasClick', (_e) => {{
      nvl.deselectAll();
      detailEl.classList.remove('open');
    }});

    // Pan canvas when dragging on empty space.
    const pan = new PanInteraction(nvl);

    // Drag individual nodes to reposition them.
    new DragNodeInteraction(nvl);

    // Zoom / fit controls
    const STEP = 0.3;
    let currentZoom = 0.5;  // matches initialZoom
    document.getElementById('btn-zoomin').addEventListener('click', () => {{
      currentZoom = Math.min(10, currentZoom + STEP);
      nvl.setZoom(currentZoom);
    }});
    document.getElementById('btn-zoomout').addEventListener('click', () => {{
      currentZoom = Math.max(0.05, currentZoom - STEP);
      nvl.setZoom(currentZoom);
    }});
    document.getElementById('btn-fit').addEventListener('click', () => {{
      nvl.fit(NODES.map(n => n.id));
    }});
  }});

}} catch(err) {{
  console.error('NVL init error:', err);
  const el = document.getElementById('err');
  el.textContent = 'Graph failed to load: ' + err.message;
  el.style.display = 'block';
  document.getElementById('loading').style.display = 'none';
}}
}})();
</script>
</body>
</html>"""


def build_sankey(rows: list[dict], title: str, height: int = 650) -> go.Figure:
    """
    Build a 4-layer Plotly Sankey from raw (part, section, fibo, parent) rows.
    Layout (left → right): CFR Part | CFR Section | FIBO Class | FIBO Parent
    """
    # Unique nodes per layer keyed by notation/id (insertion-ordered)
    parts   = list(dict.fromkeys(r["part"]    for r in rows))
    secs    = list(dict.fromkeys(r["section"] for r in rows))
    fibos   = list(dict.fromkeys(r["fibo"]    for r in rows))
    parents = list(dict.fromkeys(r["parent"]  for r in rows))

    # prefLabel maps for tooltips (key → human-readable name)
    part_lbl = {r["part"]:    r.get("part_label", r["part"])    for r in rows}
    sec_lbl  = {r["section"]: r.get("sec_label",  r["section"]) for r in rows}

    # Node display labels: notation keys, § prefix on sections
    all_labels = parts + ["§" + s for s in secs] + fibos + parents
    # Tooltip customdata: prefLabel for SEC nodes, same as label for FIBO
    all_custom = (
        [part_lbl[p] for p in parts]  +
        [sec_lbl[s]  for s in secs]   +
        fibos                          +
        parents
    )
    # Index keys use raw notation values so link counters resolve correctly
    all_keys = parts + secs + fibos + parents
    idx      = {k: i for i, k in enumerate(all_keys)}

    # Link counts and scores at each layer boundary
    part_sec  = Counter((r["part"],    r["section"]) for r in rows)
    sec_fibo  = Counter((r["section"], r["fibo"])    for r in rows)
    fibo_par  = Counter((r["fibo"],    r["parent"])  for r in rows)

    # Avg similarity per (section, fibo) pair for middle-link tooltips
    sec_fibo_scores: dict = defaultdict(list)
    for r in rows:
        if r.get("score") is not None:
            sec_fibo_scores[(r["section"], r["fibo"])].append(r["score"])
    sec_fibo_avg = {k: sum(v) / len(v) for k, v in sec_fibo_scores.items()}

    sources, targets, values, link_custom = [], [], [], []

    # Part → Section  (no tooltip — empty customdata[0])
    for (a, b), v in part_sec.items():
        sources.append(idx[a]); targets.append(idx[b]); values.append(v)
        link_custom.append([""])

    # Section → FIBO Class  (GOVERNS count + avg similarity in customdata[0])
    for (a, b), v in sec_fibo.items():
        sources.append(idx[a]); targets.append(idx[b]); values.append(v)
        avg = sec_fibo_avg.get((a, b), 0.0)
        link_custom.append([f"rels: {v}    avg similarity: {avg:.3f}"])

    # FIBO Class → Parent  (no tooltip — empty customdata[0])
    for (a, b), v in fibo_par.items():
        sources.append(idx[a]); targets.append(idx[b]); values.append(v)
        link_custom.append([""])

    # x positions enforce 4-layer left-to-right ordering
    node_x = [0.01]*len(parts) + [0.33]*len(secs) + [0.66]*len(fibos) + [0.99]*len(parents)
    node_y = even_y(len(parts)) + even_y(len(secs)) + even_y(len(fibos)) + even_y(len(parents))

    # Oranges for SEC (left), greens for FIBO (right)
    node_color = (
        ["#c2410c"] * len(parts)   +   # CFR Part    — deep orange
        ["#fb923c"] * len(secs)    +   # CFR Section — light orange
        ["#16a34a"] * len(fibos)   +   # FIBO Class  — medium green
        ["#14532d"] * len(parents)     # FIBO Parent — dark forest green
    )

    fig = go.Figure(go.Sankey(
        arrangement="fixed",
        node=dict(
            label         = all_labels,
            customdata    = all_custom,
            hovertemplate = "%{customdata}<br>Flow: %{value}<extra></extra>",
            x             = node_x,
            y             = node_y,
            color         = node_color,
            pad           = 12,
            thickness     = 18,
        ),
        link=dict(
            source        = sources,
            target        = targets,
            value         = values,
            customdata    = link_custom,
            hovertemplate = "%{customdata[0]}<extra></extra>",
            color         = "rgba(120, 120, 120, 0.15)",
        ),
    ))
    fig.update_layout(
        title_text=title,
        font_size=11,
        height=height,
        margin=dict(l=10, r=10, t=40, b=10),
    )
    return fig


# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.title("SEC CFR Title 17\n→ FIBO Governance Explorer")
st.sidebar.caption("Vector GOVERNS — section-level rollup")
st.sidebar.divider()

top_n_fibo     = st.sidebar.slider("Top FIBO classes",  min_value=5,  max_value=50,  value=20, step=5)
top_n_sections = st.sidebar.slider("Top CFR sections",  min_value=10, max_value=100, value=40, step=10)
min_score      = st.sidebar.slider("Min similarity score", min_value=0.70, max_value=0.95, value=0.75, step=0.05)

st.sidebar.divider()
st.sidebar.markdown(
    "**Layers (left → right)**\n"
    "- CFR Part *(deep orange)*\n"
    "- CFR Section *(light orange)*\n"
    "- FIBO Class *(medium green)*\n"
    "- FIBO Parent *(dark green)*\n\n"
    "Flow width = GOVERNS link count."
)


# ── Tabs ──────────────────────────────────────────────────────────────────────
st.title("SEC CFR Title 17 → FIBO Governance Explorer")
tab1, tab2 = st.tabs(["GOVERNS Explorer", "Coverage Analysis"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — GOVERNS Explorer (existing content)
# ══════════════════════════════════════════════════════════════════════════════
with tab1:

    fibo_labels, rows = load_sankey_data(top_n_fibo, top_n_sections, min_score)

    if not rows:
        st.warning("No GOVERNS relationships found for the selected parameters. Try lowering the score threshold.")
        st.stop()

    sankey_fig = build_sankey(
        rows,
        title=f"CFR Part → Section → FIBO Class → FIBO Parent  "
              f"(top {top_n_fibo} FIBO · top {top_n_sections} sections · min score {min_score:.2f})",
        height=700,
    )
    st.plotly_chart(sankey_fig, use_container_width=True)


    # ── Drill-down ────────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Drill Down")

    col1, col2 = st.columns([1, 2])

    with col1:
        selected_fibo = st.selectbox("Select a FIBO class", options=fibo_labels, index=0)

    # Reset graph view when FIBO selection changes
    if st.session_state.get("_sel_fibo") != selected_fibo:
        st.session_state["show_graph"] = False
        st.session_state["_sel_fibo"] = selected_fibo

    sections = load_fibo_sections(selected_fibo, min_score)

    with col2:
        if sections:
            # Mini 4-layer Sankey scoped to the selected FIBO class
            mini_rows = [
                {"part":       s["part"],
                 "part_label": s["part_label"],
                 "section":    s["notation"],
                 "sec_label":  s["title"],
                 "fibo":       selected_fibo,
                 "parent":     "[FIBO Root]",
                 "score":      s["score"]}
                for s in sections
            ]
            # Fetch FIBO parent for the selected class
            with _session() as _s:
                fp = _s.run(
                    """
                    MATCH (f:FIBO:Class {prefLabel: $label})-[:SUBCLASS_OF]->(p:Class)
                    RETURN p.prefLabel AS parent LIMIT 1
                    """,
                    label=selected_fibo,
                ).single()
            fibo_parent = fp["parent"] if fp else "[FIBO Root]"
            for r in mini_rows:
                r["parent"] = fibo_parent

            mini_fig = build_sankey(
                mini_rows,
                title=selected_fibo,
                height=300,
            )
            st.plotly_chart(mini_fig, use_container_width=True)

        # Graph icon button — toggles the NVL force-directed graph
        graph_open = st.session_state.get("show_graph", False)
        ico_color  = "#ef4444" if graph_open else "#00acee"
        btn_label  = "Hide Graph" if graph_open else "View Graph"
        ic, bt = st.columns([0.08, 0.92])
        with ic:
            st.markdown(
                f'<div style="margin-top:6px">{_graph_icon(22, ico_color)}</div>',
                unsafe_allow_html=True,
            )
        with bt:
            if st.button(btn_label, key="graph_btn",
                         help="Toggle force-directed GOVERNS subgraph"):
                st.session_state["show_graph"] = not graph_open
                st.rerun()

    # ── NVL force-directed graph (full-width, below columns) ──────────────────
    if st.session_state.get("show_graph", False):
        with st.spinner("Building graph…"):
            nvl_nodes, nvl_rels = load_governs_subgraph(selected_fibo, min_score)
        if nvl_nodes:
            components.html(
                build_nvl_html(nvl_nodes, nvl_rels, height=540),
                height=550,
                scrolling=False,
            )
        else:
            st.info("No graph data found for this selection.")

    # Section expanders
    if sections:
        st.markdown(f"**{len(sections)} CFR sections** reference `{selected_fibo}`")

        all_parts      = sorted({s["part"] for s in sections})
        selected_parts = st.multiselect("Filter by CFR Part", options=all_parts, default=all_parts)
        filtered       = [s for s in sections if s["part"] in selected_parts]

        for sec in filtered:
            header = (
                f"§{sec['notation']}  —  {sec['title']}"
                f"  · Part {sec['part']}  · score {sec['score']:.3f}"
            )
            with st.expander(header):
                paras = load_section_paragraphs(sec["notation"], selected_fibo)
                if paras:
                    for p in paras:
                        indent   = "&nbsp;" * 4 * (p.get("depth") or 0)
                        marker   = f"**{p['marker']}**" if p.get("marker") else ""
                        text     = p.get("text") or ""
                        score_md = (
                            f"  <span style='color:#16a34a;font-size:0.8em'>"
                            f"▶ {p['score']:.3f}</span>"
                            if p.get("score") else ""
                        )  # green to match FIBO color
                        st.markdown(
                            f"{indent}{marker} {text}{score_md}",
                            unsafe_allow_html=True,
                        )
                else:
                    st.caption("No paragraph-level GOVERNS found for this section.")
    else:
        st.info("No sections found. Try lowering the score threshold.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Coverage Analysis
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    fibo_stats, sec_stats = load_coverage_stats()

    fibo_total     = fibo_stats["total"]
    fibo_governed  = fibo_stats["governed"]
    fibo_gap       = fibo_total - fibo_governed
    fibo_pct       = fibo_governed / fibo_total * 100 if fibo_total else 0

    sec_total      = sec_stats["total"]
    sec_linked     = sec_stats["linked"]
    sec_gap        = sec_total - sec_linked
    sec_pct        = sec_linked / sec_total * 100 if sec_total else 0

    st.subheader("Regulatory Coverage Analysis")
    st.caption(
        "Identifies compliance risk areas — FIBO concepts ungoverned by Title 17 "
        "and CFR sections with no mapped financial concept."
    )

    # ── KPI metrics ───────────────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("FIBO Classes Governed",   f"{fibo_governed:,}",
              f"+{fibo_pct:.1f}%",        delta_color="normal")
    k2.metric("FIBO Classes Ungoverned", f"{fibo_gap:,}",
              f"+{100-fibo_pct:.1f}% — compliance risk", delta_color="inverse")
    k3.metric("CFR Sections Linked",     f"{sec_linked:,}",
              f"+{sec_pct:.1f}%",         delta_color="normal")
    k4.metric("CFR Sections Unlinked",   f"{sec_gap:,}",
              f"+{100-sec_pct:.1f}% — compliance risk", delta_color="inverse")

    # ── Donut charts ──────────────────────────────────────────────────────────
    dc1, dc2 = st.columns(2)

    with dc1:
        fibo_donut = go.Figure(go.Pie(
            labels        = ["Governed", "Ungoverned"],
            values        = [fibo_governed, fibo_gap],
            hole          = 0.6,
            marker_colors = ["#16a34a", "#ef4444"],
            textposition  = "outside",
            textinfo      = "label+percent",
            hovertemplate = "%{label}: %{value:,}<extra></extra>",
        ))
        fibo_donut.add_annotation(
            text      = f"<b>{fibo_total:,}</b><br>classes",
            x=0.5, y=0.5,
            font_size = 15,
            showarrow = False,
        )
        fibo_donut.update_layout(
            title_text  = "FIBO Class Coverage",
            height      = 340,
            margin      = dict(l=60, r=60, t=40, b=40),
            showlegend  = False,
        )
        st.plotly_chart(fibo_donut, use_container_width=True)

    with dc2:
        sec_donut = go.Figure(go.Pie(
            labels        = ["Linked", "Unlinked"],
            values        = [sec_linked, sec_gap],
            hole          = 0.6,
            marker_colors = ["#16a34a", "#ef4444"],
            textposition  = "outside",
            textinfo      = "label+percent",
            hovertemplate = "%{label}: %{value:,}<extra></extra>",
        ))
        sec_donut.add_annotation(
            text      = f"<b>{sec_total:,}</b><br>sections",
            x=0.5, y=0.5,
            font_size = 15,
            showarrow = False,
        )
        sec_donut.update_layout(
            title_text  = "CFR Section Coverage",
            height      = 340,
            margin      = dict(l=60, r=60, t=40, b=40),
            showlegend  = False,
        )
        st.plotly_chart(sec_donut, use_container_width=True)

    st.divider()

    # ── Drill-down panels ─────────────────────────────────────────────────────
    left, right = st.columns(2)

    # ── Left: Ungoverned FIBO ─────────────────────────────────────────────────
    with left:
        st.subheader(f"Ungoverned FIBO Classes  ({fibo_gap:,})")
        st.caption("Financial concepts not addressed by any Title 17 provision.")

        ug_rows = load_ungoverned_fibo()
        ug_df   = pd.DataFrame(ug_rows, columns=["module", "concept", "uri"])

        # Bar chart: count per module
        module_counts = (
            ug_df.groupby("module").size()
                 .reset_index(name="count")
                 .sort_values("count", ascending=True)
        )
        bar_fig = go.Figure(go.Bar(
            x=module_counts["count"],
            y=module_counts["module"],
            orientation="h",
            marker_color="#16a34a",
            hovertemplate="%{y}: %{x} ungoverned<extra></extra>",
        ))
        bar_fig.update_layout(
            height=max(250, len(module_counts) * 18),
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title="Ungoverned classes",
            yaxis=dict(tickfont=dict(size=10)),
        )
        st.plotly_chart(bar_fig, use_container_width=True)

        # Filter + table
        modules = ["All"] + sorted(ug_df["module"].unique())
        sel_mod = st.selectbox("Filter by module", modules, key="ug_mod")
        filtered_ug = ug_df if sel_mod == "All" else ug_df[ug_df["module"] == sel_mod]
        st.dataframe(
            filtered_ug[["module", "concept"]].reset_index(drop=True),
            use_container_width=True,
            height=300,
        )

    # ── Right: Unlinked SEC Sections ──────────────────────────────────────────
    with right:
        st.subheader(f"Unlinked CFR Sections  ({sec_gap:,})")
        st.caption("Regulatory provisions with no mapped FIBO financial concept.")

        ul_rows = load_unlinked_sections()
        ul_df   = pd.DataFrame(ul_rows, columns=["part_key", "part_name", "notation", "title"])

        # Bar chart: count per part
        part_counts = (
            ul_df.groupby("part_name").size()
                 .reset_index(name="count")
                 .sort_values("count", ascending=True)
        )
        bar_fig2 = go.Figure(go.Bar(
            x=part_counts["count"],
            y=part_counts["part_name"],
            orientation="h",
            marker_color="#c2410c",
            hovertemplate="%{y}: %{x} unlinked<extra></extra>",
        ))
        bar_fig2.update_layout(
            height=max(250, len(part_counts) * 18),
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title="Unlinked sections",
            yaxis=dict(tickfont=dict(size=10)),
        )
        st.plotly_chart(bar_fig2, use_container_width=True)

        # Filter + table
        parts = ["All"] + sorted(ul_df["part_name"].unique())
        sel_part = st.selectbox("Filter by Part", parts, key="ul_part")
        filtered_ul = ul_df if sel_part == "All" else ul_df[ul_df["part_name"] == sel_part]
        display_ul = filtered_ul[["part_name", "notation", "title"]].copy()
        display_ul["notation"] = "§" + display_ul["notation"]
        st.dataframe(
            display_ul.reset_index(drop=True),
            use_container_width=True,
            height=300,
        )
