# =============================================================
#  tests/consulta_prospectos.py
#
#  Aplica filtros inteligentes sobre prospectos_rankeados
#  para reducir las 1,014 empresas a los ~50-60 más calientes.
#  Corre en terminal separada mientras ocds_oferentes corre.
# =============================================================

import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path("data/factoring_prospeccion.db")
conn = sqlite3.connect(DB_PATH)

print("=" * 60)
print("FILTRO 1 — Prospectos ideales (nuevos, creciendo, con liquidez)")
print("=" * 60)

df_f1 = pd.read_sql("""
    SELECT
        ranking,
        score,
        razon_social,
        comuna,
        tramo_ventas,
        licitaciones_ganadas,
        total_oc,
        ROUND(monto_total_oc, 0)    AS monto_total_oc,
        ultima_oc,
        motivo
    FROM prospectos_rankeados
    WHERE
        nivel       = '1 - Contactar hoy'
        AND CAST(licitaciones_ganadas AS INTEGER) BETWEEN 1 AND 20
        AND CAST(total_oc AS INTEGER) > 0
        AND tramo_capital_negativo IS NOT NULL
        AND tramo_capital_negativo != ''
        AND tramo_capital_negativo != 'nan'
    ORDER BY score DESC, total_oc DESC
    LIMIT 60
""", conn)

print(f"\nResultado: {len(df_f1)} empresas\n")
print(df_f1.to_string(index=False))

print("\n" + "=" * 60)
print("FILTRO 2 — OC de alto monto en últimos 12 meses")
print("(independiente del nivel — puede haber joyas en Nivel 2)")
print("=" * 60)

df_f2 = pd.read_sql("""
    SELECT
        ranking,
        nivel,
        score,
        razon_social,
        comuna,
        tramo_ventas,
        total_oc,
        ROUND(monto_total_oc, 0)    AS monto_total_oc,
        ROUND(monto_total_oc / NULLIF(total_oc, 0), 0) AS promedio_oc,
        ultima_oc,
        motivo
    FROM prospectos_rankeados
    WHERE
        nivel IN ('1 - Contactar hoy', '2 - Contactar esta semana')
        AND CAST(total_oc AS INTEGER) > 0
        AND CAST(monto_total_oc AS REAL) > 10000000
        AND ultima_oc >= date('now', '-12 months')
        AND CAST(licitaciones_ganadas AS INTEGER) BETWEEN 1 AND 25
    ORDER BY monto_total_oc DESC
    LIMIT 40
""", conn)

print(f"\nResultado: {len(df_f2)} empresas con OC > $10M recientes\n")
print(df_f2.to_string(index=False))

print("\n" + "=" * 60)
print("FILTRO 3 — Empresas nuevas en MP (primera OC hace < 18 meses)")
print("Estas NO tienen factoring establecido todavía")
print("=" * 60)

df_f3 = pd.read_sql("""
    SELECT
        ranking,
        score,
        razon_social,
        comuna,
        tramo_ventas,
        licitaciones_ganadas,
        total_oc,
        ROUND(monto_total_oc, 0) AS monto_total_oc,
        ultima_oc,
        motivo
    FROM prospectos_rankeados
    WHERE
        nivel IN ('1 - Contactar hoy', '2 - Contactar esta semana')
        AND CAST(total_oc AS INTEGER) BETWEEN 1 AND 8
        AND ultima_oc >= date('now', '-18 months')
    ORDER BY score DESC, monto_total_oc DESC
    LIMIT 40
""", conn)

print(f"\nResultado: {len(df_f3)} empresas nuevas en MP\n")
print(df_f3.to_string(index=False))

print("\n" + "=" * 60)
print("RESUMEN EJECUTIVO")
print("=" * 60)
print(f"  Filtro 1 (ideal):          {len(df_f1):>4} empresas")
print(f"  Filtro 2 (alto monto):     {len(df_f2):>4} empresas")
print(f"  Filtro 3 (nuevas en MP):   {len(df_f3):>4} empresas")

# Union de los tres filtros sin duplicados
ruts_f1 = set(df_f1["razon_social"].tolist())
ruts_f2 = set(df_f2["razon_social"].tolist())
ruts_f3 = set(df_f3["razon_social"].tolist())
total_unicos = len(ruts_f1 | ruts_f2 | ruts_f3)
print(f"\n  Total únicos combinados:   {total_unicos:>4} empresas")
print(f"  → Estas son tus primeras llamadas")

conn.close()