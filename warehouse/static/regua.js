// Régua do «Arranjar Layout» (pedido do utilizador, 2026-11-19). Dois modos, só medem
// (não mexem no arranjo nem ficam guardados):
//  * Régua (R): cruz no rato. Em chão livre mostra a distância entre os dois
//    elementos mais perto na horizontal e na vertical (ex.: largura do corredor).
//    Em cima de um elemento mostra a distância de cada lado dele ao vizinho mais
//    perto. Paredes do «Espaço» (Cubo) contam como elementos.
//  * Medir (M): 2 cliques em dois elementos -> menor distância entre eles
//    (perpendicular às faces quando estão frente a frente; senão com dx e dy).
// Esc sai. A geometria vem de window.blocosDoCanvas() (index.html) em metros;
// PXM pixels por metro, como o canvas.
const NS = "http://www.w3.org/2000/svg";
const EPS = 1e-6;

const PXM = () => window.PXM_CANVAS || 20;
let modo = null;            // null | "cruz" | "dois"
let primeiro = null;        // modo «dois»: 1.º elemento clicado
let camada = null;

function fmt(v) { return v.toFixed(2).replace(".", ",") + " m"; }

function poligono(b) {
  const rot = (b.rot || 0) * Math.PI / 180;
  const cx = b.x + b.w / 2, cy = b.y + b.h / 2;
  return [[-b.w / 2, -b.h / 2], [b.w / 2, -b.h / 2], [b.w / 2, b.h / 2], [-b.w / 2, b.h / 2]]
    .map(([dx, dy]) => [cx + dx * Math.cos(rot) - dy * Math.sin(rot), cy + dx * Math.sin(rot) + dy * Math.cos(rot)]);
}

function nomeDe(b) { return b.nome || b.rack_tipo || b.type; }

// elementos: blocos do canvas (não o Cubo) + as 4 paredes do «Espaço»
function elementos() {
  const out = (window.blocosDoCanvas ? window.blocosDoCanvas() : [])
    .filter(b => b.type !== "cubo")
    .map(b => ({ id: b.id, nome: nomeDe(b), pts: poligono(b), fechado: true }));
  const comp = parseFloat(document.getElementById("hangar-comp")?.value) || 0;
  const larg = parseFloat(document.getElementById("hangar-larg")?.value) || 0;
  if (comp > 0 && larg > 0) {
    out.push({ id: "parede_n", nome: "parede", pts: [[0, 0], [comp, 0]], fechado: false });
    out.push({ id: "parede_s", nome: "parede", pts: [[0, larg], [comp, larg]], fechado: false });
    out.push({ id: "parede_o", nome: "parede", pts: [[0, 0], [0, larg]], fechado: false });
    out.push({ id: "parede_e", nome: "parede", pts: [[comp, 0], [comp, larg]], fechado: false });
  }
  return out;
}

function arestas(el) {
  const p = el.pts, out = [];
  const n = el.fechado ? p.length : p.length - 1;
  for (let i = 0; i < n; i++) out.push([p[i], p[(i + 1) % p.length]]);
  return out;
}

function dentro(pt, el) {
  if (!el.fechado) return false;
  let ins = false;
  const p = el.pts;
  for (let i = 0, j = p.length - 1; i < p.length; j = i++) {
    const [xi, yi] = p[i], [xj, yj] = p[j];
    if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi) ins = !ins;
  }
  return ins;
}

function area(el) {
  const p = el.pts;
  let a = 0;
  for (let i = 0, j = p.length - 1; i < p.length; j = i++) a += (p[j][0] + p[i][0]) * (p[j][1] - p[i][1]);
  return Math.abs(a / 2);
}

// raio de `o` na direção [dx, dy] (eixo): 1.º toque numa aresta dos elementos dados
function raio(o, dir, els) {
  let best = null;
  for (const el of els) {
    for (const [a, b] of arestas(el)) {
      let t;
      if (dir[0] !== 0) {           // horizontal: aresta tem de cruzar y = o.y
        const y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1]);
        if (o[1] < y0 - EPS || o[1] > y1 + EPS || Math.abs(b[1] - a[1]) < EPS) continue;
        const x = a[0] + (b[0] - a[0]) * (o[1] - a[1]) / (b[1] - a[1]);
        t = (x - o[0]) * dir[0];
      } else {
        const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]);
        if (o[0] < x0 - EPS || o[0] > x1 + EPS || Math.abs(b[0] - a[0]) < EPS) continue;
        const y = a[1] + (b[1] - a[1]) * (o[0] - a[0]) / (b[0] - a[0]);
        t = (y - o[1]) * dir[1];
      }
      if (t > EPS && (!best || t < best.t)) best = { t, el, pt: [o[0] + dir[0] * t, o[1] + dir[1] * t] };
    }
  }
  return best;
}

// ------------------------------------------------------------------ desenho
function garantirCamada() {
  const cont = document.getElementById("canvas-container");
  const canvas = document.getElementById("canvas");
  if (!cont || !canvas) return null;
  if (!camada) {
    camada = document.createElementNS(NS, "svg");
    camada.id = "regua-camada";
    camada.style.cssText = "position:absolute; left:0; top:0; pointer-events:none; z-index:5;";
    cont.appendChild(camada);
  }
  camada.setAttribute("width", canvas.getAttribute("width"));
  camada.setAttribute("height", canvas.getAttribute("height"));
  return camada;
}

function limpar() { if (camada) camada.innerHTML = ""; }

function el(tag, attrs, txt) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (txt != null) e.textContent = txt;
  camada.appendChild(e);
  return e;
}

const COR = "#dc2626";

function cota(a, b, rotulo, cor = COR) {
  const k = PXM();
  const [x1, y1, x2, y2] = [a[0] * k, a[1] * k, b[0] * k, b[1] * k];
  el("line", { x1, y1, x2, y2, stroke: cor, "stroke-width": 1.5 });
  // traços nas pontas, perpendiculares
  const L = Math.hypot(x2 - x1, y2 - y1) || 1;
  const nx = -(y2 - y1) / L * 5, ny = (x2 - x1) / L * 5;
  for (const [x, y] of [[x1, y1], [x2, y2]]) el("line", { x1: x - nx, y1: y - ny, x2: x + nx, y2: y + ny, stroke: cor, "stroke-width": 1.5 });
  const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
  const t = el("text", { x: mx, y: my - 7, "font-size": 12, "font-weight": 700, fill: "#fff", "text-anchor": "middle",
                          "font-family": "system-ui, sans-serif" }, rotulo);
  const bb = t.getBBox();
  const fundo = document.createElementNS(NS, "rect");
  for (const [kk, v] of Object.entries({ x: bb.x - 4, y: bb.y - 2, width: bb.width + 8, height: bb.height + 4, rx: 4, fill: cor })) fundo.setAttribute(kk, v);
  camada.insertBefore(fundo, t);
}

function realcar(elemento, cor) {
  if (!elemento) return;
  const k = PXM();
  if (elemento.fechado) {
    el("polygon", { points: elemento.pts.map(([x, y]) => `${x * k},${y * k}`).join(" "), fill: cor + "22", stroke: cor, "stroke-width": 2 });
  } else {
    const [a, b] = elemento.pts;
    el("line", { x1: a[0] * k, y1: a[1] * k, x2: b[0] * k, y2: b[1] * k, stroke: cor, "stroke-width": 3 });
  }
}

function aviso(txt) {
  const cont = document.getElementById("canvas-container");
  let d = document.getElementById("regua-aviso");
  if (!txt) { if (d) d.remove(); return; }
  if (!d) {
    d = document.createElement("div");
    d.id = "regua-aviso";
    d.style.cssText = "position:fixed; z-index:50; background:#111827e6; color:#fff; " +
      "font:12px system-ui, sans-serif; padding:5px 9px; border-radius:6px; pointer-events:none;";
    document.body.appendChild(d);
  }
  const r = cont.getBoundingClientRect();     // canto do canvas (fixo: não sai com o scroll)
  d.style.left = (r.left + 10) + "px";
  d.style.top = (r.top + 10) + "px";
  d.textContent = txt;
}

// ------------------------------------------------------------------ modo cruz
function cruz(p) {
  garantirCamada();
  limpar();
  const k = PXM();
  const W = +camada.getAttribute("width"), H = +camada.getAttribute("height");
  el("line", { x1: 0, y1: p[1] * k, x2: W, y2: p[1] * k, stroke: "#64748b", "stroke-width": 0.75, "stroke-dasharray": "4,4" });
  el("line", { x1: p[0] * k, y1: 0, x2: p[0] * k, y2: H, stroke: "#64748b", "stroke-width": 0.75, "stroke-dasharray": "4,4" });
  const els = elementos();
  const sob = els.filter(e => dentro(p, e)).sort((a, b) => area(a) - area(b))[0];
  if (sob) {
    // em cima de um elemento: de cada lado dele ao vizinho mais perto
    realcar(sob, "#2563eb");
    const outros = els.filter(e => e !== sob);
    for (const dir of [[-1, 0], [1, 0], [0, -1], [0, 1]]) {
      const saida = raio(p, dir, [sob]);
      if (!saida) continue;
      const viz = raio(saida.pt, dir, outros);
      if (viz) cota(saida.pt, viz.pt, fmt(viz.t));
    }
    return;
  }
  // chão livre: entre os dois elementos mais perto, na horizontal e na vertical
  for (const [d1, d2] of [[[-1, 0], [1, 0]], [[0, -1], [0, 1]]]) {
    const a = raio(p, d1, els), b = raio(p, d2, els);
    if (a && b) cota(a.pt, b.pt, fmt(a.t + b.t));
  }
}

// ------------------------------------------------------------------ modo 2 cliques
function segDist(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const L2 = dx * dx + dy * dy;
  const t = L2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2)) : 0;
  const q = [a[0] + t * dx, a[1] + t * dy];
  return [Math.hypot(p[0] - q[0], p[1] - q[1]), q];
}

function maisPerto(A, B) {
  // sobrepostos?
  if (A.pts.some(pt => dentro(pt, B)) || B.pts.some(pt => dentro(pt, A))) return { d: 0 };
  let best = null;
  for (const [X, Y] of [[A, B], [B, A]]) {
    for (const p of X.pts) {
      for (const [a, b] of arestas(Y)) {
        const [d, q] = segDist(p, a, b);
        if (!best || d < best.d - 1e-9) best = X === A ? { d, pa: p, pb: q } : { d, pa: q, pb: p };
      }
    }
  }
  return best;
}

function elementoEm(p) {
  return elementos().filter(e => e.fechado && dentro(p, e)).sort((a, b) => area(a) - area(b))[0] || null;
}

function desenharDois(seg, hover) {
  garantirCamada();
  limpar();
  if (primeiro) realcar(primeiro, "#2563eb");
  if (hover && hover !== primeiro) realcar(hover, "#94a3b8");
  if (!seg) return;
  realcar(seg.B, "#2563eb");
  const r = maisPerto(seg.A, seg.B);
  if (!r || r.d === 0) { aviso(`${seg.A.nome} e ${seg.B.nome} sobrepõem-se · Esc para sair`); return; }
  const dx = Math.abs(r.pb[0] - r.pa[0]), dy = Math.abs(r.pb[1] - r.pa[1]);
  const reto = dx < 1e-3 || dy < 1e-3;
  cota(r.pa, r.pb, reto ? fmt(r.d) : `${fmt(r.d)} (dx ${fmt(dx)} · dy ${fmt(dy)})`);
  if (!reto) {   // componentes a tracejado
    const k = PXM();
    el("line", { x1: r.pa[0] * k, y1: r.pa[1] * k, x2: r.pb[0] * k, y2: r.pa[1] * k, stroke: COR, "stroke-width": 1, "stroke-dasharray": "3,3" });
    el("line", { x1: r.pb[0] * k, y1: r.pa[1] * k, x2: r.pb[0] * k, y2: r.pb[1] * k, stroke: COR, "stroke-width": 1, "stroke-dasharray": "3,3" });
  }
  aviso(`${seg.A.nome} ↔ ${seg.B.nome}: ${fmt(r.d)} · clica noutro elemento para medir de novo · Esc para sair`);
}

// ------------------------------------------------------------------ ligar
let ultimaMedida = null;

function pontoDe(e) {
  const r = document.getElementById("canvas").getBoundingClientRect();
  return [(e.clientX - r.left) / PXM(), (e.clientY - r.top) / PXM()];
}

function botoes() {
  for (const [id, m] of [["btn-regua", "cruz"], ["btn-medir", "dois"]]) {
    const b = document.getElementById(id);
    if (!b) continue;
    const on = modo === m;
    b.style.background = on ? "var(--accent)" : "";
    b.style.color = on ? "#fff" : "";
  }
}

function definirModo(m) {
  modo = modo === m ? null : m;
  primeiro = null;
  ultimaMedida = null;
  limpar();
  botoes();
  const canvas = document.getElementById("canvas");
  if (canvas) canvas.style.cursor = modo ? "crosshair" : "default";
  aviso(modo === "cruz" ? "Régua: passa o rato pelo armazém · Esc para sair"
    : modo === "dois" ? "Medir: clica no 1.º elemento · Esc para sair" : null);
}

function ligar() {
  const canvas = document.getElementById("canvas");
  if (!canvas) return;
  document.getElementById("btn-regua")?.addEventListener("click", () => definirModo("cruz"));
  document.getElementById("btn-medir")?.addEventListener("click", () => definirModo("dois"));
  // nos modos de medida o clique não pode arrastar blocos: apanha-se antes (captura)
  canvas.addEventListener("mousedown", (e) => {
    if (!modo) return;
    e.stopPropagation();
    e.preventDefault();
    if (modo !== "dois") return;
    const alvo = elementoEm(pontoDe(e));
    if (!alvo) return;
    if (!primeiro || ultimaMedida) {
      primeiro = alvo;
      ultimaMedida = null;
      desenharDois(null, null);
      aviso(`Medir: ${alvo.nome} · clica no 2.º elemento · Esc para sair`);
    } else if (alvo.id !== primeiro.id) {
      ultimaMedida = { A: primeiro, B: alvo };
      desenharDois(ultimaMedida, null);
    }
  }, true);
  canvas.addEventListener("mousemove", (e) => {
    if (modo === "cruz") cruz(pontoDe(e));
    else if (modo === "dois" && !ultimaMedida) desenharDois(null, elementoEm(pontoDe(e)));
  });
  canvas.addEventListener("mouseleave", () => { if (modo === "cruz") limpar(); });
  document.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
    if (!canvas.offsetParent) return;          // só com o «Arranjar Layout» à vista
    if (e.key === "Escape" && modo) { definirModo(modo); }
    else if ((e.key === "r" || e.key === "R") && !e.ctrlKey && !e.metaKey) definirModo("cruz");
    else if ((e.key === "m" || e.key === "M") && !e.ctrlKey && !e.metaKey) definirModo("dois");
  });
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ligar);
else ligar();

// para testes no browser
window.regua = { modo: () => modo, definirModo, cruz, elementos, maisPerto, raio };
