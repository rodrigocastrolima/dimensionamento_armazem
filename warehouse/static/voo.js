// Voo livre (WASD + rato) com gravação de vídeo.
//
// Substitui o percurso automático do tour.js: nenhuma heurística acerta no
// enquadramento que um humano quer para uma apresentação. Aqui é o utilizador
// que pilota e decide o que mostrar.
//
// Controlos (estilo FPS/Blender fly):
//   W/S  frente/trás      A/D  esquerda/direita
//   Q/E  descer/subir     Shift  turbo    Ctrl  lento
//   Rato  olhar em volta (pointer lock: clica no viewer para capturar)
//   Esc  larga o rato
//
// Gravação: canvas.captureStream() + MediaRecorder. Exige
// preserveDrawingBuffer: true no WebGLRenderer, senão o vídeo sai preto.
import * as THREE from "three";

const VEL_BASE = 6.0;      // m/s
const VEL_TURBO = 22.0;    // com Shift
const VEL_LENTA = 1.8;     // com Ctrl -- para aproximações finas
const SENSIB = 0.0022;     // rad por pixel de rato
const ALTURA_MIN = 0.4;    // não atravessa o chão

export function criarVooLivre(camera, controls, dom) {
  const teclas = new Set();
  let ativo = false;
  let yaw = 0, pitch = 0;

  // ---- estado da câmara ao entrar/sair, para não estragar a vista do utilizador
  let posGuardada = null, alvoGuardado = null;

  function _sincronizarAngulos() {
    // deriva yaw/pitch da direção atual da câmara, para o voo começar exatamente
    // de onde a vista está (senão dá um salto ao entrar no modo)
    const dir = new THREE.Vector3();
    camera.getWorldDirection(dir);
    yaw = Math.atan2(-dir.x, -dir.z);
    pitch = Math.asin(THREE.MathUtils.clamp(dir.y, -1, 1));
  }

  function _aplicarOrientacao() {
    // ordem YXZ: yaw em torno de Y (global) e pitch em torno de X (local).
    // Sem esta ordem a câmara "rola" (roll) e o horizonte inclina.
    camera.rotation.order = "YXZ";
    camera.rotation.y = yaw;
    camera.rotation.x = pitch;
    camera.rotation.z = 0;
  }

  const onKeyDown = (e) => {
    if (!ativo) return;
    const k = e.key.toLowerCase();
    if ("wasdqe".includes(k) || k === "shift" || k === "control") {
      teclas.add(k);
      e.preventDefault();       // W/S/A/D não devem fazer scroll da página
    }
  };
  const onKeyUp = (e) => {
    const k = e.key.toLowerCase();
    teclas.delete(k);
    if (k === "shift") teclas.delete("shift");
    if (k === "control") teclas.delete("control");
  };

  const onMouseMove = (e) => {
    if (!ativo || document.pointerLockElement !== dom) return;
    yaw -= e.movementX * SENSIB;
    pitch -= e.movementY * SENSIB;
    // trava o pitch antes do zénite/nadir: passar de 90 graus vira a imagem
    pitch = THREE.MathUtils.clamp(pitch, -Math.PI / 2 + 0.05, Math.PI / 2 - 0.05);
    _aplicarOrientacao();
  };

  const onClickDom = () => { if (ativo) dom.requestPointerLock(); };
  const onLockChange = () => {
    // Esc larga o rato mas o modo continua ativo (WASD ainda funciona);
    // clicar no viewer volta a capturar.
  };

  function entrar() {
    if (ativo) return;
    ativo = true;
    posGuardada = camera.position.clone();
    alvoGuardado = controls.target.clone();
    controls.enabled = false;      // o OrbitControls luta com o voo
    _sincronizarAngulos();
    _aplicarOrientacao();
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    document.addEventListener("mousemove", onMouseMove);
    dom.addEventListener("click", onClickDom);
    document.addEventListener("pointerlockchange", onLockChange);
    dom.requestPointerLock();
  }

  function sair(reporVista = true) {
    if (!ativo) return;
    ativo = false;
    teclas.clear();
    window.removeEventListener("keydown", onKeyDown);
    window.removeEventListener("keyup", onKeyUp);
    document.removeEventListener("mousemove", onMouseMove);
    dom.removeEventListener("click", onClickDom);
    document.removeEventListener("pointerlockchange", onLockChange);
    if (document.pointerLockElement === dom) document.exitPointerLock();
    camera.rotation.order = "XYZ";
    if (reporVista && posGuardada) {
      camera.position.copy(posGuardada);
      controls.target.copy(alvoGuardado);
    } else {
      // fica onde está: o alvo do OrbitControls passa a ser um ponto à frente,
      // senão ele puxa a câmara de volta ao alvo antigo no primeiro update()
      const dir = new THREE.Vector3();
      camera.getWorldDirection(dir);
      controls.target.copy(camera.position).addScaledVector(dir, 10);
    }
    controls.enabled = true;
  }

  /** Chamar a cada frame com o dt em segundos. */
  function update(dt) {
    if (!ativo || !teclas.size) return;
    const v = teclas.has("shift") ? VEL_TURBO
            : teclas.has("control") ? VEL_LENTA : VEL_BASE;
    const passo = v * Math.min(dt, 0.1);   // dt limitado: separador inativo

    const frente = new THREE.Vector3();
    camera.getWorldDirection(frente);
    const direita = new THREE.Vector3().crossVectors(frente, camera.up).normalize();

    if (teclas.has("w")) camera.position.addScaledVector(frente, passo);
    if (teclas.has("s")) camera.position.addScaledVector(frente, -passo);
    if (teclas.has("d")) camera.position.addScaledVector(direita, passo);
    if (teclas.has("a")) camera.position.addScaledVector(direita, -passo);
    if (teclas.has("e")) camera.position.y += passo;     // sobe (mundo, não local)
    if (teclas.has("q")) camera.position.y -= passo;
    if (camera.position.y < ALTURA_MIN) camera.position.y = ALTURA_MIN;
  }

  return { entrar, sair, update, estaAtivo: () => ativo };
}

// ---------------------------------------------------------------- gravação
/** Grava o canvas para WebM. Devolve {parar()} ou null se não for suportado. */
export function gravarCanvas(canvas, fps = 60, nomeFicheiro = "armazem.webm") {
  if (typeof MediaRecorder === "undefined" || !canvas.captureStream) {
    console.warn("[voo] MediaRecorder/captureStream indisponivel neste browser");
    return null;
  }
  const tipos = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
  const mime = tipos.find(t => MediaRecorder.isTypeSupported(t));
  const stream = canvas.captureStream(fps);
  const rec = new MediaRecorder(stream, mime ? { mimeType: mime,
                                                videoBitsPerSecond: 12_000_000 }
                                            : undefined);
  const pedacos = [];
  rec.ondataavailable = (e) => { if (e.data.size) pedacos.push(e.data); };
  rec.onstop = () => {
    const blob = new Blob(pedacos, { type: mime || "video/webm" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = nomeFicheiro;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  };
  rec.start();
  const t0 = performance.now();
  return {
    parar: () => { if (rec.state !== "inactive") rec.stop(); },
    duracao_s: () => (performance.now() - t0) / 1000,
  };
}
