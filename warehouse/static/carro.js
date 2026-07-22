// Modelo procedural do BEN (microcarro CEiiA): corpo prateado boxy, cabine +
// tejadilho pretos (dois tons), rodas grandes com capa aero preta, saias/cavas
// pretas, frente minimalista. Leve (~poucos milhares de triângulos).
// Fonte única partilhada por index.html e pelas páginas de preview.
import * as THREE from "three";

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

// ---- roda grande com capa aero preta (eixo ao longo de Z) ----
export function criarRoda() {
  const w = new THREE.Group();
  w.add(new THREE.Mesh(new THREE.TorusGeometry(0.28, 0.13, 14, 28), M_TIRE));   // pneu
  const capa = new THREE.Mesh(new THREE.CylinderGeometry(0.30, 0.30, 0.05, 30), M_COVER);
  capa.rotation.x = Math.PI / 2; capa.position.z = 0.05; w.add(capa);            // disco liso
  const anel = new THREE.Mesh(new THREE.TorusGeometry(0.205, 0.018, 8, 30), M_TRIM2);
  anel.position.z = 0.085; w.add(anel);                                          // padrão subtil
  const centro = new THREE.Mesh(new THREE.CylinderGeometry(0.055, 0.055, 0.05, 16), M_TRIM2);
  centro.rotation.x = Math.PI / 2; centro.position.z = 0.09; w.add(centro);
  w.traverse(o => { if (o.isMesh) o.castShadow = true; });
  return w;
}

export function criarCarro() {
  const g = new THREE.Group();

  // ---- corpo prateado (boxy, arcos de roda grandes, cintura ~0.88) ----
  const body = new THREE.Shape();
  body.moveTo(-1.14, 0.34);
  body.lineTo(-1.06, 0.30);
  body.absarc(-0.74, 0.30, 0.40, Math.PI, 0, true);        // arco frente
  body.lineTo(0.46, 0.30);
  body.absarc(0.80, 0.30, 0.40, Math.PI, 0, true);         // arco trás
  body.lineTo(1.14, 0.34);
  body.quadraticCurveTo(1.21, 0.48, 1.20, 0.66);           // traseira vertical
  body.lineTo(1.17, 0.87);                                 // cintura trás
  body.quadraticCurveTo(0.6, 0.905, 0.0, 0.905);           // cintura (leve arqueado)
  body.quadraticCurveTo(-0.7, 0.905, -1.15, 0.86);
  body.quadraticCurveTo(-1.22, 0.66, -1.22, 0.50);         // frente
  body.quadraticCurveTo(-1.20, 0.38, -1.14, 0.34);
  g.add(new THREE.Mesh(extr(body, 1.30, 0.09, 0.05), M_BODY));

  // ---- cabine preta (greenhouse alto e direito, tejadilho plano) ----
  const cab = new THREE.Shape();
  cab.moveTo(-1.00, 0.92);                                 // base para-brisas (capot curto)
  cab.lineTo(1.08, 0.90);                                  // base traseira
  cab.quadraticCurveTo(1.15, 1.16, 1.00, 1.42);            // vidro traseiro (vertical)
  cab.quadraticCurveTo(0.55, 1.52, -0.10, 1.52);           // tejadilho
  cab.quadraticCurveTo(-0.72, 1.52, -0.96, 1.38);
  cab.quadraticCurveTo(-1.10, 1.15, -1.00, 0.92);          // para-brisas direito
  g.add(new THREE.Mesh(extr(cab, 1.18, 0.06, 0.05), M_CAB));

  // ---- tejadilho preto mate (por cima da cabine) ----
  const roof = new THREE.Mesh(box(1.86, 0.07, 1.06), M_ROOF);
  roof.position.set(-0.02, 1.52, 0);
  g.add(roof);

  // ---- saias / cavas / fascias pretas ----
  for (const sz of [-1, 1]) {
    const saia = new THREE.Mesh(box(1.5, 0.16, 0.06), M_CLAD);
    saia.position.set(0.03, 0.26, sz * 0.635); g.add(saia);
  }
  const fF = new THREE.Mesh(box(0.10, 0.36, 1.16), M_CLAD); fF.position.set(-1.16, 0.42, 0); g.add(fF);  // frente
  const fT = new THREE.Mesh(box(0.10, 0.34, 1.16), M_CLAD); fT.position.set(1.18, 0.44, 0); g.add(fT);   // trás
  // contorno preto das cavas (lábio do arco)
  for (const sx of [-0.74, 0.80]) for (const sz of [-1, 1]) {
    const lip = new THREE.Mesh(new THREE.TorusGeometry(0.415, 0.03, 8, 20, Math.PI), M_CLAD);
    lip.position.set(sx, 0.30, sz * 0.64); g.add(lip);
  }

  // ---- rodas grandes nos 4 cantos ----
  for (const sx of [-0.74, 0.80]) for (const sz of [-1, 1]) {
    const roda = criarRoda();
    roda.position.set(sx, 0.40, sz * 0.62);   // assenta no chão (raio ~0.41)
    g.add(roda);
  }

  // ---- frente minimalista: painel preto + faróis finos + marcadores laranja ----
  for (const sz of [-1, 1]) {
    const lamp = new THREE.Mesh(box(0.05, 0.05, 0.34), M_LAMP);
    lamp.position.set(-1.205, 0.66, sz * 0.30); g.add(lamp);              // farol fino (em L simplificado)
    const lampV = new THREE.Mesh(box(0.05, 0.16, 0.05), M_LAMP);
    lampV.position.set(-1.205, 0.57, sz * 0.45); g.add(lampV);
    const markF = new THREE.Mesh(box(0.04, 0.05, 0.06), M_MARK);
    markF.position.set(-1.19, 0.50, sz * 0.60); g.add(markF);             // marcador laranja frente
    const markL = new THREE.Mesh(box(0.10, 0.05, 0.04), M_MARK);
    markL.position.set(-0.52, 0.62, sz * 0.665); g.add(markL);            // repetidor lateral
    // espelho
    const esp = new THREE.Mesh(box(0.10, 0.10, 0.05), M_CAB);
    esp.position.set(-0.60, 1.02, sz * 0.70); g.add(esp);
  }
  // farolins traseiros
  for (const sz of [-1, 1]) {
    const tl = new THREE.Mesh(box(0.05, 0.14, 0.10), new THREE.MeshStandardMaterial({ color: 0x3a0a0c, emissive: 0x7a1418, emissiveIntensity: 0.6 }));
    tl.position.set(1.215, 0.70, sz * 0.42); g.add(tl);
  }

  g.traverse(o => { if (o.isMesh) o.castShadow = true; });
  return g;
}
