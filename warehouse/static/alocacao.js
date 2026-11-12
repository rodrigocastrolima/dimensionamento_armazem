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
let nivelSel = null;        // nível da posição na vista de cima
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
  if (modo === "tapadas") return p.tapadas || 0;
  return p.picking_min_dia;
}

// SKUs que a pesquisa escolhe: o código exato primeiro; senão código ou descrição que contenham o texto
function skusDaPesquisa(txt) {
  if (!dados || !txt) return [];
  const q = txt.trim().toLowerCase();
  if (!q) return [];
  const todos = Object.keys(dados.skus || {});
  const exato = todos.filter((s) => codigo(s).toLowerCase() === q || s.toLowerCase() === q);
  if (exato.length) return exato;
  return todos.filter((s) => s.toLowerCase().includes(q)
    || ((dados.skus[s] || {}).descricao || "").toLowerCase().includes(q));
}

// onde está cada unidade de um SKU: [{pid, endereco, nivel, n}] (inclui as OVERSIZE dos pares)
function enderecosDoSku(sku) {
  const out = [];
  for (const p of dados.posicoes) {
    for (const n of p.niveis) {
      const k = n.caixas.filter((c) => c.sku === sku).reduce((s, c) => s + c.n_unidades, 0);
      const t = n.caixas.filter((c) => c.sku === sku && (c.tapada_por || []).length).reduce((s, c) => s + c.n_unidades, 0);
      if (k) out.push({ pid: p.id, endereco: p.endereco, nivel: n.nivel, n: k, t, dist: p.picking_min_dia });
    }
  }
  return out;
}

function posicoesDoSku(txt) {
  if (!dados || !txt) return new Set();
  const q = txt.trim().toLowerCase();
  if (!q) return new Set();
  const alvo = new Set(skusDaPesquisa(txt));
  return new Set(dados.posicoes.filter((p) => p.niveis.some((n) => caixasDoNivel(n).some(
    (c) => alvo.has(c.sku)))).map((p) => p.id));
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
  const comPesquisa = destaque.size > 0;
  for (const p of dados.posicoes) {
    // posição «frente+trás» (OVERSIZE num par de módulos costas com costas): só o contorno, se tiver carga
    if (p.par) {
      if (p.niveis.some((n) => n.caixas.length)) {
        const r = el("rect", { x: p.x, y: p.y, width: p.larg_m, height: p.prof_m, fill: "none", class: "rack",
                               stroke: p.id === selecionada || destaque.has(p.id) ? "#1d4ed8" : "#7c3aed",
                               "stroke-width": destaque.has(p.id) ? 0.25 : 0.1,
                               "stroke-dasharray": "0.35 0.2", "pointer-events": "stroke" }, svg);
        r.addEventListener("click", () => selecionar(p.id));
        r.addEventListener("mousemove", (ev) => mostrarTooltip(ev, p));
        r.addEventListener("mouseleave", () => ($("aloc-tooltip").style.display = "none"));
      }
      continue;
    }
    const fill = modo === "classe" ? (COR_CLASSE[melhorClasse(p)] || "#f3f4f6")
      : modo === "tapadas" ? (p.tapadas ? corCalor(0.3 + 0.7 * p.tapadas / max) : "#f8fafc")
      : modo === "dia" ? (movDe(p) ? corCalor(0.3 + 0.7 * movDe(p) / max) : "#f8fafc")
      : corCalor(Math.min(1, valorCalor(p, modo) / escala(p)));
    const r = el("rect", { x: p.x, y: p.y, width: p.larg_m, height: p.prof_m, fill, class: "rack",
                           stroke: destaque.has(p.id) ? "#1d4ed8" : p.id === selecionada ? "#1d4ed8" : "#9ca3af",
                           "stroke-width": destaque.has(p.id) ? 0.25 : p.id === selecionada ? 0.12 : 0.03,
                           opacity: comPesquisa && !destaque.has(p.id) ? 0.25 : 1 }, svg);
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
  } else if (modo === "tapadas") {
    const comT = dados.posicoes.filter((p) => !p.par && p.tapadas);
    leg.innerHTML = `<span><b>${dados.resumo.tapadas || 0}</b> unidade(s) atrás de uma caixa de OUTRO componente,
        em ${comT.length} módulo(s)</span>
      <span class="aloc-escala">por módulo: 0 <i></i> ${fmt(max, 0)}</span>
      <span class="dica" style="margin:0">clica num módulo: a vista de cima mostra quem tapa quem</span>`;
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
    + (p.tapadas ? `<br><span style="color:#b91c1c">${p.tapadas} unidade(s) atrás de outro componente</span>` : "")
    + (nPar ? `<br>${nPar} nível(eis) com OVERSIZE do par frente+trás (inclui ${fmt(p.visitas_par_dia || 0, 2)} visitas/dia)` : "");
  tt.style.display = "block";
  tt.style.left = `${ev.clientX + 14}px`;
  tt.style.top = `${ev.clientY + 14}px`;
}

// ----------------------------------------------------------------- vista de frente
// nível a mostrar de cima: o do componente pesquisado; senão o com mais caixas tapadas; senão o 1.º com caixas
function nivelPadrao(p) {
  const alvo = new Set(skusDaPesquisa($("aloc-sku").value).slice(0, 1));
  const doSku = p.niveis.find((n) => n.caixas.some((c) => alvo.has(c.sku)));
  if (doSku) return doSku.nivel;
  const comT = [...p.niveis].sort((a, b) => (b.tapadas || 0) - (a.tapadas || 0))[0];
  if (comT && comT.tapadas) return comT.nivel;
  return (p.niveis.find((n) => n.caixas.length) || p.niveis[0] || {}).nivel ?? null;
}

function selecionar(id, nivel = null) {
  selecionada = id;
  const p = dados && dados.posicoes.find((x) => x.id === id);
  nivelSel = nivel ?? (p ? nivelPadrao(p) : null);
  desenharPlanta();
  desenharFrente();
}

function escolherNivel(nivel) {
  nivelSel = nivel;
  desenharFrente();
}

// o nível escolhido visto de cima, à escala: vão na horizontal, profundidade na vertical, frente em baixo
function desenharCima(p, alvo) {
  const box = $("aloc-cima");
  const n = p.niveis.find((x) => x.nivel === nivelSel);
  if (!n) { box.innerHTML = ""; return; }
  const W = 560, H = 160, topo = 16, baixo = 16;
  const larg = p.larg_nivel_m, prof = p.prof_nivel_m || 1;
  const s = Math.min((W - 24) / larg, (H - topo - baixo) / prof);
  const x0 = (W - larg * s) / 2, yF = topo + prof * s;
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  const tit = el("text", { x: 4, y: 10, "font-size": 9, fill: "#374151", "font-weight": 700 }, svg);
  tit.textContent = `N${n.nivel} visto de cima · ${fmt(larg, 2)} × ${fmt(prof, 2)} m`;
  if (n.tapadas) {
    const tt = el("text", { x: W - 4, y: 10, "font-size": 9, fill: "#b91c1c", "text-anchor": "end", "font-weight": 700 }, svg);
    tt.textContent = `${n.tapadas} tapada(s)`;
  }
  el("rect", { x: x0, y: topo, width: larg * s, height: prof * s, fill: "#f9fafb", stroke: "#d1d5db",
               "stroke-width": 0.8 }, svg);
  for (const x of [x0 - 2, x0 + larg * s + 2]) el("line", { x1: x, y1: topo, x2: x, y2: yF, stroke: "#6b7280",
                                                            "stroke-width": 3 }, svg);
  el("line", { x1: x0 - 3, y1: yF + 1, x2: x0 + larg * s + 3, y2: yF + 1, stroke: "#f59e0b", "stroke-width": 2 }, svg);
  const fr = el("text", { x: W / 2, y: H - 3, "font-size": 8.5, fill: "#6b7280", "text-anchor": "middle" }, svg);
  fr.textContent = "▲ frente — corredor (onde está o operador)";
  if (n.par) {
    const t = el("text", { x: W / 2, y: topo + prof * s / 2 + 3, "font-size": 9, fill: "#7c3aed", "text-anchor": "middle" }, svg);
    t.textContent = "nível ocupado pelo par frente+trás (OVERSIZE)";
  }
  for (const c of n.caixas) {
    const tap = (c.tapada_por || []).length > 0, doSku = alvo.has(c.sku);
    const bx = x0 + c.x_m * s, by = yF - (c.y_m + c.larg_m) * s, bw = c.comp_m * s, bh = c.larg_m * s;
    const r = el("rect", { x: bx + 0.5, y: by + 0.5, width: Math.max(bw - 1, 1), height: Math.max(bh - 1, 1),
                           fill: COR_CLASSE[c.classe] || "#ccc", opacity: alvo.size && !doSku ? 0.35 : 0.9,
                           stroke: doSku ? "#1d4ed8" : tap ? "#b91c1c" : "#374151",
                           "stroke-width": doSku ? 2.5 : tap ? 2 : 0.6, "stroke-dasharray": tap && !doSku ? "4 2" : "none" }, svg);
    const ti = el("title", {}, r);
    ti.textContent = `${c.sku}\n${c.unidade} × ${c.n_unidades} · ${c.pecas} peças · classe ${c.classe}` +
      `\n${c.atras ? `atrás (a ${fmt(c.y_m, 2)} m da frente)` : "à frente"}` +
      (tap ? `\n⚠ tapada por: ${c.tapada_por.map(codigo).join(", ")}` : "");
    const rot = cabe(rotuloCurto(c.sku) + (c.n_unidades > 1 ? ` ×${c.n_unidades}` : ""), bw - 2, 4.6);
    if (rot && bh > 9) {
      const t = el("text", { x: bx + bw / 2, y: by + bh / 2 + 3, "font-size": 7.5, "text-anchor": "middle",
                             fill: tap ? "#7f1d1d" : "#111", "pointer-events": "none" }, svg);
      t.textContent = (tap ? "⚠" : "") + rot;
    }
  }
  box.innerHTML = "";
  box.appendChild(svg);
}

function desenharFrente() {
  const p = dados && dados.posicoes.find((x) => x.id === selecionada);
  if (!p) return;
  $("aloc-frente-titulo").textContent = `Vista de frente — ${p.endereco}`;
  const alvoFrente = new Set(skusDaPesquisa($("aloc-sku").value).slice(0, 1));
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
    const escolhido = n.nivel === nivelSel;
    const zona = el("rect", { x: x0, y: yb - n.altura_util_m * s, width: largPx, height: n.altura_util_m * s,
                              fill: escolhido ? "rgba(37,99,235,0.06)" : "transparent",
                              stroke: escolhido ? "#2563eb" : "#d1d5db", "stroke-dasharray": escolhido ? "none" : "3 3",
                              "stroke-width": escolhido ? 1.5 : 0.8, cursor: "pointer" }, svg);
    zona.addEventListener("click", () => escolherNivel(n.nivel));
    const lt = el("text", { x: 3, y: yb - 4, "font-size": 10, fill: escolhido ? "#2563eb" : "#6b7280",
                            "font-weight": escolhido ? 700 : 400, cursor: "pointer" }, svg);
    lt.textContent = `N${n.nivel}`;
    lt.addEventListener("click", () => escolherNivel(n.nivel));
    if (n.tapadas) {
      const tw = el("text", { x: 3, y: yb - 15, "font-size": 9, fill: "#b91c1c", "font-weight": 700 }, svg);
      tw.textContent = `⚠${n.tapadas}`;
    }
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
      const doSku = alvoFrente.has(c.sku);
      const r = el("rect", { x: x0 + c.x_m * s, y: yb - c.alt_m * s, width: Math.max(c.comp_m * s - 1, 1),
                             height: Math.max(c.alt_m * s - 1, 1), fill: COR_CLASSE[c.classe] || "#ccc",
                             opacity: alvoFrente.size && !doSku ? 0.25 : atras ? 0.45 : 0.95,
                             stroke: doSku ? "#1d4ed8" : "#374151", "stroke-width": doSku ? 2.5 : 0.6 }, svg);
      r.addEventListener("click", () => escolherNivel(n.nivel));
      const tit = el("title", {}, r);
      tit.textContent = `${c.sku}\n${c.unidade} × ${c.n_unidades} · ${c.pecas} peças · classe ${c.classe}` +
        (atras ? `\n(atrás, a ${fmt(c.y_m, 2)} m da frente)` : "");
      const rot = atras ? "" : cabe(rotuloCurto(c.sku), c.comp_m * s - 3);
      if (rot && c.alt_m * s > 11) {
        const t = el("text", { x: x0 + (c.x_m + c.comp_m / 2) * s, y: yb - c.alt_m * s / 2 + 3, "font-size": 8,
                               "text-anchor": "middle", fill: "#111", "pointer-events": "none" }, svg);
        t.textContent = rot;
      }
    }
    // caixas tapadas por OUTRO componente: contorno vermelho tracejado por cima (escondidas atrás da da frente)
    for (const c of n.caixas.filter((c) => (c.tapada_por || []).length)) {
      el("rect", { x: x0 + c.x_m * s + 1.5, y: yb - c.alt_m * s + 1.5, width: Math.max(c.comp_m * s - 4, 1),
                   height: Math.max(c.alt_m * s - 4, 1), fill: "none", stroke: "#b91c1c", "stroke-width": 1.5,
                   "stroke-dasharray": "4 2", "pointer-events": "none" }, svg);
    }
  }
  $("aloc-frente").innerHTML = "";
  $("aloc-frente").appendChild(svg);
  desenharCima(p, alvoFrente);
  const tabela = (lista, doPar) => {
    const por = {};
    for (const c of lista) {
      por[c.sku] = por[c.sku] || { sku: c.sku, desc: c.descricao, un: c.unidade, n: 0, pecas: 0, classe: c.classe,
                                   frente: false, tras: false, tapa: new Set() };
      por[c.sku].n += c.n_unidades;
      por[c.sku].pecas += c.pecas;
      if (c.atras) por[c.sku].tras = true; else por[c.sku].frente = true;
      for (const t of c.tapada_por || []) por[c.sku].tapa.add(t);
    }
    const fila = (x) => (doPar ? "par" : x.frente && x.tras ? "frente e trás" : x.tras ? "trás" : "frente")
      + (x.tapa.size ? ` <span class="aloc-tapada" title="Para a tirar é preciso mexer primeiro em: ${esc([...x.tapa].map(codigo).join(", "))}">⚠ atrás de ${esc([...x.tapa].map(rotuloCurto).join(", "))}</span>` : "");
    return Object.values(por).map((x) =>
      `<tr${doPar ? ' style="color:#7c3aed"' : ""}${alvoFrente.has(x.sku) ? ' class="aloc-sku-alvo"' : ""}><td title="${esc(x.desc)}">${doPar ? "↔ " : ""}${esc(codigo(x.sku))}</td>
        <td>${esc(x.un)}</td><td class="num">${x.n}</td>
        <td class="num">${x.pecas}</td><td><span class="swatch" style="background:${COR_CLASSE[x.classe]}"></span> ${esc(x.classe)}</td>
        <td>${fila(x)}</td></tr>`).join("");
  };
  const linhas = [...p.niveis].reverse().map((n) => {
    const itens = tabela(n.caixas, false) + (n.par ? tabela(n.par.caixas, true) : "");
    const vis = n.visitas_dia + (n.par ? n.par.visitas_dia / 2 : 0);
    return `<tr class="aloc-nivel-cab${n.nivel === nivelSel ? " sel" : ""}" data-nivel="${n.nivel}"><td colspan="6" style="background:var(--surface-2); color:var(--text-dim)">
      <b>N${n.nivel}</b> · ${fmt(n.altura_util_m, 2)} m útil · ${fmt(vis, 2)} visitas/dia`
      + (n.tapadas ? ` · <span class="aloc-tapada">⚠ ${n.tapadas} tapada(s)</span>` : "")
      + (n.par ? ` · <span style="color:#7c3aed">reservado pelo par frente+trás (OVERSIZE, ↔)</span>` : "") + `</td></tr>`
      + (itens || `<tr><td colspan="6" style="color:var(--text-faint)">vazio</td></tr>`);
  }).join("");
  $("aloc-nivel-detalhe").innerHTML = `<table><thead><tr><th>SKU</th><th>Unid.</th><th class="num">Nº</th>
    <th class="num">Peças</th><th>Classe</th><th>Fila</th></tr></thead><tbody>${linhas}</tbody></table>`;
  for (const tr of $("aloc-nivel-detalhe").querySelectorAll("tr[data-nivel]"))
    tr.addEventListener("click", () => escolherNivel(+tr.dataset.nivel));
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
    $("aloc-estado").textContent = "O arranjo não tem racks: carrega o layout no «Arranjar Layout» («Carregar»).";
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
    {   // nível da vista de cima: mantém-se se ainda existir nessa posição
      const p = dados.posicoes.find((x) => x.id === selecionada);
      if (p && !p.niveis.some((n) => n.nivel === nivelSel)) nivelSel = nivelPadrao(p);
    }
    $("aloc-download").style.display = "";
    desenharEstado();
    const r = d.resumo;
    $("aloc-estado").innerHTML = `<b>${r.unidades_alocadas}</b> unidades em <b>${r.posicoes}</b> posições` +
      (r.unidades_nao_alocadas ? ` · <b style="color:var(--danger)">${r.unidades_nao_alocadas} por alocar</b>` : " · nada por alocar") +
      ` · ${corpo.carros_por_dia} carro(s)/dia · ${d.tempos ? d.tempos.dias : "—"} dias úteis simulados`;
    preencherSkus();
    desenharPlanta();
    desenharFrente();
    desenharFichaSku();
    desenharListaSkus();
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
    + (e.calibracao ? ` · tempos calibrados no layout de referência (${esc(e.calibracao)})` : "");
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
const NOME_ZONA = { 0: "Bulk (sai todos os dias)", 1: "Voltas regulares do comboio",
                    2: "Consumíveis (reposição semanal)", 3: "Sem picking" };

// ficha do componente pesquisado: onde está cada unidade (clicável) e o que o algoritmo sabe dele
function desenharFichaSku() {
  const ficha = $("aloc-sku-ficha");
  if (!ficha) return;
  const txt = $("aloc-sku").value;
  const lista = skusDaPesquisa(txt);
  if (!dados || !txt.trim()) { ficha.innerHTML = ""; return; }
  if (!lista.length) { ficha.innerHTML = '<span class="dica">Nenhum componente com esse código ou descrição.</span>'; return; }
  if (lista.length > 1) {
    ficha.innerHTML = `<b>${lista.length} componentes</b> correspondem — escolhe um: ` + lista.slice(0, 12).map((s) =>
      `<a href="#" data-sku="${esc(codigo(s))}">${esc(codigo(s))}</a> <span class="dica">${esc((dados.skus[s] || {}).descricao || "")}</span>`)
      .join(" · ") + (lista.length > 12 ? " …" : "");
    for (const a of ficha.querySelectorAll("a[data-sku]")) a.addEventListener("click", (ev) => {
      ev.preventDefault(); $("aloc-sku").value = a.dataset.sku; aoPesquisar();
    });
    return;
  }
  const s = lista[0], info = dados.skus[s] || {};
  const ends = enderecosDoSku(s);
  const nao = (dados.nao_alocados || []).filter((n) => n.sku === s);
  const tot = ends.reduce((a, e) => a + e.n, 0);
  const totT = ends.reduce((a, e) => a + e.t, 0);
  const porPos = {};
  for (const e of ends) (porPos[e.pid] = porPos[e.pid] || { pid: e.pid, endereco: e.endereco, niveis: [] }).niveis.push(e);
  ficha.innerHTML = `<div class="aloc-ficha-cab"><b>${esc(codigo(s))}</b> — ${esc(info.descricao || "")}</div>
    <div class="aloc-ficha-linha">${esc(NOME_ZONA[info.zona] || "")} · ${fmt(info.visitas_dia || 0, 2)} visitas/dia ·
      <b>${tot}</b> unidade(s) em <b>${Object.keys(porPos).length}</b> posição(ões)${totT ? ` · <span class="aloc-tapada">⚠ ${totT} atrás de outro componente</span>` : ""}${nao.length ? ` · <span style="color:#b91c1c">${nao.reduce((a, n) => a + n.n_unidades, 0)} por alocar: ${esc(nao[0].motivo)}</span>` : ""}</div>
    <div class="aloc-ficha-ends">${Object.values(porPos).map((g) =>
      `<a href="#" data-pid="${esc(g.pid)}" data-nivel="${g.niveis[0].nivel}">${esc(g.endereco)}</a> <span class="dica">${g.niveis.map((e) => `N${e.nivel}: ${e.n}${e.t ? ` (${e.t} tapada${e.t > 1 ? "s" : ""})` : ""}`).join(", ")}</span>`).join("<br>")}</div>`;
  for (const a of ficha.querySelectorAll("a[data-pid]")) a.addEventListener("click", (ev) => {
    ev.preventDefault(); selecionar(a.dataset.pid, +a.dataset.nivel);
  });
}

function aoPesquisar() {
  if (!dados) return;
  desenharPlanta();
  desenharFichaSku();
  const lista = skusDaPesquisa($("aloc-sku").value);
  if (lista.length === 1) {           // abre logo a vista de frente na 1.ª posição do componente
    const e = enderecosDoSku(lista[0])[0];
    if (e) selecionar(e.pid, e.nivel);
  } else if (selecionada) {
    desenharFrente();
  }
}
$("aloc-sku").addEventListener("input", () => { aoPesquisar(); marcarNaLista(); });

// ----------------------------------------------------------------- lista de componentes
// para percorrer todos os SKUs depressa e ver onde ficou cada um (2026-11-12)
const COR_ZONA = { 0: "#d9541e", 1: "#2e5c8a", 2: "#5b9a68", 3: "#9e9e9e" };
let listaSkus = [];        // a lista filtrada e ordenada, como está à vista
let skuNaLista = null;     // o SKU selecionado na lista

function linhasSkus() {
  const pos = {}, un = {};
  for (const p of dados.posicoes) for (const n of p.niveis) for (const c of n.caixas) {
    (pos[c.sku] = pos[c.sku] || new Set()).add(p.id);
    un[c.sku] = (un[c.sku] || 0) + c.n_unidades;
  }
  const nao = {};
  for (const n of dados.nao_alocados || []) nao[n.sku] = (nao[n.sku] || 0) + n.n_unidades;
  return Object.entries(dados.skus || {}).map(([s, i]) => ({
    sku: s, cod: codigo(s), desc: i.descricao || "", zona: i.zona, vis: i.visitas_dia || 0,
    un: un[s] || 0, npos: pos[s] ? pos[s].size : 0, nao: nao[s] || 0,
  }));
}

function desenharListaSkus() {
  if (!dados) return;
  $("aloc-sku-nav").style.display = "";
  $("aloc-sku-lista").style.display = "";
  $("aloc-sku-ficha").classList.add("fixa");
  const q = $("aloc-sku-filtro").value.trim().toLowerCase();
  const z = $("aloc-sku-zona").value;
  const ordem = $("aloc-sku-ordem").value;
  let l = linhasSkus().filter((x) => (!q || x.sku.toLowerCase().includes(q) || x.desc.toLowerCase().includes(q))
    && (z === "" || (z === "nao" ? x.nao > 0 : String(x.zona) === z)));
  const porCod = (a, b) => a.cod.localeCompare(b.cod);
  l.sort(ordem === "codigo" ? porCod
    : ordem === "visitas" ? (a, b) => b.vis - a.vis || porCod(a, b)
    : ordem === "posicoes" ? (a, b) => b.npos - a.npos || porCod(a, b)
    : (a, b) => a.zona - b.zona || b.vis - a.vis || porCod(a, b));
  listaSkus = l;
  $("aloc-sku-lista").innerHTML = `<table><thead><tr><th>SKU</th><th>Descrição</th><th>Zona</th>
    <th class="num">Vis./dia</th><th class="num">Unid.</th><th class="num">Posições</th></tr></thead><tbody>
    ${l.map((x, i) => `<tr data-i="${i}" class="${x.sku === skuNaLista ? "sel" : ""} ${x.nao ? "nao" : ""}">
      <td>${esc(x.cod)}</td><td class="desc" title="${esc(x.desc)}">${esc(x.desc)}</td>
      <td><span class="aloc-zona-ponto" style="background:${COR_ZONA[x.zona] || "#ccc"}"></span>${esc((NOME_ZONA[x.zona] || "").split(" (")[0])}</td>
      <td class="num">${fmt(x.vis, 2)}</td><td class="num">${x.un}${x.nao ? ` <b>+${x.nao} por alocar</b>` : ""}</td>
      <td class="num">${x.npos}</td></tr>`).join("")}</tbody></table>`;
  for (const tr of $("aloc-sku-lista").querySelectorAll("tr[data-i]"))
    tr.addEventListener("click", () => { irPara(+tr.dataset.i); $("aloc-sku-lista").focus({ preventScroll: true }); });
  atualizarContador();
}

function atualizarContador() {
  const i = listaSkus.findIndex((x) => x.sku === skuNaLista);
  $("aloc-sku-pos").textContent = listaSkus.length ? `${i >= 0 ? i + 1 : "–"} / ${listaSkus.length}` : "0 / 0";
}

function irPara(i) {
  if (!listaSkus.length) return;
  i = Math.max(0, Math.min(listaSkus.length - 1, i));
  const x = listaSkus[i];
  skuNaLista = x.sku;
  $("aloc-sku").value = x.cod;
  aoPesquisar();
  for (const tr of $("aloc-sku-lista").querySelectorAll("tr[data-i]")) tr.classList.toggle("sel", +tr.dataset.i === i);
  const tr = $("aloc-sku-lista").querySelector(`tr[data-i="${i}"]`);
  const caixa = $("aloc-sku-lista");
  if (tr) {
    const cab = caixa.querySelector("thead") ? caixa.querySelector("thead").offsetHeight : 0;
    const topo = tr.offsetTop - cab, fundo = tr.offsetTop + tr.offsetHeight;
    if (topo < caixa.scrollTop) caixa.scrollTop = topo;
    else if (fundo > caixa.scrollTop + caixa.clientHeight) caixa.scrollTop = fundo - caixa.clientHeight;
  }
  atualizarContador();
}

function passo(d) {
  const i = listaSkus.findIndex((x) => x.sku === skuNaLista);
  irPara(i < 0 ? 0 : i + d);
}

// a pesquisa por texto também marca o componente na lista, se for um só
function marcarNaLista() {
  const l = skusDaPesquisa($("aloc-sku").value);
  skuNaLista = l.length === 1 ? l[0] : null;
  for (const tr of $("aloc-sku-lista").querySelectorAll("tr[data-i]"))
    tr.classList.toggle("sel", listaSkus[+tr.dataset.i] && listaSkus[+tr.dataset.i].sku === skuNaLista);
  atualizarContador();
}

$("aloc-sku-ant").addEventListener("click", () => passo(-1));
$("aloc-sku-seg").addEventListener("click", () => passo(1));
for (const id of ["aloc-sku-filtro", "aloc-sku-zona", "aloc-sku-ordem"])
  $(id).addEventListener(id === "aloc-sku-filtro" ? "input" : "change", desenharListaSkus);
// setas ↑ ↓ (e ← →) com a lista ou os botões ◀ ▶ em foco; nunca dentro do filtro ou das listas de escolha
for (const id of ["aloc-sku-lista", "aloc-sku-nav"]) $(id).addEventListener("keydown", (ev) => {
  if (["INPUT", "SELECT"].includes(ev.target.tagName)) return;
  const d = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[ev.key];
  if (d) { ev.preventDefault(); passo(d); }
});
$("aloc-dia").addEventListener("change", () => escolherDia(parseInt($("aloc-dia").value, 10) || 1));
$("aloc-dia-ant").addEventListener("click", () => escolherDia((diaAtual || 1) - 1));
$("aloc-dia-seg").addEventListener("click", () => escolherDia((diaAtual || 0) + 1));
$("aloc-dia-pior").addEventListener("click", () => {
  const dias = (dados && dados.tempos && dados.tempos.por_dia) || [];
  if (dias.length) escolherDia(dias.reduce((a, b) => (b.sobra_modelo_min < a.sobra_modelo_min ? b : a)).dia);
});
$("aloc-dia-prox").addEventListener("click", proximoVermelho);
