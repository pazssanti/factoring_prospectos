# =============================================================
#  transform/calcular_plazos_pago.py
#
#  Propósito: calcular la velocidad de pago de cada organismo
#  público de Los Lagos, usando como proxy los días entre la
#  fecha de adjudicación de la licitación y la fecha de
#  aceptación de la OC por el proveedor.
#
#  Por qué importa para factoring: una empresa que provee a
#  organismos que tardan 90 días en emitir la OC necesita
#  factoring más que una que recibe la OC en 15 días.
#
#  Inputs:  clean_ordenes, clean_licitaciones
#  Outputs: plazos_pago_organismos (tabla DB)
#           — codigoorganismo, nombre_organismo, n_oc,
#             monto_total_clp, dias_promedio, dias_mediana,
#             score_velocidad_pago (0-100), categoria
# =============================================================

import sys
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from config import DB_PATH


def score_velocidad(dias: float) -> float:
    """
    Convierte días promedio adj→OC a score 0-100.
    Más rápido = score más alto = organismo más atractivo para factoring.

    Escala:
      <= 7 días  → 100  (muy rápido)
      <= 15 días → 85
      <= 30 días → 70   (normal, 30 días es el estándar legal)
      <= 60 días → 45
      <= 90 días → 25
      > 90 días  → 10   (muy lento, alto costo de carry)
    """
    if pd.isna(dias) or dias <= 0:
        return 50.0   # sin dato — neutral
    if dias <= 7:
        return 100.0
    if dias <= 15:
        return 85.0
    if dias <= 30:
        return 70.0
    if dias <= 60:
        return 45.0
    if dias <= 90:
        return 25.0
    return 10.0


def categoria_velocidad(dias: float) -> str:
    if pd.isna(dias) or dias <= 0:
        return "SIN DATO"
    if dias <= 20:
        return "RAPIDO"
    if dias <= 50:
        return "NORMAL"
    return "LENTO"


def run():
    print("=" * 55)
    print("transform/calcular_plazos_pago.py")
    print("Calculando velocidad de pago por organismo")
    print("=" * 55)

    conn = sqlite3.connect(DB_PATH)

    # ── Cruzar OC con licitaciones para obtener días adj→OC ───
    try:
        df = pd.read_sql("""
            SELECT
                o.codigoorganismopublico  AS codigoorganismo,
                o.nombreorganismo         AS nombre_organismo,
                o.regionunidadcompra      AS region_organismo,
                o.monto_oc_clp            AS monto,
                CAST(
                    julianday(o.fechaaceptacion)
                    - julianday(l.fechaadjudicacion)
                AS REAL) AS dias_adj_oc
            FROM clean_ordenes o
            JOIN clean_licitaciones l
                ON o.codigolicitacion = l.codigoexterno
            WHERE o.es_aceptada = 1
              AND o.fechaaceptacion IS NOT NULL
              AND l.fechaadjudicacion IS NOT NULL
              AND o.codigoorganismopublico IS NOT NULL
              AND o.codigoorganismopublico != ''
        """, conn)
    except Exception as exc:
        print(f"  Error al cargar datos: {exc}")
        conn.close()
        return

    print(f"  Registros OC con licitación cruzada: {len(df):,}")

    if df.empty:
        print("  Sin datos para calcular plazos — abortando")
        conn.close()
        return

    # Filtrar días razonables (0-365 días, eliminar outliers extremos)
    df_validos = df[
        (df["dias_adj_oc"] >= 0) & (df["dias_adj_oc"] <= 365)
    ].copy()
    print(f"  Registros con días válidos (0-365): {len(df_validos):,}")

    # ── Agrupar por organismo ─────────────────────────────────
    agg = df_validos.groupby("codigoorganismo").agg(
        nombre_organismo  = ("nombre_organismo",  "first"),
        region_organismo  = ("region_organismo",  "first"),
        n_oc              = ("monto",              "count"),
        monto_total_clp   = ("monto",              "sum"),
        dias_promedio     = ("dias_adj_oc",         "mean"),
        dias_mediana      = ("dias_adj_oc",         "median"),
        dias_min          = ("dias_adj_oc",         "min"),
        dias_max          = ("dias_adj_oc",         "max"),
    ).reset_index()

    # Solo organismos con al menos 3 OC (estadística más confiable)
    agg = agg[agg["n_oc"] >= 3].copy()
    print(f"  Organismos con >= 3 OC: {len(agg):,}")

    # ── Score y categoría ─────────────────────────────────────
    agg["dias_promedio"] = agg["dias_promedio"].round(1)
    agg["dias_mediana"]  = agg["dias_mediana"].round(1)
    agg["score_velocidad_pago"] = (
        agg["dias_mediana"].apply(score_velocidad).round(1)
    )
    agg["categoria"] = agg["dias_mediana"].apply(categoria_velocidad)

    # ── Ordenar por velocidad descendente ─────────────────────
    agg = agg.sort_values("score_velocidad_pago", ascending=False)

    # ── Resumen ───────────────────────────────────────────────
    cat_counts = agg["categoria"].value_counts()
    print("\n  Distribución por categoría:")
    for cat, cnt in cat_counts.items():
        print(f"    {cat}: {cnt} organismos")

    top5 = agg.head(5)[["nombre_organismo", "dias_mediana", "categoria"]]
    print("\n  TOP 5 más rápidos:")
    for _, row in top5.iterrows():
        print(f"    {str(row['nombre_organismo'])[:45]:45} "
              f"{row['dias_mediana']:5.0f} días  {row['categoria']}")

    # ── Guardar ───────────────────────────────────────────────
    agg.to_sql("plazos_pago_organismos", conn,
               if_exists="replace", index=False)
    conn.commit()

    n = conn.execute(
        "SELECT COUNT(*) FROM plazos_pago_organismos"
    ).fetchone()[0]
    print(f"\n  plazos_pago_organismos: {n:,} organismos")

    conn.close()
    print("\ntransform/calcular_plazos_pago.py completado.")


if __name__ == "__main__":
    run()
