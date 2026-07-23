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
// Deriva os pontos de paragem de picking + a faixa de corredor de cada fila.
//
// IMPORTANTE (layout.py): as filas são DUPLAS costas-com-costas. A fiada
// "frente" fica em y e a "tras" em y+prof, encostadas pelas costas; o corredor
// de serviço é a faixa ANTES da "frente" e DEPOIS da "tras":
//
//     [corredor] [frente y] [tras y+prof] [corredor] [frente ...]
//
// Logo a face de serviço da "frente" abre para -y (corredor anterior) e a da
// "tras" para +y (corredor seguinte). Inverter isto põe o operador dentro do
// bloco de racks (atravessa-os).
function pontosDePicking(layout, corredorPorRack, tiposPermitidos) {
  const pts = [];
  for (const r of layout) {
    if (tiposPermitidos && !tiposPermitidos.includes(r.rack_nome)) continue;
    const temPicking = r.blocos?.some(b => b.niveis?.some(n => n.eh_picking && n.caixas.length));
    if (!temPicking) continue;
    const corredor = corredorPorRack[r.rack_nome] ?? 2.5;
    const recuo = Math.min(corredor * 0.45, corredor - 0.35);   // dentro do corredor
    const yFace = (r.lado === "tras") ? r.y + r.prof_m + recuo
                                      : r.y - recuo;
    pts.push({
      x: r.x + r.larg_m / 2,      // em frente ao centro da posição
      y: yFace,                    // dentro do corredor
      yCorredor: yFace,
      rack: r.rack_nome,
    });
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
  // comPorta = true -> arrasta porta-paletes (acede a porta-paletes/cantilever);
  // false -> operador a pé, só prateleiras leves.
  constructor(pontos, destino, rng, comPorta = false) {
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
    this._novoAlvo();
  }

  // com porta-paletes a carga é a palete; a pé é a caixa na mão
  _mostrarCarga(v) {
    if (this.comPorta) { this.pp.palete.visible = v; this.pp.carga.visible = v; }
    else this.o.carga.visible = v;
  }

  _novoAlvo() {
    if (this.recolhas >= this.alvoRecolhas) {
      this.alvo = { x: this.destino.x, y: this.destino.y, entrega: true };
    } else {
      const p = this.pontos[Math.floor(this.rng() * this.pontos.length)];
      this.alvo = { x: p.x, y: p.y, yCorredor: p.yCorredor, entrega: false };
    }
    // trajeto em L pelo corredor: primeiro alinha em y (corredor), depois x
    this.pernas = [
      new THREE.Vector2(this.pos.x, this.alvo.yCorredor ?? this.alvo.y),
      new THREE.Vector2(this.alvo.x, this.alvo.yCorredor ?? this.alvo.y),
    ];
    if (this.alvo.entrega) this.pernas.push(new THREE.Vector2(this.alvo.x, this.alvo.y));
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

export function criarOperadores(scene, layout, zonasLayout, opcoes = {}) {
  const n = opcoes.n ?? 4;
  const nPorta = opcoes.nPorta ?? 2;
  const nomeDestino = opcoes.destino ?? "7 - Zona de Espera de Transporte para BDL";
  const corredorPorRack = opcoes.corredorPorRack ?? {};

  // tipologias existentes no layout, separadas por perfil
  const todas = [...new Set(layout.map(r => r.rack_nome))];
  const tiposPe = todas.filter(t => RACKS_A_PE.includes(t));
  const tiposPorta = todas.filter(t => !RACKS_A_PE.includes(t));

  const pontosPe = pontosDePicking(layout, corredorPorRack, tiposPe);
  const pontosPorta = pontosDePicking(layout, corredorPorRack, tiposPorta);
  if (!pontosPe.length && !pontosPorta.length) return null;

  // destino: por nome de zona (robusto a mudanças de layout); senão, fallback
  let z = zonasLayout?.find(zz => zz.nome === nomeDestino);
  if (!z) z = zonasLayout?.find(zz => (zz.nome || "").includes("Espera de Transporte"));
  const destino = z
    ? { x: z.x + z.larg_m / 2, y: z.y + z.prof_m / 2 }
    : { x: pontos[0].x, y: pontos[0].y };

  let seed = 20260723;
  const rng = () => (seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296;

  const grupo = new THREE.Group();
  grupo.userData.isOperadores = true;
  const ops = [];
  // a pé -> só prateleiras leves
  if (pontosPe.length) {
    for (let i = 0; i < n; i++) {
      const op = new Operador(pontosPe, destino, rng, false);
      ops.push(op); grupo.add(op.grupo);
    }
  }
  // com porta-paletes -> porta-paletes/cantilever (tudo o resto)
  if (pontosPorta.length) {
    for (let i = 0; i < nPorta; i++) {
      const op = new Operador(pontosPorta, destino, rng, true);
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
