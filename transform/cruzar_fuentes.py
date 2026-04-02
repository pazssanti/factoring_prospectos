# =============================================================
#  transform/cruzar_fuentes.py — Versión 2
#
#  NUEVO: filtra empresas no vigentes (cerradas feb 2026)
#  antes del cruce. Esto elimina empresas fantasma del ranking.
#
#  Genera: clean_proveedores
# =============================================================

import sys
import sqlite3
import pandas as pd
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import DB_PATH
from utils.helpers import normalizar_rut_serie


def run():
    print("=" * 55)
    print("transform/cruzar_fuentes.py — Versión 2")
    print("Cruzando SII + Mercado Público + filtro vigencia")
    print("=" * 55)

    conn = sqlite3.connect(DB_PATH)

    # ── Cargar tablas ─────────────────────────────────────────
    print("\nCargando tablas...")
    df_lit = pd.read_sql("SELECT * FROM clean_licitaciones", conn)
    df_oc  = pd.read_sql("SELECT * FROM clean_ordenes", conn)
    df_sii = pd.read_sql("SELECT * FROM raw_empresas_sii", conn)
    print(f"  clean_licitaciones: {len(df_lit):,}")
    print(f"  clean_ordenes:      {len(df_oc):,}")
    print(f"  raw_empresas_sii:   {len(df_sii):,}")

    # ── Filtrar SII: solo Los Lagos ───────────────────────────
    df_sii_lagos = df_sii[
        df_sii["region"].str.contains("LAGOS", case=False, na=False)
    ].copy()
    print(f"\n  SII Los Lagos (total):   {len(df_sii_lagos):,}")

    # ── NUEVO: filtrar solo empresas vigentes feb 2026 ────────
    if "vigente_2026" in df_sii_lagos.columns:
        df_vigentes = df_sii_lagos[
            df_sii_lagos["vigente_2026"] == 1
        ].copy()
        n_excluidas = len(df_sii_lagos) - len(df_vigentes)
        print(f"  Excluidas (cerradas):    {n_excluidas:,}")
        print(f"  Vigentes feb 2026:       {len(df_vigentes):,}")
        df_sii_lagos = df_vigentes
    else:
        print("  AVISO: columna vigente_2026 no encontrada")
        print("  → Ejecuta ingesta/sii_nomina.py primero")

    # ── Historial licitaciones por proveedor (CORREGIDO) ──────
    print("\nCalculando historial licitaciones...")

    # Solo contar licitaciones ÚNICAS adjudicadas
    lit_adj = df_lit[df_lit["es_adjudicada"] == 1].copy()

    hist_lit = lit_adj.groupby("rut_proveedor_norm").agg(
        licitaciones_ganadas   = ("codigoexterno", "nunique"),
        monto_total_adjudicado = ("monto_total_adjudicado", "sum"),
        monto_promedio_lit     = ("monto_total_adjudicado", "mean"),
        primera_licitacion     = ("fechaadjudicacion", "min"),
        ultima_licitacion      = ("fechaadjudicacion", "max"),
        rubro_frecuente        = ("rubro1",
                                  lambda x: x.mode().iloc[0]
                                  if len(x.mode()) > 0 else ""),
        organismos_distintos   = ("codigoorganismo", "nunique"),
    ).reset_index()

    # Total licitaciones donde participó (ganadas o no)
    # en el CSV histórico, todas son adjudicadas por el filtro
    # de ingesta, así que total = ganadas en este caso
    hist_lit["total_licitaciones"] = hist_lit["licitaciones_ganadas"]
    # NOTA: los CSV de datos.chilecompra.cl contienen SOLO licitaciones
    # adjudicadas (el Estado publica el resultado, no las participaciones
    # sin éxito). Por eso este campo siempre vale 100% — es un artefacto
    # de la fuente, no la tasa de éxito real del proveedor.
    # La tasa real se calcula desde OCDS en f_tasa_adjudicacion (scoring).
    hist_lit["pct_adjudicacion_csv"] = 100.0

    # ── Historial OC por proveedor (CORREGIDO) ────────────────
    print("Calculando historial órdenes de compra...")

    oc_acep = df_oc[df_oc["es_aceptada"] == 1].copy()

    # Volumen últimos 30 días (señal clave para Convenio Marco)
    oc_acep["_fecha_dt"] = pd.to_datetime(
        oc_acep["fechaaceptacion"], errors="coerce"
    )
    _hoy   = pd.Timestamp("today")
    _30d   = _hoy - pd.Timedelta(days=30)
    _365d  = _hoy - pd.Timedelta(days=365)
    oc_30d = oc_acep[oc_acep["_fecha_dt"] >= _30d]
    oc_12m = oc_acep[oc_acep["_fecha_dt"] >= _365d]

    hist_oc_30d = oc_30d.groupby("rut_proveedor_norm").agg(
        oc_30d_count = ("codigo",       "nunique"),
        oc_30d_monto = ("monto_oc_clp", "sum"),
    ).reset_index()

    hist_oc_12m = oc_12m.groupby("rut_proveedor_norm").agg(
        oc_12m_count = ("codigo",       "nunique"),
        oc_12m_monto = ("monto_oc_clp", "sum"),
    ).reset_index()

    hist_oc = oc_acep.groupby("rut_proveedor_norm").agg(
        total_oc        = ("codigo",                 "nunique"),
        monto_total_oc  = ("monto_oc_clp",           "sum"),
        monto_prom_oc   = ("monto_oc_clp",           "mean"),
        primera_oc      = ("fechaaceptacion",        "min"),
        ultima_oc       = ("fechaaceptacion",        "max"),
        organismos_oc   = ("codigoorganismopublico", "nunique"),
    ).reset_index()

    hist_oc = (hist_oc
               .merge(hist_oc_30d, on="rut_proveedor_norm", how="left")
               .merge(hist_oc_12m, on="rut_proveedor_norm", how="left"))
    hist_oc[["oc_30d_count","oc_30d_monto",
             "oc_12m_count","oc_12m_monto"]] = (
        hist_oc[["oc_30d_count","oc_30d_monto",
                 "oc_12m_count","oc_12m_monto"]].fillna(0)
    )

    print(f"  Proveedores con licitaciones: "
          f"{len(hist_lit):,}")
    print(f"  Proveedores con OC:           "
          f"{len(hist_oc):,}")

    # ── Combinar historiales ──────────────────────────────────
    hist_mp = pd.merge(
        hist_lit, hist_oc,
        on="rut_proveedor_norm", how="outer"
    ).fillna(0)

    # ── Ratio OC / licitación ─────────────────────────────────
    # Ratio alto (>8) → Convenio Marco o suministro periódico.
    # Ratio bajo (≤2) → licitación tradicional.
    # Este dato cambia radicalmente la estrategia de prospección.
    hist_mp["ratio_oc_licitacion"] = (
        hist_mp["total_oc"]
        / hist_mp["licitaciones_ganadas"].clip(lower=1)
    ).round(1)

    # ── % OC de Convenio Marco ────────────────────────────────
    # Cuántas OC de la empresa vienen de un Convenio Marco.
    # Fuente: join clean_ordenes × clean_licitaciones por tipo.
    # LIMITACIÓN: solo refleja las licitaciones históricas del CSV.
    # Las OC de CM puro (sin licitación previa en nuestra DB) no
    # quedan capturadas aquí; el ratio alto las detecta igual.
    try:
        df_cm = pd.read_sql("""
            SELECT o.rut_proveedor_norm,
                   COUNT(DISTINCT o.codigo) AS n_oc_cm
            FROM   clean_ordenes o
            JOIN   clean_licitaciones l
                ON o.codigolicitacion = l.codigoexterno
            WHERE  (l.tipo LIKE '%Convenio%'
                    OR LOWER(l.nombre) LIKE '%convenio marco%')
              AND  o.es_aceptada = 1
            GROUP BY o.rut_proveedor_norm
        """, conn)
        hist_mp = hist_mp.merge(df_cm, on="rut_proveedor_norm", how="left")
        hist_mp["n_oc_cm"] = hist_mp["n_oc_cm"].fillna(0)
        hist_mp["pct_oc_convenio_marco"] = (
            hist_mp["n_oc_cm"]
            / hist_mp["total_oc"].clip(lower=1) * 100
        ).round(1)
        print(f"  Proveedores con OC de CM: "
              f"{(hist_mp['n_oc_cm'] > 0).sum():,}")
    except Exception as exc:
        hist_mp["n_oc_cm"] = 0
        hist_mp["pct_oc_convenio_marco"] = 0.0
        print(f"  AVISO pct_oc_convenio_marco: {exc}")

    # ── Tipo de relación empresa-Estado ──────────────────────
    # Etiqueta legible para el equipo comercial.
    def _tipo_relacion(row):
        pct_cm = float(row.get("pct_oc_convenio_marco", 0) or 0)
        ratio  = float(row.get("ratio_oc_licitacion",   0) or 0)
        if pct_cm >= 50 or ratio >= 8:
            return "CONVENIO MARCO"
        if ratio >= 3:
            return "SUMINISTRO"
        if row.get("total_oc", 0) > 0:
            return "LICITACION"
        return "SIN OC"

    hist_mp["tipo_relacion_estado"] = hist_mp.apply(_tipo_relacion, axis=1)

    # ── Cruzar con SII Los Lagos vigentes ────────────────────
    print("Cruzando con SII...")

    df_sii_lagos["rut_norm_cruce"] = normalizar_rut_serie(
        df_sii_lagos["rut_normalizado"]
    )

    df_resultado = pd.merge(
        df_sii_lagos,
        hist_mp,
        left_on="rut_norm_cruce",
        right_on="rut_proveedor_norm",
        how="left"
    )

    # ── Flags ─────────────────────────────────────────────────
    df_resultado["aparece_en_mp"] = (
        df_resultado["licitaciones_ganadas"].fillna(0) > 0
    ).astype(int)

    df_resultado["aparece_en_oc"] = (
        df_resultado["total_oc"].fillna(0) > 0
    ).astype(int)

    # Rellenar NaN en columnas numéricas
    cols_num = [
        "licitaciones_ganadas", "total_licitaciones",
        "monto_total_adjudicado", "pct_adjudicacion_csv",
        "total_oc", "monto_total_oc", "monto_prom_oc",
        "organismos_distintos", "organismos_oc",
        "ratio_oc_licitacion", "n_oc_cm", "pct_oc_convenio_marco",
        "oc_30d_count", "oc_30d_monto", "oc_12m_count", "oc_12m_monto",
    ]
    for col in cols_num:
        if col in df_resultado.columns:
            df_resultado[col] = df_resultado[col].fillna(0)

    # ── Estadísticas ──────────────────────────────────────────
    n_total  = len(df_resultado)
    n_con_mp = df_resultado["aparece_en_mp"].sum()
    n_con_oc = df_resultado["aparece_en_oc"].sum()

    print(f"\n  Resultado del cruce:")
    print(f"  Empresas vigentes Los Lagos: {n_total:,}")
    print(f"  Con licitaciones ganadas:    {n_con_mp:,}")
    print(f"  Con órdenes de compra:       {n_con_oc:,}")
    print(f"  Solo SII (sin Estado):       "
          f"{n_total - n_con_mp:,}")

    # Monto total OC en millones para referencia
    monto_mm = df_resultado["monto_total_oc"].sum() / 1_000_000
    print(f"  Monto total OC (MM CLP):     ${monto_mm:,.1f}M")

    # ── Guardar ───────────────────────────────────────────────
    df_resultado.to_sql(
        "clean_proveedores", conn,
        if_exists="replace", index=False
    )
    conn.commit()

    n = conn.execute(
        "SELECT COUNT(*) FROM clean_proveedores"
    ).fetchone()[0]
    print(f"\n  clean_proveedores: {n:,} registros")

    conn.close()
    print("\ntransform/cruzar_fuentes.py completado.")


if __name__ == "__main__":
    run()