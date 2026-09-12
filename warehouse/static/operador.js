// Operadores de picking a circular no layout 3D.
//
// Rotas REAIS, derivadas do layout: as filas de racks são duplas
// costas-com-costas, por isso o corredor de serviço fica na faixa imediatamente
// a seguir a cada par de filas. Um operador desloca-se sempre por essas faixas
// (movimento em L: primeiro ao longo do corredor, depois até à posição), pára
// em frente a posições com nível de picking, "apanha" uma caixa, e no fim da
// volta entrega no ponto de destino (uma zona funcional, por nome).
//
// Sem pathfinding e sem colisões: os corredores são retos e conhecidos.
import * as THREE from "three";

// ---------------------------------------------------------------- geometria
const _geo = new Map();
function box(w, h, d) {
  const k = `${w.toFixed(3)}|${h.toFixed(3)}|${d.toFixed(3)}`;
  if (!_geo.has(k)) _geo.set(k, new THREE.BoxGeometry(w, h, d));
  return _geo.get(k);
}

const M_COLETE = new THREE.MeshStandardMaterial({ color: 0xf5c518, roughness: 0.6, emissive: 0x5a4600, emissiveIntensity: 0.25 });
const M_CALCAS = new THREE.MeshStandardMaterial({ color: 0x2c3440, roughness: 0.9 });
const M_PELE   = new THREE.MeshStandardMaterial({ color: 0xc79a72, roughness: 0.8 });
const M_CAPACETE = new THREE.MeshStandardMaterial({ color: 0xe8eef5, roughness: 0.45, metalness: 0.1 });
const M_CAIXA  = new THREE.MeshStandardMaterial({ color: 0xb98a55, roughness: 0.85 });

// Boneco procedural leve (~180 triângulos). Anca em y=0.
function criarBoneco() {
  const g = new THREE.Group();
  const pernaE = new THREE.Mesh(box(0.14, 0.78, 0.16), M_CALCAS);
  const pernaD = new THREE.Mesh(box(0.14, 0.78, 0.16), M_CALCAS);
  pernaE.position.set(-0.11, 0.39, 0); pernaD.position.set(0.11, 0.39, 0);
  g.add(pernaE, pernaD);

  const tronco = new THREE.Mesh(box(0.42, 0.56, 0.24), M_COLETE);
  tronco.position.y = 1.06; g.add(tronco);

  const bracoE = new THREE.Mesh(box(0.11, 0.52, 0.12), M_COLETE);
  const bracoD = new THREE.Mesh(box(0.11, 0.52, 0.12), M_COLETE);
  bracoE.position.set(-0.27, 1.06, 0); bracoD.position.set(0.27, 1.06, 0);
  g.add(bracoE, bracoD);

  const cabeca = new THREE.Mesh(box(0.20, 0.22, 0.20), M_PELE);
  cabeca.position.y = 1.45; g.add(cabeca);
  const capacete = new THREE.Mesh(new THREE.SphereGeometry(0.128, 12, 8, 0, Math.PI * 2, 0, Math.PI / 2), M_CAPACETE);
  capacete.position.y = 1.54; g.add(capacete);

  // caixa transportada (só visível depois de apanhar)
  const carga = new THREE.Mesh(box(0.30, 0.22, 0.24), M_CAIXA);
  carga.position.set(0, 1.02, 0.26); carga.visible = false;
  g.add(carga);

  g.traverse(o => { if (o.isMesh) o.castShadow = true; });
  return { grupo: g, pernaE, pernaD, bracoE, bracoD, carga };
}

// ---------------------------------------------------------------- rotas
// Os operadores andam sobre os CORREDORES REAIS que o backend calcula
// (layout.py -> web.py "corredores"), em vez de os reconstruir por adivinhação.
// Cada corredor é um retângulo {x, y, comp_m (ao longo de x), larg_m (ao longo
// de y)}. O seu eixo de circulação é a dimensão MAIOR -- por isso isto funciona
// com filas horizontais, verticais ou rodadas, sem qualquer suposição de eixo.

/** Descreve um corredor como uma LINHA de circulação: eixo (0=x, 1=y), a
 *  coordenada fixa no outro eixo (o centro da faixa), e o intervalo percorrível. */
function linhaDoCorredor(c) {
  const horizontal = c.comp_m >= c.larg_m;
  if (horizontal) {
    return { eixo: 0, fixo: c.y + c.larg_m / 2, min: c.x, max: c.x + c.comp_m,
             meia: c.larg_m / 2 };
  }
  return { eixo: 1, fixo: c.x + c.comp_m / 2, min: c.y, max: c.y + c.larg_m,
           meia: c.comp_m / 2 };
}

/** Ponto no corredor `L` mais próximo de (px,py), preso ao intervalo do corredor. */
function pontoNoCorredor(L, px, py) {
  if (L.eixo === 0) {   // corredor horizontal: y fixo, x livre
    return new THREE.Vector2(Math.max(L.min, Math.min(L.max, px)), L.fixo);
  }
  return new THREE.Vector2(L.fixo, Math.max(L.min, Math.min(L.max, py)));
}

/** Índice do corredor que SERVE a posição `r` -- o que confina com uma das suas
 *  duas faces de serviço, não o mais próximo em linha reta.
 *
 *  A distinção importa: as filas são duplas costas-com-costas, por isso a face
 *  de trás de uma posição está encostada à fila vizinha e NÃO é servível. Um
 *  "mais próximo" cego escolhe o corredor do outro lado do par -- para as filas
 *  de bordo do bloco isso mandava o operador a pé parar em frente às
 *  porta-paletes do par seguinte, atravessando as prateleiras que ia servir.
 *
 *  Por isso mede-se a distância à FACE (não ao centro) e exige-se que o
 *  corredor esteja do lado de fora dela, com tolerância para erro de
 *  arredondamento do backend. */
const TOL_FACE_M = 0.05;

function corredorQueServe(linhas, r) {
  let best = -1, dBest = Infinity;
  for (let i = 0; i < linhas.length; i++) {
    const L = linhas[i];
    // faces da posição no eixo perpendicular ao corredor
    const f0 = L.eixo === 0 ? r.y : r.x;
    const f1 = L.eixo === 0 ? r.y + r.prof_m : r.x + r.larg_m;
    // a posição tem de estar dentro do alcance longitudinal do corredor
    const c0 = L.eixo === 0 ? r.x : r.y;
    const c1 = L.eixo === 0 ? r.x + r.larg_m : r.y + r.prof_m;
    if (c1 < L.min - TOL_FACE_M || c0 > L.max + TOL_FACE_M) continue;
    // distância do eixo do corredor a cada face, contando só se estiver FORA
    const d = Math.min(
      L.fixo <= f0 + TOL_FACE_M ? f0 - L.fixo : Infinity,   // corredor antes da face da frente
      L.fixo >= f1 - TOL_FACE_M ? L.fixo - f1 : Infinity);  // corredor depois da face de trás
    if (d < dBest) { dBest = d; best = i; }
  }
  return best;
}

function pontosDePicking(layout, tiposPermitidos, linhas) {
  const RENTE = 0.55;   // distância a que o operador para da face da prateleira
  const pts = [];
  for (const r of layout) {
    if (tiposPermitidos && !tiposPermitidos.includes(r.rack_nome)) continue;
    const temPicking = r.blocos?.some(b => b.niveis?.some(n => n.eh_picking && n.caixas.length));
    if (!temPicking) continue;
    const iCor = corredorQueServe(linhas, r);
    if (iCor < 0) continue;        // posição sem corredor que a sirva: ignora-se
    const L = linhas[iCor];
    const cx = r.x + r.larg_m / 2;
    const cy = r.y + r.prof_m / 2;
    // Ponto de PARAGEM: no corredor, à frente da posição e rente à face DELA.
    // Mede-se a partir da FACE da estante, não do eixo do corredor -- medi-lo do
    // eixo deixava o operador a meio da faixa (1.25 m da estante num corredor de
    // 2.5 m) em vez de encostado. O recuo é limitado a `meia` para nunca passar
    // o eixo e invadir a metade que serve a fila oposta (nos corredores de
    // 1.2 m da Prateleira Leve, meia = 0.6 m).
    const recuo = Math.min(RENTE, L.meia);
    const antes = (L.eixo === 0 ? cy : cx) < L.fixo;   // estante antes do eixo?
    // face virada para este corredor, e ponto recuado dela para dentro da faixa
    const face = L.eixo === 0
      ? (antes ? r.y + r.prof_m : r.y)
      : (antes ? r.x + r.larg_m : r.x);
    const parada = antes ? face + recuo : face - recuo;
    let px, py;
    if (L.eixo === 0) {                       // corredor horizontal
      px = Math.max(L.min, Math.min(L.max, cx));
      py = parada;
    } else {                                  // corredor vertical
      py = Math.max(L.min, Math.min(L.max, cy));
      px = parada;
    }
    // direção para onde fica virado ao parar: da parada para a face da estante
    const olhar = L.eixo === 0
      ? { x: 0, y: face - py }
      : { x: face - px, y: 0 };
    pts.push({ x: px, y: py, iCorredor: iCor, rack: r.rack_nome, olhar });
  }
  return pts;
}

// Porta-paletes manual: garfos + timão + a palete transportada. O operador a pé
// vai atrás a empurrar. ~120 triângulos.
const M_METAL = new THREE.MeshStandardMaterial({ color: 0x8d949c, metalness: 0.75, roughness: 0.4 });
const M_PALETE = new THREE.MeshStandardMaterial({ color: 0xa9803f, roughness: 0.9 });

function criarPortaPaletes() {
  const g = new THREE.Group();
  for (const sx of [-0.28, 0.28]) {                      // garfos
    const garfo = new THREE.Mesh(box(0.16, 0.09, 1.15), M_METAL);
    garfo.position.set(sx, 0.06, 0.30); g.add(garfo);
  }
  const corpo = new THREE.Mesh(box(0.74, 0.22, 0.30), M_METAL);
  corpo.position.set(0, 0.14, -0.32); g.add(corpo);
  const timao = new THREE.Mesh(box(0.07, 0.86, 0.07), M_METAL);
  timao.position.set(0, 0.56, -0.44); timao.rotation.x = 0.30; g.add(timao);
  const punho = new THREE.Mesh(box(0.34, 0.07, 0.07), M_METAL);
  punho.position.set(0, 0.95, -0.56); g.add(punho);

  const palete = new THREE.Mesh(box(0.80, 0.14, 1.20), M_PALETE);
  palete.position.set(0, 0.18, 0.30); palete.visible = false; g.add(palete);
  const carga = new THREE.Mesh(box(0.74, 0.55, 1.10), M_CAIXA);
  carga.position.set(0, 0.53, 0.30); carga.visible = false; g.add(carga);

  g.traverse(o => { if (o.isMesh) o.castShadow = true; });
  return { grupo: g, palete, carga };
}

// ---------------------------------------------------------------- operador
const VEL = 1.15;          // m/s a andar
const T_APANHA = 1.1;      // s parado a apanhar
const T_ENTREGA = 1.6;     // s parado a entregar

class Operador {
  // comPorta = true -> arrasta porta-paletes (acede a tipologias pesadas);
  // false -> operador a pé, só prateleiras leves.
  constructor(pontos, destino, rng, comPorta, linhas, transversal) {
    this.linhas = linhas;             // linhas de circulação dos corredores
    this.transversal = transversal;   // linha que liga os corredores nos topos
    const b = criarBoneco();
    this.o = b; this.comPorta = comPorta;
    if (comPorta) {
      // o boneco empurra: porta-paletes à frente, operador atrás
      const pp = criarPortaPaletes();
      this.pp = pp;
      const raiz = new THREE.Group();
      pp.grupo.position.z = 0.62;          // equipamento à frente do operador
      raiz.add(pp.grupo, b.grupo);
      this.grupo = raiz;
    } else {
      this.grupo = b.grupo;
    }
    this.pontos = pontos; this.destino = destino; this.rng = rng;
    this.faseAndar = rng() * 10;
    this.vel = (comPorta ? VEL * 0.78 : VEL) * (0.85 + rng() * 0.3);
    this.estado = "andar";
    this.espera = 0;
    this.recolhas = 0;
    this.alvoRecolhas = 2 + Math.floor(rng() * 2);   // 2-3 recolhas por volta
    const p0 = pontos[Math.floor(rng() * pontos.length)];
    this.grupo.position.set(p0.x, 0, p0.y);
    this.pos = new THREE.Vector2(p0.x, p0.y);
    this.corAtual = p0.iCorredor;     // corredor onde está a circular
    this._novoAlvo();
  }

  // com porta-paletes a carga é a palete; a pé é a caixa na mão
  _mostrarCarga(v) {
    if (this.comPorta) { this.pp.palete.visible = v; this.pp.carga.visible = v; }
    else this.o.carga.visible = v;
  }

  _novoAlvo() {
    let iDest, px, py, entrega;
    if (this.recolhas >= this.alvoRecolhas) {
      // entrega: um ponto fora do bloco, alcançado pela transversal
      entrega = true;
      this.alvo = { x: this.destino.x, y: this.destino.y, entrega: true };
      iDest = this.corAtual; px = this.destino.x; py = this.destino.y;
    } else {
      const p = this.pontos[Math.floor(this.rng() * this.pontos.length)];
      entrega = false;
      // guarda também para onde o operador tem de ficar VIRADO ao parar: a
      // estante que está a servir, não a direção do último passo.
      this.alvo = { x: p.x, y: p.y, entrega: false, olhar: p.olhar };
      iDest = p.iCorredor; px = p.x; py = p.y;
    }

    // Percurso: (1) recua para a linha do corredor atual, (2) se muda de
    // corredor, vai por uma TRANSVERSAL fora do bloco ate à linha do destino,
    // (3) avança nessa linha ate à frente do alvo, (4) passo final ate ao alvo
    // (rente à estante, ou ao ponto de entrega). Andar SEMPRE sobre linhas de
    // corredor garante que nunca se atravessa racks, em qualquer orientacao.
    const Lc = this.linhas[this.corAtual];
    const Ld = this.linhas[iDest];
    const T = this.transversal;
    this.pernas = [];
    if (iDest !== this.corAtual) {
      // (1) só ao MUDAR de corredor é preciso voltar ao eixo (para sair pela
      // transversal). Dentro do mesmo corredor, voltar ao eixo era o que punha o
      // operador a andar pelo meio da faixa em vez de rente às estantes.
      this.pernas.push(pontoNoCorredor(Lc, this.pos.x, this.pos.y));
      // (2) muda de corredor pela transversal (fora do bloco)
      const saidaAtual = new THREE.Vector2(
        Lc.eixo === 0 ? T.x : Lc.fixo,
        Lc.eixo === 0 ? Lc.fixo : T.y);
      const entradaDest = new THREE.Vector2(
        Ld.eixo === 0 ? T.x : Ld.fixo,
        Ld.eixo === 0 ? Ld.fixo : T.y);
      this.pernas.push(saidaAtual);    // vai ate ao topo do corredor atual
      this.pernas.push(entradaDest);   // desliza pela transversal ate ao destino
    }
    // (3) avança até à frente do alvo, JÁ na metade do corredor onde o alvo
    // está. Usar aqui o eixo do corredor (pontoNoCorredor) fazia o operador
    // circular sempre pelo meio da faixa e, no corredor partilhado entre
    // prateleira leve e porta-paletes, deixava-o do lado errado -- via-se a fila
    // de operadores ao centro em vez de encostada às caixas que serve.
    this.pernas.push(Ld.eixo === 0
      ? new THREE.Vector2(Math.max(Ld.min, Math.min(Ld.max, px)), py)
      : new THREE.Vector2(px, Math.max(Ld.min, Math.min(Ld.max, py))));
    // (4) passo final ate ao alvo
    this.pernas.push(new THREE.Vector2(px, py));
    this.corAtual = iDest;
    this.perna = 0;
  }

  update(dt) {
    const b = this.o;
    if (this.estado === "parado") {
      this.espera -= dt;
      // braços a subir/descer a apanhar
      const k = Math.sin(Math.max(this.espera, 0) * 4) * 0.5;
      b.bracoE.rotation.x = b.bracoD.rotation.x = -0.9 - k * 0.4;
      if (this.espera <= 0) { this.estado = "andar"; this._novoAlvo(); }
      return;
    }

    const alvo = this.pernas[this.perna];
    const dx = alvo.x - this.pos.x, dy = alvo.y - this.pos.y;
    const dist = Math.hypot(dx, dy);
    const passo = this.vel * dt;

    if (dist <= passo) {
      this.pos.set(alvo.x, alvo.y);
      this.perna++;
      if (this.perna >= this.pernas.length) {
        if (this.alvo.entrega) {
          this.estado = "parado"; this.espera = T_ENTREGA;
          this._mostrarCarga(false); this.recolhas = 0;
          this.alvoRecolhas = 2 + Math.floor(this.rng() * 2);
        } else {
          this.estado = "parado"; this.espera = T_APANHA;
          this._mostrarCarga(true); this.recolhas++;
          // vira-se para a estante que está a servir: a rotação só é atualizada
          // enquanto anda, por isso sem isto ficava com a direção do último passo
          const ol = this.alvo.olhar;
          if (ol) this.grupo.rotation.y = Math.atan2(ol.x, ol.y);
        }
      }
    } else {
      this.pos.x += (dx / dist) * passo;
      this.pos.y += (dy / dist) * passo;
      this.grupo.rotation.y = Math.atan2(dx, dy);
    }
    this.grupo.position.set(this.pos.x, 0, this.pos.y);

    // marcha: pernas e braços em oposição
    this.faseAndar += dt * this.vel * 5.2;
    const s = Math.sin(this.faseAndar) * 0.45;
    b.pernaE.rotation.x = s; b.pernaD.rotation.x = -s;
    b.bracoE.rotation.x = -s * 0.7; b.bracoD.rotation.x = s * 0.7;
  }
}

// ---------------------------------------------------------------- API
// Regra operacional: quem anda a pé só faz picking manual em estantes leves;
// paletes/material longo exigem porta-paletes. Por isso cada perfil tem o seu
// conjunto de tipologias acessíveis.
const RACKS_A_PE = ["Prateleira Leve"];

/** O backend só emite os corredores INTERIORES (entre pares de filas) -- a faixa
 *  que corre ao longo da parede é área envolvente, não um corredor listado. Mas
 *  as filas de BORDO do bloco só têm essa faixa para as servir: sem ela ficam
 *  sem corredor e o operador é atirado para o corredor do par seguinte,
 *  atravessando os racks que devia servir (era o que punha os operadores a pé em
 *  frente às porta-paletes -- as prateleiras leves são a fila de bordo).
 *
 *  Sintetiza-se então uma faixa de cada lado do bloco, no mesmo eixo dos
 *  corredores existentes e recuada meia largura de corredor para fora. */
function _faixasPerimetrais(layout, linhas) {
  if (!linhas.length) return [];
  const eixo = linhas[0].eixo;                  // segue a orientação das filas
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const r of layout) {
    x0 = Math.min(x0, r.x); y0 = Math.min(y0, r.y);
    x1 = Math.max(x1, r.x + r.larg_m); y1 = Math.max(y1, r.y + r.prof_m);
  }
  const meia = 1.25;                            // metade de um corredor de 2.5 m
  if (eixo === 0) {   // corredores horizontais (y fixo) -> faixas em cima e em baixo
    return [
      { eixo: 0, fixo: y0 - meia, min: x0, max: x1, meia },
      { eixo: 0, fixo: y1 + meia, min: x0, max: x1, meia },
    ];
  }
  return [
    { eixo: 1, fixo: x0 - meia, min: y0, max: y1, meia },
    { eixo: 1, fixo: x1 + meia, min: y0, max: y1, meia },
  ];
}

/** Faixa transversal (fora do bloco) por onde se muda de corredor. Fica logo a
 *  seguir ao extremo do bloco, ao longo do eixo PERPENDICULAR ao dos corredores
 *  -- assim liga todos os corredores sem cruzar racks, seja qual for a
 *  orientacao das filas. */
function _transversal(layout, linhas) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const r of layout) {
    x0 = Math.min(x0, r.x); y0 = Math.min(y0, r.y);
    x1 = Math.max(x1, r.x + r.larg_m); y1 = Math.max(y1, r.y + r.prof_m);
  }
  // corredores horizontais (eixo 0) -> mudam-se por uma faixa VERTICAL na
  // lateral (x fixo); verticais -> por uma faixa HORIZONTAL no topo (y fixo).
  const horizontais = linhas.filter(L => L.eixo === 0).length >= linhas.length / 2;
  return horizontais ? { x: x1 + 1.5, y: (y0 + y1) / 2 }   // lateral direita
                     : { x: (x0 + x1) / 2, y: y1 + 1.5 };  // topo
}

export function criarOperadores(scene, layout, zonasLayout, opcoes = {}) {
  const n = opcoes.n ?? 4;
  const nPorta = opcoes.nPorta ?? 2;
  const nomeDestino = opcoes.destino ?? "7 - Zona de Espera de Transporte para BDL";
  const corredores = opcoes.corredores ?? [];

  if (!corredores.length) {
    console.warn("[operador] backend nao enviou corredores -- nenhum operador criado");
    return null;
  }

  // tipologias existentes no layout, separadas por perfil
  const todas = [...new Set(layout.map(r => r.rack_nome))];
  const tiposPe = todas.filter(t => RACKS_A_PE.includes(t));
  const tiposPorta = todas.filter(t => !RACKS_A_PE.includes(t));

  // linhas de circulação: um corredor real por cada + as faixas perimetrais
  // (que o backend não lista, mas são as únicas que servem as filas de bordo)
  const linhas = corredores.map(linhaDoCorredor);
  linhas.push(..._faixasPerimetrais(layout, linhas));
  const transversal = _transversal(layout, linhas);

  const pontosPe = pontosDePicking(layout, tiposPe, linhas);
  const pontosPorta = pontosDePicking(layout, tiposPorta, linhas);
  if (!pontosPe.length && !pontosPorta.length) {
    console.warn("[operador] sem posicoes de picking no layout -- nenhum operador criado");
    return null;
  }

  // destino: por nome de zona (robusto a mudanças de layout); senão, fallback
  let z = zonasLayout?.find(zz => zz.nome === nomeDestino);
  if (!z) z = zonasLayout?.find(zz => (zz.nome || "").includes("Espera de Transporte"));
  const p0 = pontosPe[0] || pontosPorta[0];
  const destino = z
    ? { x: z.x + z.larg_m / 2, y: z.y + z.prof_m / 2 }
    : { x: p0.x, y: p0.y };

  let seed = 20260723;
  const rng = () => (seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296;

  const grupo = new THREE.Group();
  grupo.userData.isOperadores = true;
  const ops = [];
  // a pé -> só prateleiras leves
  if (pontosPe.length) {
    for (let i = 0; i < n; i++) {
      const op = new Operador(pontosPe, destino, rng, false, linhas, transversal);
      ops.push(op); grupo.add(op.grupo);
    }
  }
  // com porta-paletes -> tipologias pesadas (tudo o resto)
  if (pontosPorta.length) {
    for (let i = 0; i < nPorta; i++) {
      const op = new Operador(pontosPorta, destino, rng, true, linhas, transversal);
      ops.push(op); grupo.add(op.grupo);
    }
  }

  // marcador do ponto de entrega
  const marca = new THREE.Mesh(
    new THREE.RingGeometry(0.45, 0.62, 28),
    new THREE.MeshBasicMaterial({ color: 0xf5c518, transparent: true, opacity: 0.75, side: THREE.DoubleSide }));
  marca.rotation.x = -Math.PI / 2; marca.position.set(destino.x, 0.02, destino.y);
  grupo.add(marca);

  scene.add(grupo);

  let escala = opcoes.velocidade ?? 1;   // multiplicador de velocidade (0 = pausa)
  return {
    grupo, destino,
    nPontosPe: pontosPe.length, nPontosPorta: pontosPorta.length,
    tiposPe, tiposPorta,
    // dt limitado a 0.1 s: evita saltos enormes quando o separador volta a ficar
    // ativo (requestAnimationFrame pausa em background e acumula tempo).
    update: (dt) => {
      if (escala <= 0) return;
      const d = Math.min(dt, 0.1) * escala;
      ops.forEach(o => o.update(d));
    },
    setVelocidade: (v) => { escala = Math.max(0, v); },
    setVisible: (v) => { grupo.visible = v; },
    dispose: () => scene.remove(grupo),
  };
}
