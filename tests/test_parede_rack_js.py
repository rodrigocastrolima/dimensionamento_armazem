"""Racks encostadas à parede ficam a 10 cm dela (2026-11-18). A regra vive no
canvas (index.html: `afastarRacksDaParede`); aqui corre-se a função REAL do
index.html em Node, com um `document` falso. Salta se não houver Node."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parents[1] / "warehouse" / "static" / "index.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node não está instalado")


def _fonte() -> str:
    html = INDEX.read_text(encoding="utf-8")
    ini = html.index("const FOLGA_PAREDE_RACK_M")
    fim = html.index("window.afastarRacksDaParede")
    return html[ini:fim]


def _correr(blocos: list[dict], comp: float = 66, larg: float = 19.5, vezes: int = 1) -> list[dict]:
    js = (f"const document = {{ getElementById: id => ({{ value: String(id === 'hangar-comp' ? {comp} : {larg}) }}) }};\n"
          + _fonte()
          + f"\nconst blocos = {json.dumps(blocos)};\n"
            f"for (let i = 0; i < {vezes}; i++) afastarRacksDaParede(blocos);\n"
            "console.log(JSON.stringify(blocos));")
    out = subprocess.run([NODE, "-e", js], capture_output=True, text=True, check=True).stdout
    return json.loads(out.strip().splitlines()[-1])


def _rack(x, y, w=10.0, h=1.2, rot=0, tipo="rack_manual"):
    return {"type": tipo, "x": x, "y": y, "w": w, "h": h, "rot": rot}


def test_fila_encostada_a_parede_de_cima_e_da_esquerda_fica_a_10cm():
    a, b = _correr([_rack(12.5, 0), _rack(0, 5)])
    assert (a["x"], a["y"]) == (12.5, 0.1)
    assert (b["x"], b["y"]) == (0.1, 5)


def test_fila_encostada_a_parede_de_baixo_e_da_direita():
    a, b = _correr([_rack(30, 19.5 - 1.2), _rack(66 - 10, 5)])
    assert a["y"] == pytest.approx(19.5 - 0.1 - 1.2) and a["x"] == 30
    assert b["x"] == pytest.approx(66 - 0.1 - 10) and b["y"] == 5


def test_so_mexe_nas_encostadas_e_so_nas_racks():
    longe = _rack(5, 5)
    zona = {"type": "zone", "x": 0, "y": 0, "w": 3, "h": 3, "rot": 0}
    obst = {"type": "obstaculo", "x": 0, "y": 0, "w": 1, "h": 1, "rot": 0}
    fora = _rack(60, 5)                      # sai pela direita (max 70 > 66): não se mexe
    ja_ok = _rack(0.1, 0.25)                 # já a 10 cm: fica
    r = _correr([longe, zona, obst, fora, ja_ok])
    assert (r[0]["x"], r[0]["y"]) == (5, 5)
    assert (r[1]["x"], r[1]["y"]) == (0, 0) and (r[2]["x"], r[2]["y"]) == (0, 0)   # zonas/obstáculos encostam
    assert r[3]["x"] == 60
    assert (r[4]["x"], r[4]["y"]) == (0.1, 0.25)


def test_bloco_rodado_conta_a_aresta_rodada():
    # 10 x 2 a 90°: ocupa 2 em x e 10 em y; centro (1, 5) -> encostado em x=0 e y=0
    (r,) = _correr([_rack(-4, 4, w=10, h=2, rot=90)])
    assert r["x"] == pytest.approx(-4 + 0.1) and r["y"] == pytest.approx(4 + 0.1)


def test_bloco_gerado_tambem_e_uma_rack():
    (r,) = _correr([_rack(0, 0, w=14.7, h=16.2, tipo="racking")])
    assert (r["x"], r["y"]) == (0.1, 0.1)


def test_idempotente_e_sem_cubo_nao_faz_nada():
    uma = _correr([_rack(0, 0)])
    tres = _correr([_rack(0, 0)], vezes=3)
    assert uma == tres
    (r,) = _correr([_rack(0, 0)], comp=0, larg=0)
    assert (r["x"], r["y"]) == (0, 0)
