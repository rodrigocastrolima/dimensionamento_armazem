"""Testes do script de import único (warehouse/import_to_supabase.py) SEM
Postgres real -- ver a mesma nota em test_db_supabase.py sobre não haver
credenciais neste ambiente. `_connect` é substituído por um par fake
connection/cursor que só REGISTA o SQL executado (não precisa de o
interpretar); `verify_import` (a parte que compara Excel vs Supabase
linha a linha) é testado à parte com dados fabricados, sem depender do
INSERT ter corrido para valer.
"""
import openpyxl
import warehouse.import_to_supabase as importer
from warehouse.models import Article, BoxType, RackType, Zona, LoadType, Empilhamento


class _RecordingCursor:
    def __init__(self, log):
        self._log = log

    def execute(self, query, params=None):
        self._log.append((query.strip().split()[0].lower(), params))
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RecordingConn:
    def __init__(self):
        self.log = []
        self.committed = False

    def cursor(self):
        return _RecordingCursor(self.log)

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _mini_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Peças"
    ws.append(["SKU", "Descrição", "Comprimento_m", "Largura_m", "Altura_m", "Peso_kg",
               "Stock_máx_un", "Peças_por_carro", "ALTURA_OVERRIDE", "EIXO_ANINHAGEM", "Empilhável",
               "Fragilidade", "Geometria", "Classe"])
    ws.append(["SIA001", "Farol", 0.4, 0.3, 0.2, 1.2, 50, 0.5, None, None,
               "EMPILHAVEL", "Normal", "Regular", "RIGIDO"])
    ws.append(["SIA002", "Painel", 1.5, 1.0, 0.2, 12.0, 10, 1.0, None, None,
               "NAO_EMPILHAVEL", "Normal", "Regular", "RIGIDO"])

    wc = wb.create_sheet("Caixas")
    wc.append(["Nome", "Int_Comp_m", "Int_Larg_m", "Int_Alt_m", "Ext_Comp_m",
               "Ext_Larg_m", "Ext_Alt_m", "Peso_max_kg"])
    wc.append(["PALLET", 1.2, 0.8, 1.5, 1.2, 0.8, 1.64, 1000])

    wr = wb.create_sheet("Racks")
    wr.append(["Nome", "Larg_m", "Prof_m", "Alt_nivel_m", "Níveis", "Aceita",
               "Footprint_m2", "Corredor_m"])
    wr.append(["Rack Porta-Paletes", 2.7, 1.2, 1.8, 4, "PALLET, L, MEIA", 3.24, 3.0])

    wz = wb.create_sheet("Zonas")
    wz.append(["Nome", "Categoria", "Comp_m", "Larg_m", "Justificacao"])
    wz.append(["Receção", "Receção", 5.4, 5.5, "24 paletes diárias"])

    wb.save(path)


def test_import_client_issues_one_insert_per_row(tmp_path, monkeypatch):
    path = tmp_path / "mini_master_data.xlsx"
    _mini_workbook(path)

    conn = _RecordingConn()
    monkeypatch.setattr(importer, "_connect", lambda: conn)
    # isola o INSERT da re-leitura: verify_import não é o alvo deste teste
    monkeypatch.setattr(importer, "verify_import", lambda *a, **k: [])

    importer.import_client("sia_test", "SIA (teste)", path)

    kinds = [k for k, _ in conn.log]
    assert kinds.count("insert") == (
        1          # clients upsert
        + 2        # articles (SIA001, SIA002)
        + 1        # box_catalog (PALLET)
        + 1        # rack_types (Rack Porta-Paletes)
        + 1        # zones (Receção)
    )
    assert kinds.count("delete") == 4   # articles/box_catalog/rack_types/zones, uma vez cada
    assert conn.committed


def test_verify_import_passes_when_data_matches(monkeypatch):
    a = Article(sku="X", descricao="", comp_m=1, larg_m=1, alt_m=1, peso_kg=1,
                stock_max_un=1, pecas_por_carro=1.0, empilhamento=Empilhamento.EMPILHAVEL)
    b = BoxType(nome=LoadType.PALLET, int_comp_m=1, int_larg_m=1, int_alt_m=1,
                ext_comp_m=1, ext_larg_m=1, ext_alt_m=1, peso_max_kg=1)
    r = RackType(nome="R", larg_m=1, prof_m=1, alt_nivel_m=1, niveis=1)
    z = Zona(nome="Z", categoria="Outra", comp_m=1, larg_m=1)

    import warehouse.db_supabase as db_supabase
    monkeypatch.setattr(db_supabase, "load_articles", lambda slug: [a])
    monkeypatch.setattr(db_supabase, "load_catalog", lambda slug: [b])
    monkeypatch.setattr(db_supabase, "load_rack_types", lambda slug: [r])
    monkeypatch.setattr(db_supabase, "load_zones", lambda slug: [z])

    problemas = importer.verify_import("sia_test", [a], [b], [r], [z])
    assert problemas == []


def test_verify_import_detects_mismatch(monkeypatch):
    a = Article(sku="X", descricao="", comp_m=1, larg_m=1, alt_m=1, peso_kg=1,
                stock_max_un=1, pecas_por_carro=1.0, empilhamento=Empilhamento.EMPILHAVEL)
    a_errada = Article(sku="X", descricao="", comp_m=1, larg_m=1, alt_m=1, peso_kg=1,
                       stock_max_un=999, pecas_por_carro=1.0,   # valor diferente!
                       empilhamento=Empilhamento.EMPILHAVEL)

    import warehouse.db_supabase as db_supabase
    monkeypatch.setattr(db_supabase, "load_articles", lambda slug: [a_errada])
    monkeypatch.setattr(db_supabase, "load_catalog", lambda slug: [])
    monkeypatch.setattr(db_supabase, "load_rack_types", lambda slug: [])
    monkeypatch.setattr(db_supabase, "load_zones", lambda slug: [])

    problemas = importer.verify_import("sia_test", [a], [], [], [])
    assert len(problemas) == 1 and "articles" in problemas[0] and "'X'" in problemas[0]


def test_verify_import_detects_missing_row(monkeypatch):
    a = Article(sku="X", descricao="", comp_m=1, larg_m=1, alt_m=1, peso_kg=1,
                stock_max_un=1, pecas_por_carro=1.0, empilhamento=Empilhamento.EMPILHAVEL)

    import warehouse.db_supabase as db_supabase
    monkeypatch.setattr(db_supabase, "load_articles", lambda slug: [])   # linha perdida
    monkeypatch.setattr(db_supabase, "load_catalog", lambda slug: [])
    monkeypatch.setattr(db_supabase, "load_rack_types", lambda slug: [])
    monkeypatch.setattr(db_supabase, "load_zones", lambda slug: [])

    problemas = importer.verify_import("sia_test", [a], [], [], [])
    assert len(problemas) == 1 and "1 no Excel vs 0 no Supabase" in problemas[0]
