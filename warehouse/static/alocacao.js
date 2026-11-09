// Separador «Alocação & Picking» (warehouse/alocacao.py + tempos.py).
//
// Pede /api/alocacao com o MESMO arranjo do «Renderizar 3D» (window.pedidoArranjo,
// definida no index.html) e desenha:
//   * em cima, a planta com o calor de TODAS as movimentações de picking (chão:
//     passagens/dia das idas e voltas; racks: visitas/dia) e, ao lado, a rack
//     escolhida de frente com o conteúdo por nível;
//   * uma faixa de indicadores do ano;
//   * o ano, um quadrado por dia útil (verde / âmbar = volta atrasada, aceite /
//     vermelho = o dia não encaixa), e o cronograma do dia escolhido
//     (/api/alocacao/dia/N): o que o operador do armazém e o comboio fazem e a
//     que horas, com os picks de cada preparação.
// Só apresentação: todos os números vêm do servidor.

const SVGNS = "http://www.w3.org/2000/svg";
const COR_CLASSE = { A: "#e06666", B: "#f6b26b", C: "#93c47d", "—": "#cccccc" };
let dados = null;           // última resposta do /api/alocacao
let selecionada = null;     // id da posição na vista de frente
let diaAtual = null;        // dia no cronograma
let crono = null;           // resposta do /api/alocacao/dia/N
let tarefaAberta = null;    // índice da tarefa com os picks à mostra
let mapaDia = false;        // true: a planta mostra os movimentos do dia (ou da tarefa aberta)

const $ = (id) => document.getElementById(id);
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const codigo = (sku) => String(sku).split("_____")[0];
// rótulo curto: sem o prefixo BEN10./SKT10. e só até ao 1.º "_" ou espaço
const rotuloCurto = (sku) => codigo(sku).replace(/^(BEN|SKT)\d+\./, "").split(/[_\s]/)[0];
const fmt = (v, d = 1) => (v == null ? "—" : Number(v).toLocaleString("pt-PT", { minimumFractionDigits: d, maximumFractionDigits: d }));

function cabe(texto, largPx, pxPorLetra = 5) {
  const n = Math.floor(largPx / pxPorLetra);
  return texto.length <= n ? texto : n >= 3 ? texto.slice(0, n - 1) + "…" : "";
}

function el(tag, attrs = {}, pai = null) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (pai) pai.appendChild(e);
  return e;
}

// branco -> amarelo -> vermelho (a mesma escala do Excel)
function corCalor(t) {
  t = Math.max(0, Math.min(1, t || 0));
  const a = t < 0.5 ? [255, 255, 255] : [255, 217, 102];
  const b = t < 0.5 ? [255, 217, 102] : [224, 102, 102];
  const u = t < 0.5 ? t / 0.5 : (t - 0.5) / 0.5;
  return `rgb(${a.map((x, i) => Math.round(x + (b[i] - x) * u)).join(",")})`;
}

function ocupacao(p) {
  const area = p.larg_nivel_m * p.prof_nivel_m;
  if (!area || !p.niveis.length) return 0;
  const usada = p.niveis.map((n) => Math.min(1, (n.caixas.reduce((s, c) => s + c.comp_m * c.larg_m, 0)
    + (n.par ? n.par.caixas.reduce((s, c) => s + c.comp_m * c.larg_m, 0) / 2 : 0)) / area));
  return usada.reduce((s, v) => s + v, 0) / usada.length;
}

// caixas de um nível, incluindo as OVERSIZE do par frente+trás que o ocupam
function caixasDoNivel(n) {
  return n.par ? n.caixas.concat(n.par.caixas) : n.caixas;
}

function melhorClasse(p) {
  const cls = new Set(p.niveis.flatMap((n) => caixasDoNivel(n).map((c) => c.classe)));
  return ["A", "B", "C", "—"].find((k) => cls.has(k)) || null;
}

function valorCalor(p, modo) {
  if (modo === "movimentos") return p.visitas_dia;
  if (modo === "ocupacao") return ocupacao(p);
  return p.picking_min_dia;
}

function posicoesDoSku(txt) {
  if (!dados || !txt) return new Set();
  const q = txt.trim().toLowerCase();
  if (!q) return new Set();
  return new Set(dados.posicoes.filter((p) => p.niveis.some((n) => caixasDoNivel(n).some(
    (c) => c.sku.toLowerCase().includes(q) || (c.descricao || "").toLowerCase().includes(q)))).map((p) => p.id));
}

// estado de um dia (cronograma encadeado: não há "voltas atrasadas"): vermelho
// = o armazém ou o comboio passam do turno, ou a reposição semanal não cabe
function estadoDia(d) {
  return d.encaixa === false ? "nao" : "ok";
}

// ----------------------------------------------------------------- planta
function desenharPlanta() {
  const svg = $("aloc-planta");
  svg.innerHTML = "";
  if (!dados) return;
  const modo = mapaDia && crono ? "dia" : $("aloc-modo").value;
  const pl = dados.planta || {};
  const caixas = [];
  for (const p of dados.posicoes) caixas.push([p.x, p.y, p.x + p.larg_m, p.y + p.prof_m]);
  for (const z of pl.zonas || []) caixas.push([z.x, z.y, z.x + z.larg_m, z.y + z.prof_m]);
  for (const o of pl.obstaculos || []) caixas.push([o.x, o.y, o.x + o.w, o.y + o.h]);
  if (pl.cubo) caixas.push([pl.cubo.x, pl.cubo.y, pl.cubo.x + pl.cubo.w, pl.cubo.y + pl.cubo.h]);
  const [px, py] = dados.ponto_carga;
  caixas.push([px - 1, py - 1, px + 1, py + 1]);
  const x0 = Math.min(...caixas.map((c) => c[0])) - 0.5, y0 = Math.min(...caixas.map((c) => c[1])) - 0.5;
  const x1 = Math.max(...caixas.map((c) => c[2])) + 0.5, y1 = Math.max(...caixas.map((c) => c[3])) + 0.5;
  svg.setAttribute("viewBox", `${x0} ${y0} ${x1 - x0} ${y1 - y0}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  const fonte = Math.max((x1 - x0) / 130, 0.22);

  if (pl.cubo) el("rect", { x: pl.cubo.x, y: pl.cubo.y, width: pl.cubo.w, height: pl.cubo.h, fill: "none",
                            stroke: "#94a3b8", "stroke-dasharray": "0.6 0.4", "stroke-width": 0.06 }, svg);
  for (const z of pl.zonas || []) {
    const cx = z.x + z.larg_m / 2, cy = z.y + z.prof_m / 2;
    const g = el("g", { transform: `rotate(${z.rot || 0} ${cx} ${cy})` }, svg);
    el("rect", { x: z.x, y: z.y, width: z.larg_m, height: z.prof_m, fill: "#f1f5f9", stroke: "#cbd5e1",
                 "stroke-width": 0.04 }, g);
    if (z.larg_m > 2) {
      const t = el("text", { x: cx, y: cy, "font-size": fonte * 0.8, fill: "#94a3b8", "text-anchor": "middle",
                             "dominant-baseline": "middle" }, g);
      t.textContent = cabe(z.nome, z.larg_m / fonte * 9, 5);
    }
  }
  for (const o of pl.obstaculos || []) {
    const cx = o.x + o.w / 2, cy = o.y + o.h / 2;
    el("rect", { x: o.x, y: o.y, width: o.w, height: o.h, fill: "#475569", opacity: 0.55,
                 transform: `rotate(${o.rot || 0} ${cx} ${cy})` }, svg);
  }
  // modo dia: os caminhos que o operador faz nesse dia (ou só na tarefa aberta)
  let mov = {};
  if (modo === "dia") {
    const t = tarefaAberta != null ? crono.tarefas[tarefaAberta] : null;
    mov = t ? (t.movimentos || {}) : (crono.movimentos_dia || {});
    const maxM = Math.max(...Object.values(mov), 1);
    // bulk de caixas: um percurso só (sai do destino, passa pelas posições, volta)
    if (t && t.percurso && t.percurso.pontos && t.percurso.pontos.length > 1) {
      el("polyline", { points: t.percurso.pontos.map((p) => p.join(",")).join(" "), fill: "none",
                       stroke: "#7c3aed", "stroke-opacity": 0.85, "stroke-width": 0.22,
                       "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
    }
    for (const [pid, n] of (t && t.percurso ? [] : Object.entries(mov))) {
      const cam = (pl.caminhos || {})[pid];
      if (!cam || cam.length < 2) continue;
      el("polyline", { points: cam.map((p) => p.join(",")).join(" "), fill: "none",
                       stroke: "#ea580c", "stroke-opacity": 0.35 + 0.5 * (n / maxM),
                       "stroke-width": 0.08 + 0.22 * Math.sqrt(n / maxM), "stroke-linejoin": "round",
                       "stroke-linecap": "round" }, svg);
    }
  }
  // chão: passagens/dia de todas as idas e voltas de picking
  const perc = modo === "movimentos" ? (pl.percursos || []) : [];
  const maxP = Math.max(...perc.map((c) => c[3]), 1e-9);
  for (const [x, y, lado, v] of perc) {
    const t = Math.sqrt(v / maxP);      // raiz: o ponto de carga não esmaga o resto
    el("rect", { x, y, width: lado, height: lado, fill: corCalor(0.35 + 0.65 * t), opacity: 0.25 + 0.6 * t }, svg);
  }

  // no modo do dia, um módulo real também conta as idas ao par frente+trás que o ocupa
  const movDe = (p) => {
    const par = (p.niveis.find((n) => n.par) || {}).par;
    return (mov[p.id] || 0) + (par ? (mov[par.id] || 0) : 0);
  };
  const vals = dados.posicoes.map((p) => (modo === "dia" ? movDe(p) : valorCalor(p, modo)));
  const max = Math.max(...vals, 1e-9);
  // escala POR TIPOLOGIA nos modos do ano (visitas e minutos): uma prateleira com
  // dezenas de caixas tem muito mais visitas do que um módulo de paletes, e com
  // uma escala única os porta-paletes ficavam todos quase brancos (2026-11-09).
  // A ocupação é uma fração (0-100 %); o modo do dia conta idas (escala única).
  const porTipo = modo === "movimentos" || modo === "minutos";
  const maxTipo = {};
  for (const p of dados.posicoes) {
    if (!p.par) maxTipo[p.rack_nome] = Math.max(maxTipo[p.rack_nome] || 1e-9, valorCalor(p, modo));
  }
  const escala = (p) => (porTipo ? maxTipo[p.rack_nome] : modo === "ocupacao" ? 1 : max);
  const destaque = posicoesDoSku($("aloc-sku").value);
  for (const p of dados.posicoes) {
    // posição «frente+trás» (OVERSIZE num par de módulos costas com costas): só o contorno, se tiver carga
    if (p.par) {
      if (p.niveis.some((n) => n.caixas.length)) {
        const r = el("rect", { x: p.x, y: p.y, width: p.larg_m, height: p.prof_m, fill: "none", class: "rack",
                               stroke: p.id === selecionada ? "#1d4ed8" : "#7c3aed", "stroke-width": 0.1,
                               "stroke-dasharray": "0.35 0.2", "pointer-events": "stroke" }, svg);
        r.addEventListener("click", () => selecionar(p.id));
        r.addEventListener("mousemove", (ev) => mostrarTooltip(ev, p));
        r.addEventListener("mouseleave", () => ($("aloc-tooltip").style.display = "none"));
      }
      continue;
    }
    const fill = modo === "classe" ? (COR_CLASSE[melhorClasse(p)] || "#f3f4f6")
      : modo === "dia" ? (movDe(p) ? corCalor(0.3 + 0.7 * movDe(p) / max) : "#f8fafc")
      : corCalor(Math.min(1, valorCalor(p, modo) / escala(p)));
    const r = el("rect", { x: p.x, y: p.y, width: p.larg_m, height: p.prof_m, fill, class: "rack",
                           stroke: p.id === selecionada ? "#1d4ed8" : destaque.has(p.id) ? "#2563eb" : "#9ca3af",
                           "stroke-width": p.id === selecionada || destaque.has(p.id) ? 0.12 : 0.03 }, svg);
    r.addEventListener("click", () => selecionar(p.id));
    r.addEventListener("mousemove", (ev) => mostrarTooltip(ev, p));
    r.addEventListener("mouseleave", () => ($("aloc-tooltip").style.display = "none"));
  }
  el("circle", { cx: px, cy: py, r: Math.max(fonte, 0.3), fill: "#2563eb", stroke: "#fff", "stroke-width": 0.06 }, svg);
  const t = el("text", { x: px, y: py - fonte * 1.6, "font-size": fonte, fill: "#1d4ed8", "text-anchor": "middle",
                         "font-weight": 700 }, svg);
  t.textContent = (pl.destino || "Destino").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

  const leg = $("aloc-legenda");
  if (modo === "dia") {
    const tk = tarefaAberta != null ? crono.tarefas[tarefaAberta] : null;
    const n = Object.values(mov).reduce((s, v) => s + v, 0);
    const pc = tk && tk.percurso;
    leg.innerHTML = (pc
      ? `<span><b>Dia ${crono.dia}</b> — ${esc(tk.nome)} (${tk.ini_h}–${tk.fim_h}): ${n} caixa(s) em
          ${pc.paragens} paragem(ns) · <b>percurso ${fmt(pc.metros, 0)} m</b>
          (uma ida e volta por caixa daria ${fmt(pc.idas_voltas_m, 0)} m)</span>
         <span><span class="swatch" style="background:#7c3aed"></span>percurso único (sai do destino e volta)</span>`
      : `<span><b>Dia ${crono.dia}</b> — ${tk ? esc(tk.nome) + ` (${tk.ini_h}–${tk.fim_h})` : "todas as operações do armazém"}:
          ${n} ida(s) e volta(s) a ${Object.keys(mov).length} posição(ões)</span>
         <span><span class="swatch" style="background:#ea580c"></span>caminho (mais grosso = mais vezes)</span>`)
      + `<button id="aloc-ver-ano" style="padding:2px 8px">Ver o ano</button>`;
    $("aloc-ver-ano").addEventListener("click", () => { mapaDia = false; desenharPlanta(); });
  } else if (modo === "classe") {
    leg.innerHTML = Object.entries(COR_CLASSE).map(([k, c]) =>
      `<span><span class="swatch" style="background:${c}"></span>${k === "—" ? "sem saídas" : "Classe " + k}</span>`).join("");
  } else {
    const un = { movimentos: "visitas/dia", minutos: "min/dia", ocupacao: "" }[modo];
    const vmax = modo === "ocupacao" ? "100 %"
      : Object.entries(maxTipo).map(([k, v]) => `${esc(k)} ${fmt(v, modo === "movimentos" ? 2 : 1)}`).join(" · ") + ` ${un}`;
    leg.innerHTML = `<span class="aloc-escala">racks${porTipo ? " (escala por tipologia, máx.)" : ""}: 0 <i></i> ${vmax}</span>`
      + (modo === "movimentos" ? `<span class="aloc-escala">chão: 0 <i style="opacity:.7"></i> ${fmt(maxP, 0)} passagens/dia</span>` : "")
      + `<span><span class="swatch" style="background:#2563eb; border-radius:50%"></span>${esc(pl.destino || "destino")} (para onde vai o picking)</span>`
      + (crono ? `<button id="aloc-ver-dia" style="padding:2px 8px">Ver o dia ${crono.dia}</button>` : "");
    if (crono) $("aloc-ver-dia").addEventListener("click", () => { mapaDia = true; desenharPlanta(); });
  }
}

function mostrarTooltip(ev, p) {
  const tt = $("aloc-tooltip");
  const skus = new Set(p.niveis.flatMap((n) => caixasDoNivel(n).map((c) => codigo(c.sku))));
  const nPar = p.niveis.filter((n) => n.par).length;
  tt.innerHTML = `<b>${esc(p.endereco)}</b><br>${esc(p.rack_nome)}<br>
    Visitas: <b>${fmt(p.visitas_dia, 2)}</b>/dia · Picking: <b>${fmt(p.picking_min_dia)}</b> min/dia<br>
    Ocupação: ${fmt(ocupacao(p) * 100, 0)} % · ${skus.size} SKU(s)`
    + (nPar ? `<br>${nPar} nível(eis) com OVERSIZE do par frente+trás (inclui ${fmt(p.visitas_par_dia || 0, 2)} visitas/dia)` : "");
  tt.style.display = "block";
  tt.style.left = `${ev.clientX + 14}px`;
  tt.style.top = `${ev.clientY + 14}px`;
}

// ----------------------------------------------------------------- vista de frente
function selecionar(id) {
  selecionada = id;
  desenharPlanta();
  desenharFrente();
}

function desenharFrente() {
  const p = dados && dados.posicoes.find((x) => x.id === selecionada);
  if (!p) return;
  $("aloc-frente-titulo").textContent = `Vista de frente — ${p.endereco}`;
  const topo = Math.max(...p.niveis.map((n) => n.base_m + n.altura_util_m), 0.5);
  const W = 300, H = 420, margem = 24;
  const s = Math.min((W - 2 * margem) / p.larg_nivel_m, (H - 26) / topo);
  const largPx = p.larg_nivel_m * s, x0 = (W - largPx) / 2, yChao = H - 12;
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMax meet");
  el("line", { x1: x0 - 4, y1: yChao, x2: x0 + largPx + 4, y2: yChao, stroke: "#374151", "stroke-width": 2 }, svg);
  for (const x of [x0 - 3, x0 + largPx + 3]) el("line", { x1: x, y1: yChao, x2: x, y2: yChao - topo * s,
                                                          stroke: "#6b7280", "stroke-width": 3 }, svg);
  for (const n of p.niveis) {
    const yb = yChao - n.base_m * s;
    el("line", { x1: x0 - 3, y1: yb, x2: x0 + largPx + 3, y2: yb, stroke: "#f59e0b", "stroke-width": 2 }, svg);
    el("rect", { x: x0, y: yb - n.altura_util_m * s, width: largPx, height: n.altura_util_m * s, fill: "none",
                 stroke: "#d1d5db", "stroke-dasharray": "3 3", "stroke-width": 0.8 }, svg);
    const lt = el("text", { x: 3, y: yb - 4, "font-size": 10, fill: "#6b7280" }, svg);
    lt.textContent = `N${n.nivel}`;
    const prof = p.prof_nivel_m || 1;
    // OVERSIZE do par frente+trás que ocupa este nível (frente e trás ao mesmo tempo)
    for (const c of (n.par ? n.par.caixas : [])) {
      const r = el("rect", { x: x0 + c.x_m * s, y: yb - c.alt_m * s, width: Math.max(c.comp_m * s - 1, 1),
                             height: Math.max(c.alt_m * s - 1, 1), fill: COR_CLASSE[c.classe] || "#ccc",
                             opacity: 0.55, stroke: "#7c3aed", "stroke-width": 1.2, "stroke-dasharray": "4 2" }, svg);
      const tit = el("title", {}, r);
      tit.textContent = `${c.sku}\nOVERSIZE × ${c.n_unidades} · ${c.pecas} peças · classe ${c.classe}` +
        `\nno par frente+trás (${n.par.endereco}): ocupa a frente e a trás deste módulo`;
      const rot = cabe(rotuloCurto(c.sku), c.comp_m * s - 3);
      if (rot && c.alt_m * s > 11) {
        const t = el("text", { x: x0 + (c.x_m + c.comp_m / 2) * s, y: yb - c.alt_m * s / 2 + 3, "font-size": 8,
                               "text-anchor": "middle", fill: "#4c1d95" }, svg);
        t.textContent = rot;
      }
    }
    for (const c of [...n.caixas].sort((a, b) => b.y_m - a.y_m)) {
      const atras = c.y_m > 0.05 * prof;
      const r = el("rect", { x: x0 + c.x_m * s, y: yb - c.alt_m * s, width: Math.max(c.comp_m * s - 1, 1),
                             height: Math.max(c.alt_m * s - 1, 1), fill: COR_CLASSE[c.classe] || "#ccc",
                             opacity: atras ? 0.45 : 0.95, stroke: "#374151", "stroke-width": 0.6 }, svg);
      const tit = el("title", {}, r);
      tit.textContent = `${c.sku}\n${c.unidade} × ${c.n_unidades} · ${c.pecas} peças · classe ${c.classe}` +
        (atras ? `\n(atrás, a ${fmt(c.y_m, 2)} m da frente)` : "");
      const rot = atras ? "" : cabe(rotuloCurto(c.sku), c.comp_m * s - 3);
      if (rot && c.alt_m * s > 11) {
        const t = el("text", { x: x0 + (c.x_m + c.comp_m / 2) * s, y: yb - c.alt_m * s / 2 + 3, "font-size": 8,
                               "text-anchor": "middle", fill: "#111" }, svg);
        t.textContent = rot;
      }
    }
  }
  $("aloc-frente").innerHTML = "";
  $("aloc-frente").appendChild(svg);
  const tabela = (lista, doPar) => {
    const por = {};
    for (const c of lista) {
      por[c.sku] = por[c.sku] || { sku: c.sku, desc: c.descricao, un: c.unidade, n: 0, pecas: 0, classe: c.classe };
      por[c.sku].n += c.n_unidades;
      por[c.sku].pecas += c.pecas;
    }
    return Object.values(por).map((x) =>
      `<tr${doPar ? ' style="color:#7c3aed"' : ""}><td title="${esc(x.desc)}">${doPar ? "↔ " : ""}${esc(codigo(x.sku))}</td>
        <td>${esc(x.un)}</td><td class="num">${x.n}</td>
        <td class="num">${x.pecas}</td><td><span class="swatch" style="background:${COR_CLASSE[x.classe]}"></span> ${esc(x.classe)}</td></tr>`).join("");
  };
  const linhas = [...p.niveis].reverse().map((n) => {
    const itens = tabela(n.caixas, false) + (n.par ? tabela(n.par.caixas, true) : "");
    const vis = n.visitas_dia + (n.par ? n.par.visitas_dia / 2 : 0);
    return `<tr><td colspan="5" style="background:var(--surface-2); color:var(--text-dim)">
      <b>N${n.nivel}</b> · ${fmt(n.altura_util_m, 2)} m útil · ${fmt(vis, 2)} visitas/dia`
      + (n.par ? ` · <span style="color:#7c3aed">reservado pelo par frente+trás (OVERSIZE, ↔)</span>` : "") + `</td></tr>`
      + (itens || `<tr><td colspan="5" style="color:var(--text-faint)">vazio</td></tr>`);
  }).join("");
  $("aloc-nivel-detalhe").innerHTML = `<table><thead><tr><th>SKU</th><th>Unid.</th><th class="num">Nº</th>
    <th class="num">Peças</th><th>Classe</th></tr></thead><tbody>${linhas}</tbody></table>`;
}

// ----------------------------------------------------------------- faixa e ano
function desenharFaixa() {
  const r = dados.resumo, t = dados.tempos || {};
  const dias = t.por_dia || [];
  const vermelhos = dias.filter((d) => estadoDia(d) === "nao");
  const comboio = dias.filter((d) => d.comboio_cabe === false);
  const armazem = dias.filter((d) => d.sobra_modelo_min < 0);
  const pior = dias.length ? dias.reduce((a, b) => (b.sobra_modelo_min < a.sobra_modelo_min ? b : a)) : null;
  const sem = t.reposicao_semanal;
  const val = t.validacao || [];
  const ind = (titulo, valor, sub, cor) => `<div class="aloc-ind"><small>${titulo}</small>
    <b style="color:${cor || "var(--text)"}">${valor}</b>${sub ? `<span>${sub}</span>` : ""}</div>`;
  $("aloc-faixa").innerHTML =
    ind("Dias que não encaixam", `${vermelhos.length} de ${dias.length}`, "sobra < 0 ou reposição semanal",
        vermelhos.length ? "#f87171" : "#4ade80")
    + ind("Armazém passa do turno", `${armazem.length} dia(s)`, "preparação acaba depois das 17:00",
          armazem.length ? "#f87171" : undefined)
    + ind("Comboio passa do turno", `${comboio.length} dia(s)`, "a última viagem acaba depois das 17:00",
          comboio.length ? "#f87171" : undefined)
    + (pior ? ind("Pior dia", `dia ${pior.dia}`, `sobra ${fmt(pior.sobra_modelo_min)} min`) : "")
    + (sem ? ind("Reposição semanal", `${sem.nao_cabem} de ${sem.reposicoes}`, `semanas sem lugar · pior ${fmt(sem.pior_min)} min`,
                 sem.nao_cabem ? "#f87171" : undefined) : "")
    + ind("Unidades alocadas", r.unidades_alocadas, r.unidades_nao_alocadas ? `${r.unidades_nao_alocadas} por alocar` : "nada por alocar",
          r.unidades_nao_alocadas ? "#f87171" : undefined)
    + ind("Validação contra o livro", `${val.filter((v) => v.ok).length} de ${val.length}`,
          `manuseio caixa ${fmt(t.calibracao?.caixa?.manuseio_min, 2)} min + deslocação`);
  const av = [...(dados.avisos || []), ...(t.avisos || [])];
  $("aloc-avisos").innerHTML = av.length ? `<div class="avisos" style="max-height:110px">⚠ ${av.map(esc).join("<br>⚠ ")}</div>` : "";
}

function desenharCalendario() {
  const dias = (dados.tempos && dados.tempos.por_dia) || [];
  $("aloc-cal").innerHTML = dias.map((d) =>
    `<i class="${estadoDia(d)}${d.dia === diaAtual ? " sel" : ""}" data-dia="${d.dia}"
        title="Dia ${d.dia} (semana ${Math.ceil(d.dia / 5)}): sobra do armazém ${fmt(d.sobra_modelo_min)} min${d.comboio_cabe === false ? " · o comboio passa do turno" : ""}${d.reposicao_semanal_min ? ` · reposição semanal ${fmt(d.reposicao_semanal_min)} min` : ""}"></i>`).join("");
  for (const i of $("aloc-cal").querySelectorAll("i")) i.addEventListener("click", () => escolherDia(+i.dataset.dia));
  $("aloc-dia").max = dias.length;
}

// ----------------------------------------------------------------- cronograma
async function escolherDia(dia) {
  const n = (dados && dados.tempos && dados.tempos.dias) || 0;
  if (!n) return;
  diaAtual = Math.max(1, Math.min(n, dia));
  $("aloc-dia").value = diaAtual;
  for (const i of $("aloc-cal").querySelectorAll("i")) i.classList.toggle("sel", +i.dataset.dia === diaAtual);
  const resp = await fetch(`/api/alocacao/dia/${diaAtual}`);
  const d = await resp.json();
  if (!resp.ok) { $("aloc-crono").innerHTML = `<span style="color:var(--danger)">${esc(d.detail)}</span>`; return; }
  crono = d;
  tarefaAberta = null;
  mapaDia = true;
  desenharCronograma();
  desenharPlanta();
}

const COR_TAREFA = (t) => {
  if (t.tipo === "margem") return "#cbd5e1";
  if (t.tipo === "reposição") return "#14b8a6";
  if (t.tipo === "dollys") return "#94a3b8";
  if (t.nome.includes("bulk")) return "#f59e0b";
  if (t.nome.includes("amanhã")) return "#64748b";
  if (t.nome.includes("Paletes")) return "#8b5cf6";
  return "#3b82f6";
};

function curto(t) {
  return t.nome.replace("Preparar bulk — ", "Bulk ").replace("Viagem do bulk — ", "Bulk ").replace("Preparar ", "")
    .replace("Volta ", "").replace(" de amanhã", " (amanhã)").replace("Margem (85 %)", "").replace("Caixas ", "C")
    .replace("Paletes ", "P").replace("Reposição semanal dos consumíveis", "Rep. semanal");
}

function desenharCronograma() {
  const c = crono;
  const est = estadoDia((dados.tempos.por_dia || []).find((x) => x.dia === c.dia) || {});
  const badge = { ok: '<span class="aloc-badge sim">ENCAIXA</span>', nao: '<span class="aloc-badge nao">NÃO ENCAIXA</span>' }[est];
  $("aloc-crono-titulo").innerHTML = `Cronograma do dia ${c.dia} (semana ${Math.ceil(c.dia / 5)}) ${badge}`;
  const i = c.indicadores;
  const ind = (t, v, cor) => `<div class="aloc-ind"><small>${t}</small><b style="color:${cor || "var(--text)"}">${v}</b></div>`;
  $("aloc-crono-ind").innerHTML = ind("Sobra do armazém", `${fmt(i.sobra_min)} min`, i.sobra_min < 0 ? "#f87171" : undefined)
    + ind("Armazém acaba", i.fim_armazem_h) + ind("Comboio acaba", i.fim_comboio_h)
    + ind("Comboio cabe no turno", i.comboio_cabe === false ? "NÃO" : "sim", i.comboio_cabe === false ? "#f87171" : undefined);

  // gráfico
  const W = 1000, esq = 118, dir = 12, topo = 22, alt = 30, gap = 18;
  const lanes = ["Armazém", "Comboio"];
  const H = topo + lanes.length * (alt + gap) + 6;
  const dur = c.turno.duracao_relogio_min;
  const x = (m) => esq + (m / dur) * (W - esq - dir);
  const [h0, m0] = c.turno.inicio_h.split(":").map(Number);
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  for (const [a, b] of c.turno.pausas) el("rect", { x: x(a), y: topo - 4, width: x(b) - x(a), height: H - topo,
                                                    fill: "#334155", opacity: 0.55 }, svg);
  for (let m = 0; m <= dur + 1e-6; m += 60) {
    el("line", { x1: x(m), y1: topo - 6, x2: x(m), y2: H - 4, stroke: "#2a3140", "stroke-width": 1 }, svg);
    const t = el("text", { x: x(m), y: 12, "font-size": 11, fill: "#9aa3b2", "text-anchor": "middle" }, svg);
    t.textContent = `${String(h0 + Math.floor((m0 + m) / 60)).padStart(2, "0")}:${String((m0 + m) % 60).padStart(2, "0")}`;
  }
  lanes.forEach((ln, li) => {
    const y = topo + li * (alt + gap);
    const t = el("text", { x: 6, y: y + alt / 2 + 4, "font-size": 12, fill: "#e6e9ef", "font-weight": 600 }, svg);
    t.textContent = ln === "Armazém" ? "Armazém (operador)" : "Comboio (mizu)";
    c.tarefas.forEach((tk, idx) => {
      if (tk.recurso !== ln) return;
      for (const [a, b] of tk.segmentos) {
        const r = el("rect", { x: x(a), y: tk.tipo === "margem" ? y + alt * 0.3 : y, width: Math.max(x(b) - x(a), 1),
                               height: tk.tipo === "margem" ? alt * 0.4 : alt, rx: 2, fill: COR_TAREFA(tk),
                               opacity: tk.tipo === "margem" ? 0.35 : 0.92,
                               stroke: tk.atraso_min > 0 ? "#f87171" : idx === tarefaAberta ? "#fff" : "none",
                               "stroke-width": tk.atraso_min > 0 || idx === tarefaAberta ? 2 : 0,
                               style: "cursor:pointer" }, svg);
        const tit = el("title", {}, r);
        tit.textContent = `${tk.nome}\n${tk.ini_h}–${tk.fim_h}` + (tk.n_picks ? `\n${tk.n_picks} pick(s), ${tk.unidades} unidade(s)` : "")
          + (tk.partida_h ? `\npartida planeada ${tk.partida_h}` : "") + (tk.atraso_min ? `\natraso ${fmt(tk.atraso_min)} min` : "")
          + (tk.nota ? `\n${tk.nota}` : "");
        r.addEventListener("click", () => abrirTarefa(idx));
        const rot = tk.tipo === "margem" ? "" : cabe(curto(tk), x(b) - x(a) - 4, 6);
        if (rot) {
          const tt = el("text", { x: x(a) + 3, y: y + alt / 2 + 4, "font-size": 10.5, fill: "#fff", "pointer-events": "none" }, svg);
          tt.textContent = rot;
        }
      }
      // partida planeada das voltas regulares
      if (ln === "Comboio" && tk.tipo === "volta" && tk.partida_h) {
        const [hh, mm] = tk.partida_h.split(":").map(Number);
        const mp = hh * 60 + mm - (h0 * 60 + m0);
        el("path", { d: `M ${x(mp) - 4} ${y - 6} L ${x(mp) + 4} ${y - 6} L ${x(mp)} ${y - 1} Z`, fill: "#e6e9ef" }, svg);
      }
    });
  });
  $("aloc-crono").innerHTML = "";
  $("aloc-crono").appendChild(svg);
  desenharTarefas();
}

function desenharTarefas() {
  const c = crono;
  const ordem = c.tarefas.map((t, i) => [t, i]).filter(([t]) => t.tipo !== "margem")
    .sort((a, b) => a[0].ini - b[0].ini || (a[0].recurso === "Armazém" ? -1 : 1));
  const html = ordem.map(([t, i]) => {
    const nota = [t.partida_h ? `partida planeada ${t.partida_h}` : "", t.atraso_min ? `atraso ${fmt(t.atraso_min)} min` : "",
                  t.percurso ? `percurso ${fmt(t.percurso.metros, 0)} m em ${t.percurso.paragens} paragens` : "", t.nota]
      .filter(Boolean).join(" · ");
    let linha = `<tr data-i="${i}" class="${i === tarefaAberta ? "sel" : ""} ${t.atraso_min > 0 ? "atraso" : ""}">
      <td>${t.ini_h}</td><td>${t.fim_h}</td><td>${esc(t.recurso)}</td><td>${esc(t.nome)}</td>
      <td class="num">${t.n_picks || ""}</td><td class="num">${t.unidades || ""}</td><td>${esc(nota)}</td></tr>`;
    if (i === tarefaAberta) {
      const ps = c.picks[t.chave] || [];
      linha += `<tr class="picks"><td colspan="7">${ps.length ? `<table><thead><tr><th>SKU</th><th>Descrição</th><th>Unidade</th>
        <th class="num">Nº</th><th class="num">Peças</th><th>Posto</th><th>Endereço(s)</th><th class="num">Tempo (min)</th></tr></thead><tbody>
        ${ps.map((k) => `<tr><td>${esc(codigo(k.sku))}</td><td>${esc(k.descricao)}</td><td>${esc(k.unidade)}</td>
          <td class="num">${k.n}</td><td class="num">${fmt(k.pecas, 0)}</td><td>${esc(k.posto)}</td>
          <td>${esc(k.enderecos.join(" | ") || "sem localização")}</td><td class="num">${fmt(k.tempo_min, 2)}</td></tr>`).join("")}
        </tbody></table>` : '<span class="dica">Sem picks nesta tarefa.</span>'}</td></tr>`;
    }
    return linha;
  }).join("");
  $("aloc-tarefas").querySelector("tbody").innerHTML = html;
  for (const tr of $("aloc-tarefas").querySelectorAll("tbody tr[data-i]"))
    tr.addEventListener("click", () => abrirTarefa(+tr.dataset.i));
}

function abrirTarefa(i) {
  tarefaAberta = tarefaAberta === i ? null : i;
  mapaDia = true;
  desenharCronograma();
  desenharPlanta();
}

function preencherSkus() {
  const skus = new Map();
  for (const p of dados.posicoes) for (const n of p.niveis) for (const c of n.caixas) skus.set(c.sku, c.descricao);
  $("aloc-skus").innerHTML = [...skus].sort().map(([s, d]) => `<option value="${esc(codigo(s))}">${esc(d)}</option>`).join("");
}

// ----------------------------------------------------------------- pedido
async function alocar() {
  const btn = $("btn-alocar");
  if (!window.pedidoArranjo || !window.temArranjo || !window.temArranjo()) {
    $("aloc-estado").textContent = "O arranjo não tem racks: gera o bloco de racks ou acrescenta filas manuais no «Arranjar Layout».";
    return;
  }
  btn.disabled = true;
  btn.textContent = "A alocar…";
  $("aloc-estado").textContent = "A alocar e a simular um ano de dias úteis (uns segundos)…";
  try {
    const corpo = Object.assign(window.pedidoArranjo(), {
      carros_por_dia: parseInt($("aloc-carros").value, 10) || 1,
    });
    const resp = await fetch("/api/alocacao", { method: "POST", headers: { "Content-Type": "application/json" },
                                                body: JSON.stringify(corpo) });
    const d = await resp.json();
    if (!resp.ok) throw new Error(d.detail || resp.statusText);
    dados = d;
    if (selecionada && !dados.posicoes.some((p) => p.id === selecionada)) selecionada = null;
    if (!selecionada && dados.posicoes.length)   // abre logo a posição com mais movimentações
      selecionada = dados.posicoes.reduce((a, b) => (b.visitas_dia > a.visitas_dia ? b : a)).id;
    $("aloc-download").style.display = "";
    desenharEstado();
    const r = d.resumo;
    $("aloc-estado").innerHTML = `<b>${r.unidades_alocadas}</b> unidades em <b>${r.posicoes}</b> posições` +
      (r.unidades_nao_alocadas ? ` · <b style="color:var(--danger)">${r.unidades_nao_alocadas} por alocar</b>` : " · nada por alocar") +
      ` · ${corpo.carros_por_dia} carro(s)/dia · ${d.tempos ? d.tempos.dias : "—"} dias úteis simulados`;
    preencherSkus();
    desenharPlanta();
    desenharFrente();
    desenharFaixa();
    desenharCalendario();
    const dias = (d.tempos && d.tempos.por_dia) || [];
    const pior = dias.length ? dias.reduce((a, b) => (b.sobra_modelo_min < a.sobra_modelo_min ? b : a)).dia : 1;
    await escolherDia(diaAtual && diaAtual <= dias.length ? diaAtual : pior);
  } catch (e) {
    $("aloc-estado").innerHTML = `<span style="color:var(--danger)">Erro: ${esc(e.message)}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Alocar ao arranjo";
  }
}

// estado (incremental / de raiz) e botão para aceitar alterações à BOM ou à lógica
function desenharEstado() {
  const e = dados.estado || {};
  const b = e.bom || {};
  const partes = [];
  if (b.novos?.length) partes.push(`${b.novos.length} SKU(s) novo(s)`);
  if (b.removidos?.length) partes.push(`${b.removidos.length} removido(s)`);
  if (b.alterados?.length) partes.push(`${b.alterados.length} com unidade/quantidade diferente`);
  $("aloc-estado-modo").innerHTML = `<b>${esc(e.modo || "—")}</b> · ${esc(e.motivo || "")}`
    + (partes.length ? ` · BOM: ${partes.join(", ")}` : "")
    + (e.calibracao ? ` · tempos calibrados no armazém dimensionado (${esc(e.calibracao)})` : "");
  const bt = $("btn-guardar-estado");
  bt.style.display = e.pode_guardar ? "" : "none";
  bt.title = (e.logica_mudou ? "A lógica de alocação mudou. " : "")
    + (partes.length ? `Alterações à BOM: ${partes.join(", ")}. ` : "")
    + "Aceitar esta alocação como o estado de referência: as próximas partem dela.";
}

async function guardarEstado() {
  const resp = await fetch("/api/alocacao/guardar-estado", { method: "POST" });
  const d = await resp.json();
  if (!resp.ok) { alert(d.detail || resp.statusText); return; }
  dados.estado = d.estado;
  dados.estado.motivo = "estado guardado: as próximas alocações partem desta";
  desenharEstado();
}

function proximoVermelho() {
  const dias = (dados && dados.tempos && dados.tempos.por_dia) || [];
  const d = dias.find((x) => x.dia > (diaAtual || 0) && estadoDia(x) === "nao") || dias.find((x) => estadoDia(x) === "nao");
  if (d) escolherDia(d.dia);
}

$("btn-alocar").addEventListener("click", alocar);
$("btn-guardar-estado").addEventListener("click", guardarEstado);
$("aloc-modo").addEventListener("change", desenharPlanta);
$("aloc-sku").addEventListener("input", () => { if (dados) desenharPlanta(); });
$("aloc-dia").addEventListener("change", () => escolherDia(parseInt($("aloc-dia").value, 10) || 1));
$("aloc-dia-ant").addEventListener("click", () => escolherDia((diaAtual || 1) - 1));
$("aloc-dia-seg").addEventListener("click", () => escolherDia((diaAtual || 0) + 1));
$("aloc-dia-pior").addEventListener("click", () => {
  const dias = (dados && dados.tempos && dados.tempos.por_dia) || [];
  if (dias.length) escolherDia(dias.reduce((a, b) => (b.sobra_modelo_min < a.sobra_modelo_min ? b : a)).dia);
});
$("aloc-dia-prox").addEventListener("click", proximoVermelho);
