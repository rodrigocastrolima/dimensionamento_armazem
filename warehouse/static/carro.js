// Modelo procedural do BEN (microcarro CEiiA): corpo prateado boxy, cabine +
// tejadilho pretos (dois tons), rodas grandes com capa aero preta, saias/cavas
// pretas, frente minimalista. Leve (~poucos milhares de triângulos).
// Fonte única partilhada por index.html e pelas páginas de preview.
import * as THREE from "three";

// -------------------------------------------------- modelo CAD real: ABANDONADO
// Existiu aqui o carregamento de `models/ben.glb` -- o CAD real do BEN convertido
// de FBX pelo tools/fbx_para_glb.py. Foi REMOVIDO: o GLB sai em farrapos e não é
// recuperável por afinação do conversor.
//
// Medido no bake de 2026-07-28 (300 k triângulos): 90 752 componentes
// desconexos, 39 992 deles um único triângulo isolado, 62% das arestas de bordo.
// O FBX de origem está SÃO (arestas de bordo a 0.12-0.19) -- o estrago é da
// decimação: 308 peças com ~103 M triângulos para um orçamento de 300 k dá um
// ratio de 0.3%, e peças individuais de 3.4 M polígonos ficam com ~10 k.
// Colapsar uma malha CAD a 0.3% não a simplifica, desintegra-a. Subir o
// orçamento não resolve em escala útil para um viewer web (faltavam ordens de
// grandeza, não 2x) e o `MIN_TRIS` por peça não protege contra isto.
//
// A cena também aparecia quase preta -- não era o material (baseColor estava em
// 0.72/0.75/0.79): triângulos soltos têm normais aleatórias, metade virada para
// dentro, e não há superfície para captar luz.
//
// Alternativa se algum dia o CAD real for indispensável: retopologia manual em
// Blender, ou pedir ao fornecedor uma versão low-poly de visualização. Não é
// trabalho de script. As dimensões medidas do CAD real ficam registadas abaixo,
// que era a parte difícil de acertar sem referência.

/** Mantida por compatibilidade: o index.html faz `await carregarBen()` antes de
 *  desenhar. Já não há nada a carregar -- o modelo é procedural e imediato. */
export function carregarBen() {
  return Promise.resolve(null);
}

const _geo = new Map();
function box(w, h, d) {
  const k = `${w.toFixed(3)}|${h.toFixed(3)}|${d.toFixed(3)}`;
  if (!_geo.has(k)) _geo.set(k, new THREE.BoxGeometry(w, h, d));
  return _geo.get(k);
}
function extr(shape, depth, bt, bs) {
  const g = new THREE.ExtrudeGeometry(shape, {
    depth, bevelEnabled: true, bevelThickness: bt, bevelSize: bs,
    bevelSegments: 4, curveSegments: 24, steps: 1 });
  g.computeBoundingBox();
  g.translate(0, 0, -(g.boundingBox.min.z + g.boundingBox.max.z) / 2);
  return g;
}

// ---- materiais (partilhados) ----
const M_BODY  = new THREE.MeshPhysicalMaterial({ color: 0xc2c7cc, metalness: 0.60, roughness: 0.34, clearcoat: 1.0, clearcoatRoughness: 0.22 });
const M_CAB   = new THREE.MeshPhysicalMaterial({ color: 0x121417, metalness: 0.30, roughness: 0.12, clearcoat: 1.0, clearcoatRoughness: 0.08 });
const M_ROOF  = new THREE.MeshStandardMaterial({ color: 0x161719, roughness: 0.55, metalness: 0.2 });
const M_CLAD  = new THREE.MeshStandardMaterial({ color: 0x131416, roughness: 0.9,  metalness: 0.1 });
const M_TIRE  = new THREE.MeshStandardMaterial({ color: 0x0e0f11, roughness: 0.95 });
const M_COVER = new THREE.MeshStandardMaterial({ color: 0x1b1d20, roughness: 0.5,  metalness: 0.35 });
const M_TRIM2 = new THREE.MeshStandardMaterial({ color: 0x3a3d42, roughness: 0.6,  metalness: 0.4 });
const M_MARK  = new THREE.MeshStandardMaterial({ color: 0xff8a1e, emissive: 0xff7a10, emissiveIntensity: 0.7, roughness: 0.5 });
const M_LAMP  = new THREE.MeshStandardMaterial({ color: 0xdfe6ee, emissive: 0xcfe0ff, emissiveIntensity: 0.5, roughness: 0.4 });

// ---- roda (eixo ao longo de Z). Raio exterior 0.33 = R_RODA do corpo.
// Nas fotos a jante é CLARA de raios largos com capa escura ao centro, e ocupa
// quase todo o diâmetro -- pneu de flanco fino, típico de microcarro.
export function criarRoda() {
  const w = new THREE.Group();
  w.add(new THREE.Mesh(new THREE.TorusGeometry(0.255, 0.075, 12, 26), M_TIRE));  // pneu (flanco fino)
  const jante = new THREE.Mesh(new THREE.CylinderGeometry(0.245, 0.245, 0.05, 26), M_TRIM2);
  jante.rotation.x = Math.PI / 2; jante.position.z = 0.045; w.add(jante);         // jante clara
  const capa = new THREE.Mesh(new THREE.CylinderGeometry(0.135, 0.135, 0.055, 20), M_COVER);
  capa.rotation.x = Math.PI / 2; capa.position.z = 0.07; w.add(capa);             // capa escura central
  w.traverse(o => { if (o.isMesh) o.castShadow = true; });
  return w;
}

// Dimensões medidas no CAD real (FBX do fornecedor), para o procedural ter as
// proporções certas: microcarro pequeno e alto, não um automóvel comum.
export const BEN_DIM = { comp_m: 2.51, larg_m: 1.72, alt_m: 1.56 };

export function criarCarro() {
  return criarCarroProcedural();
}

/** BEN procedural, às cotas do CAD real (ver BEN_DIM): 2.51 x 1.72 x 1.56 m.
 *
 *  Construído a partir das fotos de referência do veículo. O que define o
 *  carácter, por ordem de importância visual:
 *   1. o "greenhouse" escuro (cabine) desce em CUNHA sobre o capô e envolve o
 *      para-brisas -- é a assinatura do carro, não um vidro vertical;
 *   2. corpo prateado boxy de ombro alto e contínuo, cintura a ~2/3 da altura;
 *   3. cavas quase circulares, muito destacadas, em plástico escuro mais largo
 *      que o corpo; rodas grandes para o tamanho do carro;
 *   4. barra de luz LED fina e horizontal na transição capô/greenhouse.
 *
 *  Eixo X = comprimento (frente em -X), Y = altura (0 = chão), Z = largura.
 *  Perfil desenhado no plano XY e extrudido em Z, que é o que mantém a contagem
 *  de triângulos baixa (poucos milhares) para um elemento decorativo clonado
 *  por zona.
 */
export function criarCarroProcedural() {
  const g = new THREE.Group();

  const L = BEN_DIM.comp_m / 2;      // 1.255 -- meio comprimento
  const W = BEN_DIM.larg_m / 2;      // 0.86  -- meia largura
  const H = BEN_DIM.alt_m;           // 1.56  -- altura total

  // eixos e rodas: roda grande, cavas quase a tocar a cintura
  const EIXO_F = -0.78, EIXO_T = 0.80;
  const R_RODA = 0.33;               // raio (pneu+jante)
  const Y_EIXO = R_RODA;             // centro do eixo = raio (assenta no chão)
  const CINTURA = 0.92;              // ombro do corpo prateado
  // Corpo mais estreito que a largura total: o bevel da extrusão acrescenta
  // 2x bevelSize de cada lado, e são as cavas salientes que definem a largura
  // máxima -- descontar aqui mantém o conjunto dentro dos 1.72 m do CAD.
  const LARG_CORPO = W * 2 - 0.22;

  // ---- corpo prateado: boxy, flancos retos, cavas circulares recortadas ----
  const body = new THREE.Shape();
  body.moveTo(-L + 0.10, 0.30);
  body.absarc(EIXO_F, 0.30, 0.40, Math.PI, 0, true);       // cava frente
  body.lineTo(EIXO_T - 0.40, 0.30);
  body.absarc(EIXO_T, 0.30, 0.40, Math.PI, 0, true);       // cava trás
  body.lineTo(L - 0.06, 0.32);
  body.quadraticCurveTo(L, 0.46, L, 0.64);                 // traseira quase vertical
  body.lineTo(L - 0.02, CINTURA - 0.02);
  body.quadraticCurveTo(L * 0.5, CINTURA, 0.0, CINTURA);   // ombro (leve arqueado)
  body.quadraticCurveTo(-L * 0.6, CINTURA, -L + 0.06, CINTURA - 0.08);
  body.quadraticCurveTo(-L, 0.62, -L, 0.46);               // frente arredondada
  body.quadraticCurveTo(-L, 0.34, -L + 0.10, 0.30);
  g.add(new THREE.Mesh(extr(body, LARG_CORPO, 0.10, 0.06), M_BODY));

  // ---- greenhouse escuro: CUNHA sobre o capô + tejadilho quase plano ----
  // O bico avança para -X bem além da base do para-brisas: é o que dá a leitura
  // de "capacete" envolvente das fotos.
  const cab = new THREE.Shape();
  cab.moveTo(-L + 0.02, 0.86);                             // bico da cunha, sobre o capô
  cab.quadraticCurveTo(-L + 0.30, 0.84, -0.62, 0.90);      // aba baixa sobre o capô
  cab.lineTo(L - 0.12, 0.90);                              // base traseira do vidro
  cab.quadraticCurveTo(L - 0.02, 1.14, L - 0.14, H - 0.06); // vidro traseiro
  cab.quadraticCurveTo(0.35, H, -0.30, H);                 // tejadilho (arco leve)
  cab.quadraticCurveTo(-0.90, H - 0.01, -1.05, H - 0.16);  // canto dianteiro do teto
  cab.quadraticCurveTo(-L + 0.04, 1.08, -L + 0.02, 0.86);  // para-brisas muito inclinado
  g.add(new THREE.Mesh(extr(cab, LARG_CORPO - 0.06, 0.05, 0.04), M_CAB));

  // ---- tejadilho mate por cima do greenhouse (dois tons das fotos) ----
  const roof = new THREE.Mesh(box(1.42, 0.06, LARG_CORPO - 0.10), M_ROOF);
  roof.position.set(-0.10, H - 0.02, 0);
  g.add(roof);

  // ---- barra de luz LED: fina, horizontal, na transição capô/greenhouse ----
  const bar = new THREE.Mesh(box(0.10, 0.035, LARG_CORPO - 0.30), M_LAMP);
  bar.position.set(-L + 0.10, 0.82, 0);
  g.add(bar);

  // ---- saia inferior escura a toda a volta ----
  for (const sz of [-1, 1]) {
    const saia = new THREE.Mesh(box(1.40, 0.15, 0.07), M_CLAD);
    saia.position.set(0.02, 0.24, sz * (W - 0.10)); g.add(saia);
  }
  const paraF = new THREE.Mesh(box(0.12, 0.34, LARG_CORPO - 0.12), M_CLAD);
  paraF.position.set(-L + 0.02, 0.40, 0); g.add(paraF);          // pala escura da frente
  const paraT = new THREE.Mesh(box(0.10, 0.30, LARG_CORPO - 0.12), M_CLAD);
  paraT.position.set(L - 0.01, 0.42, 0); g.add(paraT);

  // ---- cavas escuras salientes + rodas ----
  // As rodas são o ponto mais largo do veículo: a face exterior do pneu fica
  // exatamente em W (0.86), para o conjunto não passar os 1.72 m do CAD.
  const Z_RODA = W - 0.10;           // + meia-espessura do pneu (0.075) + jante
  for (const sx of [EIXO_F, EIXO_T]) for (const sz of [-1, 1]) {
    const lip = new THREE.Mesh(new THREE.TorusGeometry(0.425, 0.04, 8, 22, Math.PI), M_CLAD);
    lip.position.set(sx, 0.30, sz * (W - 0.05)); g.add(lip);
    const roda = criarRoda();
    roda.position.set(sx, Y_EIXO, sz * Z_RODA);
    g.add(roda);
  }

  // ---- faróis escuros retangulares baixos + marcadores ----
  for (const sz of [-1, 1]) {
    const farol = new THREE.Mesh(box(0.06, 0.13, 0.17), M_CAB);
    farol.position.set(-L + 0.01, 0.60, sz * 0.42); g.add(farol);
    const lente = new THREE.Mesh(box(0.03, 0.07, 0.11), M_LAMP);
    lente.position.set(-L - 0.01, 0.60, sz * 0.42); g.add(lente);
    const markL = new THREE.Mesh(box(0.09, 0.045, 0.035), M_MARK);
    markL.position.set(-0.44, 0.72, sz * (W - 0.12)); g.add(markL);   // repetidor lateral
    // espelho, no ombro junto ao para-brisas
    const esp = new THREE.Mesh(box(0.09, 0.09, 0.05), M_CAB);
    esp.position.set(-0.54, 1.00, sz * (W - 0.06)); g.add(esp);
  }
  // farolins traseiros
  const M_TAIL = new THREE.MeshStandardMaterial({ color: 0x3a0a0c, emissive: 0x7a1418, emissiveIntensity: 0.6, roughness: 0.5 });
  for (const sz of [-1, 1]) {
    const tl = new THREE.Mesh(box(0.05, 0.12, 0.14), M_TAIL);
    tl.position.set(L + 0.01, 0.70, sz * 0.44); g.add(tl);
  }

  g.traverse(o => { if (o.isMesh) o.castShadow = true; });
  return g;
}
