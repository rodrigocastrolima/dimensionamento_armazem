"""Testes do backend Supabase da camada de acesso a dados (db_supabase.py)
SEM ligação real a Postgres nenhuma -- não há projeto/credenciais neste
ambiente (ver NEEDS_HUMAN.md). `db_supabase._connect` é substituído por um
par fake connection/cursor que devolve linhas fabricadas, para validar só a
lógica Python de mapear linha -> dataclass (a mesma responsabilidade que
io_excel.py tem para o Excel): reaproveita dimensao_segura, mesmos avisos
para tokens desconhecidos, mesmos valores por omissão.

Quando houver credenciais reais (ver tools/import_to_supabase.py), a
verificação end-to-end é o próprio import script: lê o Excel, escreve no
Supabase, relê pelo db_supabase e compara contagens/valores linha a linha.
"""
import warehouse.db_supabase as db_supabase
from warehouse.models import LoadType, Empilhamento


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.last_query = None
        self.last_params = None

    def execute(self, query, params=None):
        self.last_query = query
        self.last_params = params
        return self

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_connect(monkeypatch, rows):
    monkeypatch.setattr(db_supabase, "_connect", lambda: _FakeConn(rows))


def test_load_articles_maps_row_to_dataclass(monkeypatch):
    rows = [{
        "sku": "SIA001", "descricao": "Farol dianteiro",
        "comp_m": 0.4, "larg_m": 0.3, "alt_m": 0.2, "peso_kg": 1.2,
        "stock_max_un_bruto": 50, "pecas_por_carro": 0.5,
        "altura_override": "", "eixo_aninhagem": "", "caixa_forcada": "",
        "pecas_caixa_forcada": 0, "empilhamento": "EMPILHAVEL",
        "fragilidade": "Normal", "geometria": "Regular", "classe": "RIGIDO",
    }]
    _patch_connect(monkeypatch, rows)
    articles = db_supabase.load_articles("sia")
    assert len(articles) == 1
    a = articles[0]
    assert a.sku == "SIA001"
    assert a.pecas_por_carro == 0.5           # fracionário preservado, sem truncar
    assert a.stock_max_un == 50 == a.stock_atual_un   # placeholder, igual a load_articles do Excel
    assert a.empilhamento == Empilhamento.EMPILHAVEL


def test_load_articles_sem_geometria_usa_fallback_e_avisa(monkeypatch, capsys):
    rows = [{
        "sku": "SIA002", "descricao": "", "comp_m": 0, "larg_m": 0, "alt_m": 0,
        "peso_kg": 0, "stock_max_un_bruto": 1, "pecas_por_carro": 1.0,
        "altura_override": "", "eixo_aninhagem": "", "caixa_forcada": "",
        "pecas_caixa_forcada": 0, "empilhamento": "EMPILHAVEL",
        "fragilidade": "Normal", "geometria": "Regular", "classe": "RIGIDO",
    }]
    _patch_connect(monkeypatch, rows)
    articles = db_supabase.load_articles("sia")
    assert (articles[0].comp_m, articles[0].larg_m, articles[0].alt_m) == (0.05, 0.05, 0.01)
    out = capsys.readouterr().out
    assert "AVISO" in out and "SIA002" in out


def test_load_catalog_maps_row_to_dataclass(monkeypatch):
    rows = [{
        "nome": "PALLET", "int_comp_m": 1.2, "int_larg_m": 0.8, "int_alt_m": 1.5,
        "ext_comp_m": 1.2, "ext_larg_m": 0.8, "ext_alt_m": 1.64, "peso_max_kg": 1000.0,
    }]
    _patch_connect(monkeypatch, rows)
    catalog = db_supabase.load_catalog("sia")
    assert catalog[0].nome == LoadType.PALLET
    assert catalog[0].peso_max_kg == 1000.0


def test_load_rack_types_native_array_no_csv_parsing(monkeypatch):
    rows = [{
        "nome": "Rack Porta-Paletes", "larg_m": 2.7, "prof_m": 1.2, "alt_nivel_m": 1.8,
        "niveis": 4, "aceita": ["PALLET", "L", "MEIA"], "footprint_m2": 3.24, "corredor_m": 3.0,
    }]
    _patch_connect(monkeypatch, rows)
    racks = db_supabase.load_rack_types("sia")
    assert [lt.value for lt in racks[0].aceita] == ["PALLET", "L", "MEIA"]


def test_load_rack_types_unknown_token_warns_like_excel_loader(monkeypatch, capsys):
    rows = [{
        "nome": "Rack Cantilever", "larg_m": 3.0, "prof_m": 0.6, "alt_nivel_m": 0.6,
        "niveis": 4, "aceita": ["LONGO"], "footprint_m2": 1.8, "corredor_m": 3.0,
    }]
    _patch_connect(monkeypatch, rows)
    racks = db_supabase.load_rack_types("sia")
    assert racks[0].aceita == []
    out = capsys.readouterr().out
    assert "AVISO" in out and "LONGO" in out and "VAZIO" in out


def test_load_zones_maps_row_to_dataclass(monkeypatch):
    rows = [{
        "nome": "Receção", "categoria": "Receção", "comp_m": 5.4, "larg_m": 5.5,
        "justificacao": "24 paletes diárias",
    }]
    _patch_connect(monkeypatch, rows)
    zonas = db_supabase.load_zones("sia")
    assert zonas[0].nome == "Receção" and zonas[0].area_m2 == 5.4 * 5.5


def test_connect_without_database_url_raises_clear_error(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    try:
        db_supabase._connect()
        assert False, "should have raised"
    except RuntimeError as e:
        assert "DATABASE_URL" in str(e) and "NEEDS_HUMAN" in str(e)
