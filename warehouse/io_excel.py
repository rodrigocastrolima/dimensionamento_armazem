"""Leitura do master data e escrita dos resultados (openpyxl). Unidades: metros."""
from __future__ import annotations
import dataclasses
import math
import shutil
import tempfile
from pathlib import Path

import openpyxl

from .models import (Article, BoxType, RackType, LoadType, Empilhamento,
                     BoxAssignment, RackRequirement, WarehouseDimensioning, Zona,
                     CaixaEspecial)


_EMPILHAMENTO_MAP = {
    "NAO_EMPILHAVEL": Empilhamento.NAO_EMPILHAVEL,
    "EMPILHAVEL": Empilhamento.EMPILHAVEL,
    # compatibilidade com o formato antigo (Sim/Não/Aninhável), caso apareça:
    # ANINHAVEL deixou de ser um valor de Empilhamento (ver Article.
    # eixo_aninhagem/EIXO_ANINHAGEM, CLAUDE.md "separar aninhável de
    # empilhável") -- cai no default (EMPILHAVEL) abaixo, que é o mais
    # correto para uma peça que ainda tenha esse texto por migrar.
    "SIM": Empilhamento.EMPILHAVEL,
    "NÃO": Empilhamento.NAO_EMPILHAVEL,
    "NAO": Empilhamento.NAO_EMPILHAVEL,
}



def _abrir_workbook(path: Path, **kw):
    """openpyxl.load_workbook que não falha quando o Excel tem o master data
    aberto: nesse caso o ficheiro fica bloqueado para abertura direta
    (PermissionError), mas pode copiar-se. Lê-se então uma cópia temporária --
    o que está gravado em disco, sem as alterações por guardar no Excel."""
    try:
        return openpyxl.load_workbook(path, **kw)
    except PermissionError:
        with tempfile.TemporaryDirectory() as tmp:
            copia = Path(tmp) / Path(path).name
            shutil.copy2(path, copia)
            return openpyxl.load_workbook(copia, **kw)


def _metros(valor, folha: str, linha_excel: int, coluna: str) -> float:
    """Célula de cota -> float. Aceita número, ou texto com vírgula decimal
    ('1,4', o que o Excel em pt-PT guarda quando a célula tem formato Texto).
    Texto que não seja número levanta ValueError com folha/linha/coluna, em
    vez do 'could not convert string to float' sem localização."""
    if isinstance(valor, str):
        try:
            return float(valor.strip().replace(",", "."))
        except ValueError:
            pass
    else:
        try:
            return float(valor)
        except (TypeError, ValueError):
            pass
    raise ValueError(
        f"Folha «{folha}», linha {linha_excel}, coluna {coluna}: "
        f"'{valor}' não é um número")


def _parse_empilhamento(valor) -> Empilhamento:
    chave = str(valor).strip().upper() if valor else ""
    return _EMPILHAMENTO_MAP.get(chave, Empilhamento.EMPILHAVEL)


# Tipos de carga que CAIXA_FORCADA pode designar: só as entradas do catálogo
# «Caixas». OVERSIZE não é uma caixa e OVERSIZE2 vem da folha «Caixas Especiais».
_CAIXAS_FORCAVEIS = (LoadType.XS, LoadType.S, LoadType.M, LoadType.L,
                     LoadType.MEIA, LoadType.PALLET)


def _parse_caixa_forcada(sku: str, caixa_val, pecas_val) -> tuple[str, int]:
    """CAIXA_FORCADA / PECAS_CAIXA_FORCADA -> ("L", 4), ("L", 0) ou ("", 0).

    Valor inválido dá ValueError com o SKU, nunca é ignorado: uma exceção
    manual mal escrita que caísse em silêncio no best-fit normal era
    exatamente o erro que a coluna existe para evitar."""
    caixa = str(caixa_val).strip().upper() if caixa_val is not None else ""
    validas = [t.value for t in _CAIXAS_FORCAVEIS]
    if caixa and caixa not in validas:
        raise ValueError(f"SKU {sku}: CAIXA_FORCADA='{caixa_val}' não é uma caixa do "
                         f"catálogo (usar {', '.join(validas)} ou deixar vazio)")

    pecas_txt = str(pecas_val).strip() if pecas_val is not None else ""
    if not pecas_txt:
        return caixa, 0
    try:
        pecas = float(pecas_txt)
    except ValueError:
        pecas = 0.0
    if pecas < 1 or pecas != int(pecas):
        raise ValueError(f"SKU {sku}: PECAS_CAIXA_FORCADA='{pecas_val}' tem de ser um "
                         f"inteiro >= 1 (ou vazio)")
    if not caixa:
        raise ValueError(f"SKU {sku}: PECAS_CAIXA_FORCADA={int(pecas)} sem CAIXA_FORCADA "
                         f"-- indicar em que caixa vão essas peças")
    return caixa, int(pecas)


def _ws(wb: openpyxl.Workbook, nome: str):
    if nome not in wb.sheetnames:
        raise ValueError(f"Sheet '{nome}' não encontrada em {wb.path if hasattr(wb, 'path') else '?'}")
    return wb[nome]


# Fallback genérico para peças sem geometria (0x0x0 -- ver dimensao_segura).
# Partilhado por qualquer backend de dados (Excel aqui, Supabase em
# db_supabase.py): a mesma regra de negócio, não duas cópias que podem divergir.
_DIM_MINIMA_M = (0.05, 0.05, 0.01)


def dimensao_segura(comp: float, larg: float, alt: float) -> tuple[float, float, float, bool]:
    """Substitui dimensões 0x0x0 por um fallback pequeno genérico.

    Uma peça sem geometria faz o boxing cair no fallback de 1 peça/palete
    (nenhuma caixa normal "cabe" com dimensão 0), o que explode o nº de
    unidades e trava o packer 2D em allocate_to_racks. Assume-se um item
    pequeno genérico só para destravar o cálculo -- resultado pouco fiável
    para estas peças, a corrigir com a geometria real na origem dos dados.

    Devolve (comp, larg, alt, foi_substituida) -- o chamador decide como
    reportar a substituição (ver load_articles/db_supabase.load_articles)."""
    if comp <= 0 or larg <= 0 or alt <= 0:
        return (*_DIM_MINIMA_M, True)
    return comp, larg, alt, False


def load_articles(path: Path) -> list[Article]:
    """Folha «Peças» -> list[Article].

    Colunas lidas pelo NOME do cabeçalho (linha 1), não por posição fixa: a
    folha pode ter colunas extra inseridas a meio -- ler por posição lia
    valores da coluna errada nesses casos, silenciosamente.

    `Article.stock_max_un` sai daqui como um placeholder (= 'Stock_máx_un'
    bruto); o valor definitivo é decidido pelo Passo 0 do pipeline
    (boxing.aplicar_stock_autonomia), não por esta função -- ver
    Config.autonomia_minima_carros."""
    wb = _abrir_workbook(path, data_only=True)
    ws = _ws(wb, "Peças")
    rows = list(ws.iter_rows(values_only=True))

    header = [str(c).strip() if c else "" for c in rows[0]]
    idx = {name: i for i, name in enumerate(header)}

    def col(name, required=True):
        if name not in idx:
            if required:
                raise ValueError(f"Coluna '{name}' não encontrada na folha «Peças»")
            return None
        return idx[name]

    i_sku = col("SKU")
    i_desc = col("Descrição")
    i_comp = col("Comprimento_m")
    i_larg = col("Largura_m")
    i_alt = col("Altura_m")
    i_peso = col("Peso_kg")
    # 'Stock_máx_un' é só REFERÊNCIA agora (Article.stock_atual_un): o stock que
    # de facto alimenta o dimensionamento (Article.stock_max_un) é decidido pelo
    # Passo 0 (boxing.stock_para_autonomia), a partir de Peças_por_carro e
    # Config.autonomia_minima_carros -- não se lê mais pronto do master data
    # (era o antigo 'Stock_máx_10un_a_utilizar' da v2, calculado à mão fora do
    # código; ver CLAUDE.md). Colunas 'Stock_máx_10un_teórico'/'Diferença .../
    # 'Stock_máx_10un_a_utilizar' que ainda existam na folha ficam por ler.
    i_stock = col("Stock_máx_un")
    i_pecas_carro = col("Peças_por_carro")
    i_orient = col("ALTURA_OVERRIDE")
    i_eixo_aninh = col("EIXO_ANINHAGEM")
    i_empilh = col("Empilhável")
    # Fragilidade/Geometria/Classe são só informativas (mostradas na UI, sem
    # uso no cálculo -- ver boxing/racking/area/layout, nenhum as lê) --
    # colunas opcionais, para poderem ser removidas da folha sem partir a
    # leitura.
    i_frag = col("Fragilidade", required=False)
    i_geom = col("Geometria", required=False)
    i_classe = col("Classe", required=False)
    i_estacao = col("Estação", required=False)
    # Exceção manual ao best-fit (ver Article.caixa_forcada) -- opcionais.
    i_caixa_forc = col("CAIXA_FORCADA", required=False)
    i_pecas_forc = col("PECAS_CAIXA_FORCADA", required=False)

    articles = []
    skus_sem_geometria = []
    for r in rows[1:]:
        if not r or r[i_sku] is None:
            continue

        sku = str(r[i_sku])
        desc = str(r[i_desc]) if r[i_desc] else ""
        comp = float(r[i_comp]) if r[i_comp] else 0.0
        larg = float(r[i_larg]) if r[i_larg] else 0.0
        alt = float(r[i_alt]) if r[i_alt] else 0.0

        comp, larg, alt, sem_geometria = dimensao_segura(comp, larg, alt)
        if sem_geometria:
            skus_sem_geometria.append(sku)

        peso_val = r[i_peso]
        peso_kg = float(peso_val) if peso_val is not None and str(peso_val) != "#N/A" else 0.0

        # ceil, NÃO int: pode ser fracionário (deriva de Peças_por_carro, que
        # também o é). O int() truncava e SKUs com valor entre 0 e 1 ficavam a
        # 0 -- 0 caixas, logo DESAPARECIAM dos resultados sem aviso, apesar de
        # serem peças reais que precisam de espaço. Uma peça física não se
        # guarda em frações e o stock é um TETO de dimensionamento: arredonda-se
        # sempre para cima, como no resto do pipeline.
        stock_val = r[i_stock]
        stock = math.ceil(float(stock_val)) if stock_val else 0

        pecas_por_carro_val = r[i_pecas_carro]
        if pecas_por_carro_val is None or str(pecas_por_carro_val) == "#N/A":
            raise ValueError(f"SKU {sku}: Peças_por_carro is required but missing or #N/A")
        # float, NÃO int: o valor é fracionário por natureza (0.5 = 1 peça a cada
        # 2 carros). O int() que aqui estava truncava e os 138 SKUs a 0.5 ficavam
        # a 0 -- deixavam de pedir zona de picking sem qualquer aviso, porque
        # ceil(0 / n) = 0. Também perdia a fração em 1.5, 17.85, etc.
        pecas_por_carro = float(pecas_por_carro_val)

        altura_override = str(r[i_orient]).strip() if r[i_orient] else ""
        eixo_aninhagem = str(r[i_eixo_aninh]).strip() if r[i_eixo_aninh] else ""
        empilh = r[i_empilh]
        empilhamento = _parse_empilhamento(empilh)
        frag = str(r[i_frag]) if i_frag is not None and r[i_frag] else "Normal"
        geom = str(r[i_geom]) if i_geom is not None and r[i_geom] else "Regular"

        classe = str(r[i_classe]).strip() if i_classe is not None and r[i_classe] else "RIGIDO"

        estacao_val = r[i_estacao] if i_estacao is not None else None
        estacao = str(estacao_val).strip() if estacao_val else ""

        caixa_forcada, pecas_caixa_forcada = _parse_caixa_forcada(
            sku,
            r[i_caixa_forc] if i_caixa_forc is not None else None,
            r[i_pecas_forc] if i_pecas_forc is not None else None)

        articles.append(Article(
            sku=sku,
            descricao=desc,
            comp_m=comp,
            larg_m=larg,
            alt_m=alt,
            peso_kg=peso_kg,
            # stock_max_un = stock_atual_un aqui é só um placeholder inicial:
            # quem decide o valor definitivo é o Passo 0 (boxing.
            # aplicar_stock_autonomia), chamado logo a seguir no pipeline
            # (main.run). Fica igual ao atual só para quem chamar load_articles
            # diretamente sem passar pelo Passo 0 não ficar com stock a zero.
            stock_max_un=stock,
            stock_atual_un=stock,
            pecas_por_carro=pecas_por_carro,
            altura_override=altura_override,
            eixo_aninhagem=eixo_aninhagem,
            empilhamento=empilhamento,
            fragilidade=frag,
            geometria=geom,
            classe=classe,
            estacao=estacao,
            caixa_forcada=caixa_forcada,
            pecas_caixa_forcada=pecas_caixa_forcada,
        ))

    if skus_sem_geometria:
        print(f"AVISO: {len(skus_sem_geometria)} SKU(s) sem geometria (0x0x0) na folha -- "
              f"a usar dimensão mínima genérica (0.05 x 0.05 x 0.01 m), resultado pouco "
              f"fiável para estas peças: {', '.join(skus_sem_geometria)}")

    return articles


def load_catalog(path: Path) -> list[BoxType]:
    """Folha «Caixas» -> list[BoxType] (S/M/L + PALLET)."""
    wb = _abrir_workbook(path, data_only=True)
    ws = _ws(wb, "Caixas")
    rows = list(ws.iter_rows(values_only=True))
    catalog = []
    for r in rows[1:]:
        nome, ic, il, ia, ec, el, ea, peso_max, *_ = r
        if nome is None:
            continue
        catalog.append(BoxType(
            nome=LoadType(str(nome).strip()),
            int_comp_m=float(ic),
            int_larg_m=float(il),
            int_alt_m=float(ia),
            ext_comp_m=float(ec),
            ext_larg_m=float(el),
            ext_alt_m=float(ea),
            peso_max_kg=float(peso_max),
        ))
    return catalog


def load_rack_types(path: Path) -> list[RackType]:
    """Folha «Racks» -> list[RackType]."""
    wb = _abrir_workbook(path, data_only=True)
    ws = _ws(wb, "Racks")
    rows = list(ws.iter_rows(values_only=True))
    header = [str(c).strip() if c else "" for c in rows[0]]
    corredor_col = header.index("Corredor_m") if "Corredor_m" in header else None
    # «Alturas_Niveis_m» (opcional): uma altura por nível, separadas por
    # vírgula (ex. "0.8,0.25,0.25,0.25,0.25"), índice 0 = nível mais baixo --
    # ver RackType.alturas_niveis_m. Sem esta coluna (ou célula vazia numa
    # linha), essa tipologia mantém o comportamento uniforme de sempre.
    alturas_col = header.index("Alturas_Niveis_m") if "Alturas_Niveis_m" in header else None
    racks = []
    # Tokens da coluna "Aceita" que não batem com nenhum LoadType (ex.: "LONGO",
    # removido do enum -- ver CLAUDE.md "cantilever/LONGO removida") eram
    # silenciosamente ignorados: uma tipologia inteira podia ficar com
    # `aceita=[]` (não aceita NADA, contribui zero capacidade em tudo) sem
    # nenhum sinal de que isso aconteceu. Recolhe-se aqui para avisar, em vez
    # de deixar essa tipologia morta parecer só mais uma linha normal.
    tokens_desconhecidos: dict[str, list[str]] = {}
    for r in rows[1:]:
        if r[0] is None:
            continue
        nome, larg, prof, alt_nivel, niveis, aceita_str, footprint, *rest = r
        aceita_tokens = [t.strip() for t in str(aceita_str).split(",") if t.strip()]
        aceita = []
        for t in aceita_tokens:
            try:
                aceita.append(LoadType(t))
            except ValueError:
                tokens_desconhecidos.setdefault(str(nome), []).append(t)
        corredor = float(r[corredor_col]) if corredor_col is not None and r[corredor_col] else 0.0

        alturas_niveis: tuple[float, ...] = ()
        alturas_cel = r[alturas_col] if alturas_col is not None else None
        if alturas_cel not in (None, ""):
            alturas_niveis = tuple(float(x) for x in str(alturas_cel).split(",") if x.strip())
            if len(alturas_niveis) != int(niveis):
                raise ValueError(
                    f"Racks: '{nome}' tem Niveis={int(niveis)} mas "
                    f"Alturas_Niveis_m só dá {len(alturas_niveis)} valor(es) "
                    f"({alturas_cel!r}) -- têm de bater certo.")

        racks.append(RackType(
            nome=str(nome),
            larg_m=float(larg),
            prof_m=float(prof),
            alt_nivel_m=max(alturas_niveis) if alturas_niveis else float(alt_nivel),
            niveis=int(niveis),
            aceita=aceita,
            footprint_m2=float(footprint) if footprint else 0.0,
            corredor_m=corredor,
            alturas_niveis_m=alturas_niveis,
        ))

    if tokens_desconhecidos:
        for nome, tokens in tokens_desconhecidos.items():
            rack_correspondente = next((r for r in racks if r.nome == nome), None)
            ficou_morta = rack_correspondente is not None and not rack_correspondente.aceita
            print(f"AVISO: tipologia '{nome}': token(s) desconhecido(s) na coluna "
                  f"'Aceita' ignorado(s) -- {', '.join(tokens)} não corresponde(m) a "
                  f"nenhum LoadType válido."
                  + (f" Esta tipologia ficou com 'aceita' VAZIO -- não aceita "
                     f"nenhum tipo de carga, contribui zero capacidade em tudo. "
                     f"Corrige a coluna 'Aceita' na folha «Racks» ou remove a linha."
                     if ficou_morta else ""))
    return racks


def load_zones(path: Path) -> list[Zona]:
    """Folha «Zonas» -> list[Zona]. Sheet opcional: devolve [] se não existir.

    Colunas lidas pelo NOME do cabeçalho (linha 1), não por posição -- como
    load_articles/load_rack_types. Antes lia por posição (nome, categoria,
    comp, larg, *resto, com resto[0] = justificação): quando a folha ganhou
    uma coluna 'Altura_m' a seguir a Larg_m (2026-09-28, equipamento real por
    zona), essa altura passou a ser lida em silêncio como se fosse a
    justificação (ex.: '3', '0.95') -- 'Justificacao' já não é a 5ª coluna.
    'Altura_m' fica por ler por agora (zonas não têm altura no 3D, ver
    CLAUDE.md); só se evita o cruzamento de colunas."""
    wb = _abrir_workbook(path, data_only=True)
    if "Zonas" not in wb.sheetnames:
        return []
    ws = wb["Zonas"]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    header = [str(c).strip() if c else "" for c in rows[0]]
    idx = {name: i for i, name in enumerate(header)}

    def col(name, required=True):
        if name not in idx:
            if required:
                raise ValueError(f"Coluna '{name}' não encontrada na folha «Zonas»")
            return None
        return idx[name]

    i_nome = col("Nome")
    i_categoria = col("Categoria", required=False)
    i_comp = col("Comp_m")
    i_larg = col("Larg_m")
    i_just = col("Justificacao", required=False)
    if i_just is None:
        i_just = col("Justificação", required=False)

    zonas = []
    for n_linha, r in enumerate(rows[1:], start=2):
        if not r or r[i_nome] is None:
            continue
        categoria = r[i_categoria] if i_categoria is not None else None
        justificacao = str(r[i_just]) if i_just is not None and r[i_just] else ""
        zonas.append(Zona(
            nome=str(r[i_nome]),
            categoria=str(categoria) if categoria else "Outra",
            comp_m=_metros(r[i_comp], "Zonas", n_linha, "Comp_m"),
            larg_m=_metros(r[i_larg], "Zonas", n_linha, "Larg_m"),
            justificacao=justificacao,
        ))
    return zonas


CAIXAS_ESPECIAIS_FOLHA = "Caixas Especiais"
CAIXAS_ESPECIAIS_COLUNAS = ("SKU", "Quantidade", "Comprimento_m", "Largura_m", "Altura_m",
                            "Notas", "Número/ID", "Estação")


def _chave_cabecalho(nome) -> str:
    """'Número/ID' -> 'numero/id': sem acentos, minúsculas -- para o
    cabeçalho não falhar por um acento ou maiúscula escritos à mão."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(nome or "")).encode("ascii", "ignore").decode()
    return s.strip().lower()


def _id_carrinho(v) -> str:
    """Número/ID como texto estável: 1 / 1.0 / '1' -> '1'."""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def load_caixas_especiais(path: Path) -> list[CaixaEspecial]:
    """Folha «Caixas Especiais» -> list[CaixaEspecial], 1 por CARRINHO (ver
    models.CaixaEspecial / Config.caixas_especiais / boxing.choose_load).
    Sheet opcional: devolve [] se não existir.

    Uma linha por SKU: SKU | Quantidade | Comprimento_m | Largura_m |
    Altura_m | Notas | Número/ID | Estação. As linhas com o mesmo
    Número/ID são o MESMO carrinho físico e juntam-se num só registo, cada
    SKU com a sua Quantidade (= stock desse SKU na linha). Dimensões,
    Notas e Estação são do carrinho: basta preenchê-las numa das linhas;
    se vierem em várias têm de coincidir -- um carrinho não tem dois
    tamanhos nem está em duas estações. Número/ID vazio = carrinho só
    desse SKU (id = o próprio SKU).

    Folha presente mas mal preenchida (colunas em falta, quantidade ou
    cota inválida, SKU repetido, carrinho incoerente) -> ValueError com a
    linha do Excel, nunca [] em silêncio: o leitor antigo (formato
    'SKU:qtd | SKU:qtd' numa só célula) ignorava o que não reconhecia, e
    quando a folha passou a este formato a linha ficou sem carrinho
    nenhum sem qualquer aviso.

    O SKU não leva strip -- tem de bater com load_articles, que também não
    o faz (há SKUs reais com espaço no fim, ex. 'BEN10.FHB001_____DOOR
    STRUCTURE ASSY ')."""
    wb = _abrir_workbook(path, data_only=True)
    if CAIXAS_ESPECIAIS_FOLHA not in wb.sheetnames:
        return []
    rows = list(wb[CAIXAS_ESPECIAIS_FOLHA].iter_rows(values_only=True))
    if not rows:
        return []
    idx = {_chave_cabecalho(c): i for i, c in enumerate(rows[0]) if c is not None}

    def col(nome: str) -> int | None:
        return idx.get(_chave_cabecalho(nome))

    obrigatorias = ("SKU", "Quantidade", "Comprimento_m", "Largura_m", "Altura_m")
    em_falta = [c for c in obrigatorias if col(c) is None]
    if em_falta:
        raise ValueError(
            f"Folha «{CAIXAS_ESPECIAIS_FOLHA}»: faltam as colunas {', '.join(em_falta)} "
            f"(esperado: {' | '.join(CAIXAS_ESPECIAIS_COLUNAS)}).")
    i_sku, i_qtd = col("SKU"), col("Quantidade")
    i_cotas = {"Comprimento_m": col("Comprimento_m"), "Largura_m": col("Largura_m"),
               "Altura_m": col("Altura_m")}
    i_notas, i_id, i_est = col("Notas"), col("Número/ID"), col("Estação")

    def celula(r, i):
        v = r[i] if i is not None and i < len(r) else None
        return None if v is None or str(v).strip() == "" else v

    def numero(v, onde: str, nome: str) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{onde}: {nome} '{v}' não é um número.")
        if f <= 0:
            raise ValueError(f"{onde}: {nome} tem de ser maior que zero.")
        return f

    caixas: dict[str, CaixaEspecial] = {}
    vistos: set[str] = set()
    for n, r in enumerate(rows[1:], start=2):
        sku = celula(r, i_sku)
        if sku is None:
            continue
        sku = str(sku)
        onde = f"Folha «{CAIXAS_ESPECIAIS_FOLHA}», linha {n} ({sku.strip()})"
        if sku in vistos:
            raise ValueError(f"{onde}: SKU repetido -- cada SKU só pode estar num carrinho.")
        vistos.add(sku)

        qtd = numero(celula(r, i_qtd), onde, "Quantidade")
        if not qtd.is_integer():
            raise ValueError(f"{onde}: Quantidade tem de ser um número inteiro de peças.")
        id_raw = celula(r, i_id)
        id_ = _id_carrinho(id_raw) if id_raw is not None else sku
        notas = celula(r, i_notas)
        notas = str(notas).strip() if notas is not None else ""
        estacao = celula(r, i_est)
        estacao = str(estacao).strip() if estacao is not None else ""

        caixa = caixas.get(id_)
        if caixa is None:
            cotas = {}
            for nome, i in i_cotas.items():
                v = celula(r, i)
                if v is None:
                    raise ValueError(f"{onde}: {nome} em falta (1ª linha do carrinho {id_}).")
                cotas[nome] = numero(v, onde, nome)
            caixa = CaixaEspecial(quantidades={}, comp_m=cotas["Comprimento_m"],
                                  larg_m=cotas["Largura_m"], alt_m=cotas["Altura_m"],
                                  notas=notas, id=id_, estacao=estacao)
            caixas[id_] = caixa
        else:
            atuais = {"Comprimento_m": caixa.comp_m, "Largura_m": caixa.larg_m,
                      "Altura_m": caixa.alt_m}
            for nome, i in i_cotas.items():
                v = celula(r, i)
                if v is not None and abs(numero(v, onde, nome) - atuais[nome]) > 1e-9:
                    raise ValueError(
                        f"{onde}: {nome}={v} diferente do resto do carrinho {id_} "
                        f"({atuais[nome]}) -- um carrinho só tem um tamanho.")
            if estacao and caixa.estacao and estacao != caixa.estacao:
                raise ValueError(
                    f"{onde}: Estação '{estacao}' diferente do resto do carrinho {id_} "
                    f"('{caixa.estacao}') -- um carrinho só está numa estação.")
            caixa.estacao = caixa.estacao or estacao
            caixa.notas = caixa.notas or notas
        caixa.quantidades[sku] = int(qtd)
    return list(caixas.values())


def caixas_especiais_por_sku(caixas: list[CaixaEspecial]) -> dict[str, CaixaEspecial]:
    """Expande list[CaixaEspecial] (cada uma podendo cobrir vários SKUs) num
    dict indexado por SKU individual -- é isto que Config.caixas_especiais
    guarda e boxing.choose_load consulta. Chaves diferentes podem apontar
    para o MESMO objeto quando partilham registo (mesmo equipamento)."""
    return {sku: caixa for caixa in caixas for sku in caixa.skus}


def com_carrinhos_da_linha(cfg):
    """Config.unidade_da_linha: devolve o cfg com skus_carrinho_linha
    preenchido a partir da folha «Caixas Especiais» (os SKUs que na linha vão
    em carrinho e por isso NÃO herdam a unidade da linha). Desligado -> o
    próprio cfg, sem ler nada. Lido a cada chamada (e não uma vez ao subir o
    servidor) para acompanhar edições ao master data."""
    if not cfg.unidade_da_linha:
        return cfg
    skus = caixas_especiais_por_sku(load_caixas_especiais(cfg.master_data))
    return dataclasses.replace(cfg, skus_carrinho_linha=frozenset(skus))


def write_pecas_com_caixa(master_path: Path, out_path: Path,
                          assignments: list[BoxAssignment]) -> None:
    """Copia a folha «Peças» do master data tal e qual (todas as colunas,
    valores originais) e acrescenta a coluna 'Tipo_Caixa': a unidade de carga
    escolhida no Passo 1 (boxing) para cada SKU."""
    wb_in = _abrir_workbook(master_path, data_only=True)
    ws_in = _ws(wb_in, "Peças")
    por_sku = {a.sku: a.load_type.value for a in assignments}

    wb_out = openpyxl.Workbook()
    ws_out = wb_out.active
    ws_out.title = "Peças"
    rows = list(ws_in.iter_rows(values_only=True))
    if rows:
        from openpyxl.styles import Font
        ws_out.append(list(rows[0]) + ["Tipo_Caixa"])
        for cell in ws_out[1]:
            cell.font = Font(bold=True)
        for r in rows[1:]:
            if r[0] is None:
                continue
            ws_out.append(list(r) + [por_sku.get(str(r[0]), "")])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb_out.save(out_path)


def flatten_posicoes_para_detalhe(posicoes) -> list:
    """Achata PosicaoChao -> linhas simples (rack_numero, rack_tipologia, sku,
    descricao, load_type, pecas_nesta_caixa) para a sheet 'Detalhe' do Excel.
    rack_numero é sintético: 1 por módulo físico, sequencial por tipologia.

    Vive aqui (e não no main) porque tem dois consumidores -- o CLI
    (main.run) e o /api/gerar do servidor web -- e ambos têm de produzir
    exatamente a mesma folha 'Detalhe'."""
    from types import SimpleNamespace
    linhas = []
    rack_numero = 0
    for pos in posicoes:
        for bloco in pos.blocos:
            rack_numero += 1
            for nivel in bloco.niveis:
                for caixa in nivel.caixas:
                    linhas.append(SimpleNamespace(
                        rack_numero=rack_numero, rack_tipologia=pos.rack_nome,
                        sku=caixa.sku, descricao=caixa.descricao,
                        load_type=caixa.load_type, pecas_nesta_caixa=caixa.pecas_nesta_caixa,
                    ))
    return linhas


def write_results(path: Path,
                  assignments: list[BoxAssignment],
                  requirements: list[RackRequirement],
                  dimensioning: WarehouseDimensioning,
                  slots: list,
                  avisos: list[str] | None = None,
                  warehouse_layout=None,
                  zonas: list[Zona] | None = None,
                  resumo_area=None,
                  articles: list[Article] | None = None,
                  justificacoes: list | None = None,
                  autonomia_resultados: list | None = None) -> None:
    """Escreve resultados.xlsx com sheets: Lógica, Resumo, Detalhe, Autonomia
    (se articles for dado), Validação (se justificacoes for dado) e Avisos
    (se houver)."""
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()

    # ------------------------------------------------------------------ Lógica
    ws_log = wb.active
    ws_log.title = "Lógica"

    def titulo(ws, texto, linha):
        c = ws.cell(linha, 1, texto)
        c.font = Font(bold=True, size=12)

    def par(ws, label, valor, linha):
        ws.cell(linha, 1, label).font = Font(bold=True)
        ws.cell(linha, 2, valor)

    def texto(ws, t, linha, col=1):
        ws.cell(linha, col, t)

    titulo(ws_log, "Pipeline de dimensionamento de armazém", 1)
    texto(ws_log, "Transforma o stock máximo de cada artigo em nº de racks e área de chão.", 2)

    titulo(ws_log, "Passo 1 — Repacking (boxing.py)", 4)
    texto(ws_log, "Best-fit entre S/M/L/MEIA/PALLET — escolhe a caixa que minimiza o nº total de", 8)
    texto(ws_log, "unidades de carga para o stock máximo (não é 'a 1ª onde cabe', testa todas as opções).", 9)
    texto(ws_log, "Plano horizontal (comp x larg): envelope 2D, testa as 2 orientações do artigo no plano.", 10)
    texto(ws_log, "ALTURA_OVERRIDE vazio (a maioria): testa ainda as 3 cotas como altura, fica com a que rende mais;", 11)
    texto(ws_log, "'x'/'y'/'z' força essa cota como altura. Fator de enchimento (encaixe imperfeito): todas as classes.", 12)
    texto(ws_log, "Eixo vertical (altura): regra de empilhamento — ver 'Parâmetros utilizados' abaixo.", 13)
    texto(ws_log, "Corte adicional pelo peso máximo da caixa (peso_max_caixa / peso_artigo).", 14)
    texto(ws_log, "Output: tipo de carga por SKU + nº de unidades para cobrir o stock máximo (teto).", 15)

    titulo(ws_log, "Passo 2 — Racking (racking.py)", 17)
    texto(ws_log, "Agrupa os SKUs por tipo de carga e calcula quantos racks de cada tipologia são necessários.", 18)
    texto(ws_log, "Cada tipo de carga escolhe, entre os racks compatíveis, o de menor m² por unidade", 19)
    texto(ws_log, "(footprint do rack / capacidade) — não é 'o 1º rack da tabela que aceita'.", 20)
    texto(ws_log, "Capacidade do rack (S/M/L/PALLET): envelope 2D por nível × nº de níveis.", 21)
    texto(ws_log, "Compatibilidade carga ↔ rack via campo 'Aceita' da sheet Racks.", 23)
    texto(ws_log, "Se um tipo de carga não tiver nenhum rack compatível viável, fica registado na sheet", 24)
    texto(ws_log, "'Avisos' (se existir) em vez de ser silenciosamente excluído do resultado.", 25)

    texto(ws_log, "Módulos de rack podem ser empilhados na vertical até à altura do armazém — ver", 24)
    texto(ws_log, "'Empilhamento de módulos de rack' abaixo.", 25)
    texto(ws_log, "Se um tipo de carga não tiver nenhum rack compatível viável, fica registado na sheet", 26)
    texto(ws_log, "'Avisos' (se existir) em vez de ser silenciosamente excluído do resultado.", 27)

    titulo(ws_log, "Empilhamento de módulos de rack (altura do armazém)", 29)
    texto(ws_log, "Distinto do empilhamento de ARTIGOS dentro de uma caixa (Passo 1) e dos NÍVEIS", 30)
    texto(ws_log, "(prateleiras) dentro de UM módulo de rack — ambos já contam para a capacidade do rack.", 31)
    texto(ws_log, "Isto é empilhar módulos INTEIROS uns em cima dos outros, na mesma posição de chão,", 32)
    texto(ws_log, "até ao pé-direito disponível (Config.altura_armazem_m). Qualquer tipologia pode", 33)
    texto(ws_log, "empilhar — calculado independentemente para cada uma, ao seu máximo possível.", 34)
    texto(ws_log, "blocos_empilhados = floor(altura_armazem_m / (Alt_nivel_m × Níveis)) do módulo, mín. 1.", 35)
    texto(ws_log, "Nº de racks (total) = módulos físicos; Posições de chão = footprints distintos", 36)
    texto(ws_log, "(= nº de racks ÷ blocos_empilhados) — só as posições de chão contam para a área.", 37)
    texto(ws_log, "altura_armazem_m = 0 → sem empilhamento de módulos (blocos = 1, comportamento normal).", 38)

    titulo(ws_log, "Passo 3 — Área (area.py)", 40)
    texto(ws_log, "Área de racks = soma(footprint unitário × nº de posições de chão) por tipologia.", 41)
    texto(ws_log, "Footprint unitário = Larg_m × Prof_m (ou valor da coluna Footprint_m2 se preenchido).", 42)
    texto(ws_log, "Corredores e layout não são calculados aqui — são feitos externamente.", 43)

    titulo(ws_log, "Parâmetros utilizados", 45)
    par(ws_log, "Estratégia de escolha de caixa", "Best-fit: minimiza nº total de unidades de carga", 46)
    par(ws_log, "Atribuição de rack", "Por eficiência: menor m² de rack por unidade (entre os compatíveis)", 47)
    par(ws_log, "Packing plano horizontal",
        "Envelope 2D; ALTURA_OVERRIDE vazio roda nos 3 eixos (melhor escolhida)", 48)
    par(ws_log, "Fator de enchimento (todas as classes)",
        "floor(peças_plano_teórico × 70%) — folgas de manuseio/encaixe imperfeito", 49)
    par(ws_log, "Packing por nível de rack", "Envelope 2D (2 orientações, melhor escolhida)", 50)
    par(ws_log, "Empilhamento NAO_EMPILHAVEL", "1 peça de altura, nunca empilha", 52)
    par(ws_log, "Empilhamento EMPILHAVEL",
        "altura cheia somada: floor(altura_disponível / altura_artigo), sem nesting", 53)
    par(ws_log, "EIXO_ANINHAGEM (x/y/z, independente do Empilhável)",
        "nessa cota: total(n) = dimensão × (1 + (n-1) × fator_empilhamento), fator = 30%", 54)
    par(ws_log, "Empilhamento de módulos de rack",
        "floor(altura_armazem_m / altura_do_módulo), por tipologia, independente", 55)

    ws_log.column_dimensions["A"].width = 42
    ws_log.column_dimensions["B"].width = 55

    # ------------------------------------------------------------------ Resumo
    ws_res = wb.create_sheet("Resumo")
    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    hdr_font = Font(bold=True, color="FFFFFF")

    def hdr_row(ws, cols, linha=1):
        for i, c in enumerate(cols, 1):
            cell = ws.cell(linha, i, c)
            cell.fill = hdr_fill
            cell.font = hdr_font
            cell.alignment = Alignment(horizontal="center")

    hdr_row(ws_res, ["Tipologia de rack", "Posições de chão", "Módulos empilhados (altura)",
                     "Nº de racks (total)", "Footprint total (m²)"])
    for rack_nome, info in dimensioning.detalhe.items():
        ws_res.append([rack_nome, info["posicoes_chao"], info["blocos_empilhados"],
                       info["num_racks"], info["footprint_total_m2"]])

    # linha de total
    total_racks = sum(i["num_racks"] for i in dimensioning.detalhe.values())
    total_posicoes = sum(i["posicoes_chao"] for i in dimensioning.detalhe.values())
    ws_res.append(["TOTAL", total_posicoes, "", total_racks, round(dimensioning.area_racks_m2, 2)])
    last = ws_res.max_row
    for col in range(1, 6):
        ws_res.cell(last, col).font = Font(bold=True)

    ws_res.column_dimensions["A"].width = 22
    ws_res.column_dimensions["B"].width = 18
    ws_res.column_dimensions["C"].width = 24
    ws_res.column_dimensions["D"].width = 18
    ws_res.column_dimensions["E"].width = 22

    if warehouse_layout is not None:
        linha = ws_res.max_row + 2
        titulo(ws_res, "Layout físico (filas duplas costas-com-costas + corredores)", linha)
        par(ws_res, "Largura do envolvente (m)", warehouse_layout.largura_total_m, linha + 1)
        par(ws_res, "Profundidade do envolvente (m)", warehouse_layout.profundidade_total_m, linha + 2)
        par(ws_res, "Área total (racks + corredores) (m²)", warehouse_layout.area_total_m2, linha + 3)
        par(ws_res, "Área de corredores (m²)", warehouse_layout.area_corredores_m2, linha + 4)

    if zonas:
        linha = ws_res.max_row + 2
        titulo(ws_res, "Zonas funcionais", linha)
        hdr_row(ws_res, ["Nome", "Categoria", "Área (m²)"], linha + 1)
        for i, z in enumerate(zonas):
            ws_res.cell(linha + 2 + i, 1, z.nome)
            ws_res.cell(linha + 2 + i, 2, z.categoria)
            ws_res.cell(linha + 2 + i, 3, round(z.area_m2, 2))

    if resumo_area is not None:
        linha = ws_res.max_row + 2
        titulo(ws_res, "Área bruta total estimada", linha)
        par(ws_res, "Footprint de racks (m²)", round(resumo_area.area_racks_m2, 2), linha + 1)
        par(ws_res, "Corredores (m²)", round(resumo_area.area_corredores_m2, 2), linha + 2)
        par(ws_res, "Zonas funcionais (m²)", round(resumo_area.area_zonas_m2, 2), linha + 3)
        par(ws_res, "Margem estrutural (m²)", round(resumo_area.margem_estrutural_m2, 2), linha + 4)
        par(ws_res, "ÁREA BRUTA TOTAL ESTIMADA (m²)", round(resumo_area.area_bruta_total_m2, 2), linha + 5)

    # ------------------------------------------------------------------ Detalhe
    ws_det = wb.create_sheet("Detalhe")
    hdr_row(ws_det, ["Rack nº", "Tipologia", "SKU", "Descrição", "Tipo de carga", "Peças nesta caixa/palete"])
    ws_det.freeze_panes = "A2"

    fill_alt = PatternFill("solid", fgColor="DCE6F1")
    ultimo_rack = None
    usar_fill = False

    for s in slots:
        rack_numero = getattr(s, "rack_numero", None)
        if rack_numero != ultimo_rack:
            usar_fill = not usar_fill
            ultimo_rack = rack_numero
        row = [rack_numero, s.rack_tipologia, s.sku, s.descricao,
               s.load_type.value if hasattr(s.load_type, "value") else s.load_type,
               s.pecas_nesta_caixa]
        ws_det.append(row)
        if usar_fill:
            for col in range(1, 7):
                ws_det.cell(ws_det.max_row, col).fill = fill_alt

    ws_det.column_dimensions["A"].width = 10
    ws_det.column_dimensions["B"].width = 18
    ws_det.column_dimensions["C"].width = 22
    ws_det.column_dimensions["D"].width = 30
    ws_det.column_dimensions["E"].width = 14
    ws_det.column_dimensions["F"].width = 16

    # ---------------------------------------------------------------- Autonomia
    # Autonomia em carros por SKU (stock usado / peças_por_carro) -- um SKU
    # por linha, tal como BoxAssignment já é (não repete por caixa/rack como
    # a folha Detalhe). Serve de referência para medir o efeito de dar
    # autonomia extra a SKUs específicos (racks fantasma, Passo 4): a mesma
    # coluna, corrida antes/depois, mostra exatamente quem subiu e quanto.
    if articles:
        article_idx = {a.sku: a for a in articles}
        autonomia_idx = {r.sku: r for r in (autonomia_resultados or [])}
        ws_aut = wb.create_sheet("Autonomia")
        hdr_row(ws_aut, ["SKU", "Descrição", "Tipo de carga",
                        "Stock atual (un)", "Stock teórico p/ autonomia mínima (un)",
                        "Stock usado (un)", "Peças/carro", "Autonomia (carros)",
                        "Override manual?"])
        ws_aut.freeze_panes = "A2"
        for a in assignments:
            art = article_idx.get(a.sku)
            if art is None:
                continue
            r = autonomia_idx.get(a.sku)
            pecas_carro = art.pecas_por_carro
            autonomia = round(art.stock_max_un / pecas_carro, 2) if pecas_carro > 0 else None
            ws_aut.append([
                a.sku, art.descricao,
                a.load_type.value if hasattr(a.load_type, "value") else a.load_type,
                r.stock_atual_un if r else art.stock_atual_un,
                r.stock_teorico_un if r else "-",
                art.stock_max_un, pecas_carro, autonomia if autonomia is not None else "-",
                ("Sim" if r.override_manual else "Não") if r else "-",
            ])
        larguras_aut = [22, 30, 14, 16, 30, 16, 14, 18, 16]
        for i, w in enumerate(larguras_aut, 1):
            ws_aut.column_dimensions[get_column_letter(i)].width = w

    # --------------------------------------------------------------- Validação
    # PROVA do Passo 1: para cada SKU, o rasto de cálculo que justifica a
    # unidade de carga da sheet 'Detalhe' -- dimensões, orientação vencedora,
    # peças/plano, peças/altura, corte por peso (ou o critério de admissão
    # geométrico, para OVERSIZE). Não é redundante com a Detalhe: a escolha
    # é por SKU, não por posição, por isso 1 linha por SKU (como a
    # Autonomia) em vez de repetir o mesmo cálculo em cada rack ocupado.
    if justificacoes:
        ws_val = wb.create_sheet("Validação")
        hdr_row(ws_val, [
            "SKU", "Descrição", "Tipo de carga",
            "Comp_m", "Larg_m", "Alt_m", "Peso_kg",
            "Orientação usada", "Caixa int. Comp_m", "Caixa int. Larg_m", "Caixa int. Alt_m",
            "Peças/plano (teórico)", "Peças/plano (aplicado)", "Peças/altura",
            "Peças/peso", "Fator limitante", "Peças/unidade (final)",
            "Stock usado (un)", "Nº de unidades", "Critério aplicado",
        ])
        ws_val.freeze_panes = "A2"
        for j in justificacoes:
            ws_val.append([
                j.sku, j.descricao,
                j.load_type.value if hasattr(j.load_type, "value") else j.load_type,
                round(j.comp_m, 3), round(j.larg_m, 3), round(j.alt_m, 3), round(j.peso_kg, 2),
                j.orientacao_usada,
                round(j.caixa_int_comp_m, 3) if j.caixa_int_comp_m is not None else "-",
                round(j.caixa_int_larg_m, 3) if j.caixa_int_larg_m is not None else "-",
                round(j.caixa_int_alt_m, 3) if j.caixa_int_alt_m is not None else "-",
                j.pecas_por_plano_teorico if j.pecas_por_plano_teorico is not None else "-",
                j.pecas_por_plano if j.pecas_por_plano is not None else "-",
                j.pecas_por_altura if j.pecas_por_altura is not None else "-",
                j.pecas_por_peso if j.pecas_por_peso is not None else "-",
                j.fator_limitante, j.pecas_por_unidade, j.stock_max_un, j.num_unidades,
                j.criterio,
            ])
        larguras = [22, 30, 14, 10, 10, 10, 10, 26, 16, 16, 16, 18, 18, 14, 12, 26, 18, 16, 14, 90]
        for i, w in enumerate(larguras, 1):
            ws_val.column_dimensions[get_column_letter(i)].width = w

    # ------------------------------------------------------------------ Avisos
    if avisos:
        ws_av = wb.create_sheet("Avisos")
        hdr_row(ws_av, ["Aviso"])
        for a in avisos:
            ws_av.append([a])
        ws_av.column_dimensions["A"].width = 90
        ws_av.sheet_properties.tabColor = "C00000"

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
