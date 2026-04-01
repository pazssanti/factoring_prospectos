# =============================================================
#  transform/limpiar_licitaciones.py — Versión 2
#
#  CORRECCIONES:
#  - Deduplica licitaciones por codigoexterno antes de contar
#    (el CSV tiene una fila por ítem, no por licitación)
#  - Deduplica OC por codigo antes de sumar montos
#  - Esto corrige los porcentajes >100% y montos inflados
#
#  Genera: clean_licitaciones y clean_ordenes
# =============================================================

import sys
import sqlite3
import pandas as pd
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import DB_PATH
from utils.helpers import normalizar_rut_serie


# ─────────────────────────────────────────────────────────────
# LIMPIAR LICITACIONES
# ─────────────────────────────────────────────────────────────

def limpiar_licitaciones(conn: sqlite3.Connection) -> pd.DataFrame:
    print("\n[1/2] Limpiando licitaciones...")

    df_raw = pd.read_sql(
        "SELECT * FROM raw_licitaciones_csv", conn
    )
    print(f"  Filas raw (con duplicados por ítem): {len(df_raw):,}")

    # ── CORRECCIÓN CLAVE: deduplicar por licitación única ────
    # El CSV tiene N filas por licitación (una por ítem/oferta).
    # Para contar licitaciones y montos correctamente,
    # necesitamos una fila por licitación.
    #
    # Estrategia:
    #   - Para datos de la licitación: tomar la primera fila
    #   - Para monto adjudicado: sumar todas las líneas
    #     (montolineaadjudica es el monto de cada ítem)

    # Primero calcular monto total adjudicado por licitación
    # sumando todas las líneas
    df_raw["montolineaadjudica_num"] = pd.to_numeric(
        df_raw["montolineaadjudica"]
        .astype(str)
        .str.replace(",", ".", regex=False)
        .str.strip(),
        errors="coerce"
    )

    monto_por_lit = df_raw.groupby("codigoexterno").agg(
        monto_total_adjudicado=("montolineaadjudica_num", "sum"),
        n_items=("codigoexterno", "count"),
        n_oferentes_distintos=("codigoproveedor", "nunique"),
    ).reset_index()

    # Ahora deduplicar: una fila por licitación
    # Priorizar filas donde oferta_seleccionada = 'Seleccionada'
    df_adj = df_raw[
        df_raw["oferta_seleccionada"].astype(str)
        .str.contains("Seleccionada", case=False, na=False)
    ].drop_duplicates("codigoexterno")

    # Filas sin adjudicación seleccionada → primera fila
    df_no_adj = df_raw[
        ~df_raw["codigoexterno"].isin(df_adj["codigoexterno"])
    ].drop_duplicates("codigoexterno")

    df_dedup = pd.concat([df_adj, df_no_adj], ignore_index=True)
    print(f"  Licitaciones únicas después de deduplicar: "
          f"{len(df_dedup):,}")

    # Unir con montos calculados
    df_dedup = df_dedup.merge(
        monto_por_lit, on="codigoexterno", how="left"
    )

    # ── Seleccionar columnas útiles ───────────────────────────
    COLS = [
        "codigoexterno", "nombre", "estado", "codigoestado",
        "nombreorganismo", "codigoorganismo",
        "regionunidad", "comunaunidad",
        "fechaadjudicacion", "fechacreacion", "fechacierre",
        "montoestimado",
        "monto_total_adjudicado",  # calculado correctamente
        "n_items",
        "rutproveedor", "nombreproveedor", "razonsocialproveedor",
        "codigoproveedor",
        "rubro1", "rubro2", "rubro3",
        "nombre_producto_genrico",
        "numerooferentes", "tipo", "anio_mes",
    ]
    COLS = [c for c in COLS if c in df_dedup.columns]
    df = df_dedup[COLS].copy()

    # ── Normalizar RUT ────────────────────────────────────────
    df["rut_proveedor_norm"] = normalizar_rut_serie(df["rutproveedor"])

    # ── Limpiar montoestimado ─────────────────────────────────
    df["montoestimado"] = pd.to_numeric(
        df["montoestimado"].astype(str)
        .str.replace(",", ".", regex=False)
        .str.strip(),
        errors="coerce"
    )

    # ── Limpiar fechas ────────────────────────────────────────
    for col in ["fechaadjudicacion", "fechacreacion", "fechacierre"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # ── Flag adjudicada ───────────────────────────────────────
    df["es_adjudicada"] = df["estado"].astype(str).str.contains(
        "djudicad", case=False, na=False
    ).astype(int)

    # ── Numerooferentes ───────────────────────────────────────
    df["numerooferentes"] = pd.to_numeric(
        df["numerooferentes"], errors="coerce"
    ).fillna(0).astype(int)

    # ── Año ───────────────────────────────────────────────────
    df["anio_adjudicacion"] = pd.to_datetime(
        df["fechaadjudicacion"], errors="coerce"
    ).dt.year

    print(f"  Adjudicadas: {df['es_adjudicada'].sum():,}")
    print(f"  Proveedores únicos: "
          f"{df['rut_proveedor_norm'].nunique():,}")

    return df


# ─────────────────────────────────────────────────────────────
# LIMPIAR ÓRDENES DE COMPRA
# ─────────────────────────────────────────────────────────────

def limpiar_ordenes(conn: sqlite3.Connection) -> pd.DataFrame:
    print("\n[2/2] Limpiando órdenes de compra...")

    df_raw = pd.read_sql(
        "SELECT * FROM raw_ordenes_csv", conn
    )
    print(f"  Filas raw (con duplicados por ítem): {len(df_raw):,}")

    # ── CORRECCIÓN CLAVE: deduplicar OC ──────────────────────
    # El CSV tiene N filas por OC (una por ítem).
    # El monto real de la OC está en montototaloc_pesoschilenos
    # que se repite igual en todas las filas de la misma OC.
    # Solo hay que deduplicar por codigo de OC.

    # Convertir monto a numérico primero
    df_raw["monto_num"] = pd.to_numeric(
        df_raw["montototaloc_pesoschilenos"].astype(str)
        .str.replace(",", ".", regex=False)
        .str.strip(),
        errors="coerce"
    )
    df_raw["neto_num"] = pd.to_numeric(
        df_raw["totalnetooc"].astype(str)
        .str.replace(",", ".", regex=False)
        .str.strip(),
        errors="coerce"
    )

    # Deduplicar: una fila por OC (el monto ya viene a nivel OC)
    df = df_raw.drop_duplicates("codigo").copy()
    print(f"  OC únicas después de deduplicar: {len(df):,}")

    # ── Seleccionar columnas útiles ───────────────────────────
    COLS = [
        "codigo", "codigolicitacion", "nombre",
        "estado", "estadoproveedor",
        "organismopublico", "codigoorganismopublico",
        "regionunidadcompra", "ciudadunidadcompra",
        "rutsucursal", "nombreproveedor",
        "regionproveedor", "comunaproveedor",
        "monto_num", "neto_num",
        "fechaenvio", "fechaaceptacion",
        "rubron1", "rubron2", "tipo", "anio_mes",
    ]
    COLS = [c for c in COLS if c in df.columns]
    df = df[COLS].copy()

    # Renombrar montos
    df = df.rename(columns={
        "monto_num": "monto_oc_clp",
        "neto_num":  "neto_oc_clp",
    })

    # ── Normalizar RUT proveedor ──────────────────────────────
    df["rut_proveedor_norm"] = normalizar_rut_serie(df["rutsucursal"])

    # ── Limpiar fechas ────────────────────────────────────────
    for col in ["fechaenvio", "fechaaceptacion"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # ── Flags ────────────────────────────────────────────────
    df["es_aceptada"] = df["estado"].astype(str).str.contains(
        "ceptad", case=False, na=False
    ).astype(int)

    df["anio_oc"] = df["fechaaceptacion"].dt.year

    print(f"  OC aceptadas: {df['es_aceptada'].sum():,}")
    print(f"  Proveedores únicos: "
          f"{df['rut_proveedor_norm'].nunique():,}")
    print(f"  Monto total OC (CLP): "
          f"${df['monto_oc_clp'].sum():,.0f}")

    return df


# ─────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────

def run():
    print("=" * 55)
    print("transform/limpiar_licitaciones.py — Versión 2")
    print("Con deduplicación por licitación y OC únicos")
    print("=" * 55)

    conn = sqlite3.connect(DB_PATH)

    df_lit = limpiar_licitaciones(conn)
    df_lit.to_sql("clean_licitaciones", conn,
                  if_exists="replace", index=False)
    conn.commit()
    print(f"\n  clean_licitaciones: {len(df_lit):,} registros")

    df_oc = limpiar_ordenes(conn)
    df_oc.to_sql("clean_ordenes", conn,
                 if_exists="replace", index=False)
    conn.commit()
    print(f"  clean_ordenes: {len(df_oc):,} registros")

    conn.close()
    print("\ntransform/limpiar_licitaciones.py completado.")


if __name__ == "__main__":
    run()