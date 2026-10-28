"""Camada de acesso a dados: Excel (io_excel.py) ou Supabase (db_supabase.py),
escolhido por `Config.data_source` -- nunca por qual módulo se importa.

Todo o resto do pipeline (boxing/racking/area/layout) continua a receber
exatamente os mesmos dataclasses (Article/BoxType/RackType/Zona)
independentemente da origem; main.py/web.py/cenario_autonomia.py chamam
estas quatro funções em vez de io_excel.load_* diretamente, para poderem
mudar de origem sem tocar em código nenhum -- só no `dados/clients/<nome>.json`
do cliente (campo `data_source`).

`db_supabase` só é importado quando realmente preciso (data_source ==
"supabase"): um cliente que continua no Excel nunca precisa de `psycopg`
instalado.
"""
from __future__ import annotations

from dataclasses import replace

from .config import Config
from .models import Article, BoxType, RackType, Zona
from . import io_excel


def load_articles(cfg: Config) -> list[Article]:
    if cfg.data_source == "supabase":
        from . import db_supabase
        return db_supabase.load_articles(cfg.client_name)
    return io_excel.load_articles(cfg.master_data)


def load_catalog(cfg: Config) -> list[BoxType]:
    if cfg.data_source == "supabase":
        from . import db_supabase
        return db_supabase.load_catalog(cfg.client_name)
    return io_excel.load_catalog(cfg.master_data)


def aplicar_pilares(racks: list[RackType], cfg: Config) -> list[RackType]:
    """Preenche `RackType.pilar_m` a partir de `Config.pilar_rack_m` (chave =
    substring do nome, case-insensitive, a primeira que casar ganha)."""
    if not cfg.pilar_rack_m:
        return racks
    saida = []
    for r in racks:
        nome = (r.nome or "").lower()
        pilar = next((float(v) for k, v in cfg.pilar_rack_m.items() if k.lower() in nome), 0.0)
        saida.append(replace(r, pilar_m=pilar) if pilar else r)
    return saida


def load_rack_types(cfg: Config) -> list[RackType]:
    if cfg.data_source == "supabase":
        from . import db_supabase
        racks = db_supabase.load_rack_types(cfg.client_name)
    else:
        racks = io_excel.load_rack_types(cfg.master_data)
    return aplicar_pilares(racks, cfg)


def load_zones(cfg: Config) -> list[Zona]:
    if cfg.data_source == "supabase":
        from . import db_supabase
        return db_supabase.load_zones(cfg.client_name)
    return io_excel.load_zones(cfg.master_data)
