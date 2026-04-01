# =============================================================
#  transform/construir_features.py — Versión 2
#
#  CAMBIOS:
#  - Usa monto_total_adjudicado (corregido) en vez de
#    monto_total_licitado inflado
#  - Usa monto_total_oc (corregido, deduplicado)
#  - Agrega feature de monto promedio por OC
#  - Usa actividad_2026 si está disponible
#
#  Genera: features_prospectos
# =============================================================

import sys
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import DB_PATH


def run():
    print("=" * 55)
    print("transform/construir_features.py — Versión 2")
    print("=" * 55)

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM clean_proveedores", conn)
    print(f"\nEmpresas cargadas: {len(df):,}")

    hoy = datetime.now()

    # ── FEATURE 1: Historial adjudicación (0-100) ─────────────
    # Escala logarítmica: 1 licitación = 40 pts, 10 = 80, 20+ = 100
    df["f_historial"] = np.where(
        df["licitaciones_ganadas"] == 0, 0,
        np.clip(
            40 + 30 * np.log10(
                df["licitaciones_ganadas"].clip(1)
            ), 0, 100
        )
    ).round(1)

    # ── FEATURE 2: Tramo ventas (0-100) ───────────────────────
    mapa_tramo = {
        "1": 20, "2": 85, "3": 100,
        "4": 60, "5": 40, "6": 30,
        "7": 20, "8": 10, "9": 5,
        "10": 5, "11": 5, "12": 5, "13": 5,
    }
    df["f_tramo_ventas"] = (
        df["tramo_ventas"].astype(str).str.strip()
        .map(mapa_tramo).fillna(0)
    )

    # ── FEATURE 3: Capital negativo (0 o 100) ─────────────────
    df["f_capital_negativo"] = df["tramo_capital_negativo"].apply(
        lambda x: 100 if pd.notna(x) and
        str(x).strip() not in ["", "0", "nan", "None"] else 0
    )

    # ── FEATURE 4: Antigüedad (0-100) ─────────────────────────
    def score_antiguedad(fecha_str):
        try:
            fecha = pd.to_datetime(fecha_str, format="%d-%m-%Y", errors="coerce")
            if pd.isna(fecha):
                fecha = pd.to_datetime(fecha_str, errors="coerce")
            if pd.isna(fecha):
                return 0
            anios = (hoy - fecha).days / 365.25
            if anios < 1:    return 10
            elif anios <= 3: return 70
            elif anios <= 10: return 100
            elif anios <= 20: return 80
            else:            return 50
        except Exception:
            return 0

    # Usar fecha_inicio_2026 si existe, si no la del 2024
    col_fecha = ("fecha_inicio_2026"
                 if "fecha_inicio_2026" in df.columns
                 else "fecha_inicio_actividades")
    df["f_antiguedad"] = df[col_fecha].apply(score_antiguedad)

    # ── FEATURE 5: Rubro prioritario (0-100) ──────────────────
    RUBROS_OK = [
        "acuicultura", "pesca", "transporte", "construccion",
        "construcci", "agricol", "ganaderia", "lacteos",
        "alimentos", "forestal", "madera", "turismo",
        "servicios", "manufactura", "salud", "educacion",
    ]

    # Usar actividad_2026 si existe
    col_act = ("actividad_2026"
               if "actividad_2026" in df.columns
               else "actividad_economica")

    def es_rubro_prioritario(rubro):
        if pd.isna(rubro):
            return 30
        r = str(rubro).lower()
        return 100 if any(k in r for k in RUBROS_OK) else 30

    df["f_rubro_prioritario"] = df[col_act].apply(
        es_rubro_prioritario
    )

    # ── FEATURE 6: Volumen OC (0-100) ─────────────────────────
    # Escala logarítmica sobre número de OC
    df["f_volumen_oc"] = np.where(
        df["total_oc"] == 0, 0,
        np.clip(20 * np.log10(df["total_oc"].clip(1)), 0, 100)
    ).round(1)

    # ── FEATURE 7: OC reciente < 12 meses (0 o 100) ──────────
    df["ultima_oc_dt"] = pd.to_datetime(
        df["ultima_oc"], format="mixed", dayfirst=True, errors="coerce"
    )
    df["f_oc_reciente"] = (
        (hoy - df["ultima_oc_dt"]).dt.days < 365
    ).fillna(False).astype(int) * 100

    # ── FEATURE 8: Monto promedio OC (0-100) ──────────────────
    # OC promedio > $5M = necesidad real de factoring
    df["f_monto_oc"] = np.where(
        df["monto_prom_oc"].fillna(0) <= 0, 0,
        np.clip(
            20 * np.log10(
                df["monto_prom_oc"].fillna(0).clip(1) / 1_000_000
            ) + 40, 0, 100
        )
    ).round(1)

    # ── FEATURE 9: Tasa de adjudicación OCDS (0-100) ──────────
    # Ratio ganadas / total_postuladas de raw_oferentes
    try:
        df_ocds = pd.read_sql("""
            SELECT rut_normalizado,
                   SUM(CASE WHEN adjudicado = 1 THEN 1 ELSE 0 END) AS ganadas,
                   COUNT(*) AS total_postuladas
            FROM raw_oferentes
            GROUP BY rut_normalizado
            HAVING total_postuladas >= 3
        """, conn)
        if len(df_ocds) >= 50:
            df_ocds["f_tasa_adjudicacion"] = (
                df_ocds["ganadas"] / df_ocds["total_postuladas"] * 100
            ).clip(0, 100).round(1)
            df = df.merge(
                df_ocds[["rut_normalizado", "f_tasa_adjudicacion"]],
                on="rut_normalizado", how="left"
            )
            df["f_tasa_adjudicacion"] = df["f_tasa_adjudicacion"].fillna(50)
            print(f"  f_tasa_adjudicacion: {len(df_ocds):,} empresas con OCDS")
        else:
            df["f_tasa_adjudicacion"] = 50
            print("  f_tasa_adjudicacion: sin datos OCDS (usando 50)")
    except Exception as exc:
        df["f_tasa_adjudicacion"] = 50
        print(f"  f_tasa_adjudicacion: fallback 50 — {exc}")

    # ── FEATURE 10: Especialización por rubro (0-100) ──────────
    # % de OC concentradas en el rubro principal de la empresa
    try:
        df_rubro_raw = pd.read_sql("""
            SELECT rut_proveedor_norm AS rut_normalizado,
                   rubron1,
                   COUNT(*) AS n_oc_rubro
            FROM clean_ordenes
            WHERE rubron1 IS NOT NULL AND rubron1 != ''
            GROUP BY rut_proveedor_norm, rubron1
        """, conn)
        df_rubro_total = (
            df_rubro_raw.groupby("rut_normalizado")["n_oc_rubro"]
            .sum().reset_index(name="n_oc_total")
        )
        df_rubro_max = (
            df_rubro_raw.sort_values("n_oc_rubro", ascending=False)
            .drop_duplicates("rut_normalizado")
            [["rut_normalizado", "n_oc_rubro"]]
        )
        df_esp = df_rubro_total.merge(df_rubro_max, on="rut_normalizado")
        df_esp["f_especializacion_rubro"] = (
            df_esp["n_oc_rubro"] / df_esp["n_oc_total"] * 100
        ).clip(0, 100).round(1)
        df = df.merge(
            df_esp[["rut_normalizado", "f_especializacion_rubro"]],
            on="rut_normalizado", how="left"
        )
        df["f_especializacion_rubro"] = df["f_especializacion_rubro"].fillna(50)
    except Exception as exc:
        df["f_especializacion_rubro"] = 50
        print(f"  f_especializacion_rubro: fallback 50 — {exc}")

    # ── FEATURE 11: Licitación grande reciente (0 o 100) ────────
    # Ganó licitación > $50M en los últimos 90 días
    try:
        df_grande = pd.read_sql("""
            SELECT DISTINCT rut_proveedor_norm AS rut_normalizado
            FROM clean_licitaciones
            WHERE monto_total_adjudicado > 50000000
            AND fechaadjudicacion >= date('now', '-90 days')
        """, conn)
        ruts_grandes = set(df_grande["rut_normalizado"].tolist())
        df["f_licitacion_grande_reciente"] = (
            df["rut_normalizado"].isin(ruts_grandes)
        ).astype(int) * 100
    except Exception as exc:
        df["f_licitacion_grande_reciente"] = 0
        print(f"  f_licitacion_grande_reciente: fallback 0 — {exc}")

    # ── FEATURE 12: Diversificación por organismo (0-100) ───────
    # 100% en 1 organismo → 0 (riesgo); distribuido → 100
    try:
        df_org_raw = pd.read_sql("""
            SELECT rut_proveedor_norm AS rut_normalizado,
                   codigoorganismopublico,
                   COUNT(*) AS n_oc
            FROM clean_ordenes
            WHERE codigoorganismopublico IS NOT NULL
            GROUP BY rut_proveedor_norm, codigoorganismopublico
        """, conn)
        df_org_total = (
            df_org_raw.groupby("rut_normalizado")["n_oc"]
            .sum().reset_index(name="n_oc_total")
        )
        df_org_max = (
            df_org_raw.groupby("rut_normalizado")["n_oc"]
            .max().reset_index(name="n_oc_max")
        )
        df_conc = df_org_total.merge(df_org_max, on="rut_normalizado")
        df_conc["f_concentracion_organismo"] = (
            100 - df_conc["n_oc_max"] / df_conc["n_oc_total"] * 100
        ).clip(0, 100).round(1)
        df = df.merge(
            df_conc[["rut_normalizado", "f_concentracion_organismo"]],
            on="rut_normalizado", how="left"
        )
        df["f_concentracion_organismo"] = (
            df["f_concentracion_organismo"].fillna(50)
        )
    except Exception as exc:
        df["f_concentracion_organismo"] = 50
        print(f"  f_concentracion_organismo: fallback 50 — {exc}")

    # ── FEATURE 13: Días entre adjudicación y OC (0-100) ────────
    # Menor tiempo → más urgente necesidad de liquidez
    try:
        df_dias = pd.read_sql("""
            SELECT o.rut_proveedor_norm AS rut_normalizado,
                   AVG(julianday(o.fechaaceptacion)
                       - julianday(l.fechaadjudicacion)) AS dias_prom
            FROM clean_ordenes o
            JOIN clean_licitaciones l
                ON o.codigolicitacion = l.codigoexterno
            WHERE o.fechaaceptacion IS NOT NULL
            AND l.fechaadjudicacion IS NOT NULL
            GROUP BY o.rut_proveedor_norm
            HAVING COUNT(*) >= 2
        """, conn)

        def _score_dias(d):
            if pd.isna(d) or d <= 0: return 50
            if d <= 15:  return 100
            if d <= 30:  return 85
            if d <= 60:  return 60
            return 30

        df_dias["f_dias_entre_adj_oc"] = df_dias["dias_prom"].apply(_score_dias)
        df = df.merge(
            df_dias[["rut_normalizado", "f_dias_entre_adj_oc"]],
            on="rut_normalizado", how="left"
        )
        df["f_dias_entre_adj_oc"] = df["f_dias_entre_adj_oc"].fillna(50)
    except Exception as exc:
        df["f_dias_entre_adj_oc"] = 50
        print(f"  f_dias_entre_adj_oc: fallback 50 — {exc}")

    # ── Columnas de salida ────────────────────────────────────
    cols_id = [
        "rut_normalizado", "razon_social", "region",
        "provincia", "comuna",
        "actividad_economica", "actividad_2026", "rubro_economico",
        "tramo_ventas", "num_trabajadores",
        "tramo_capital_positivo", "tramo_capital_negativo",
        "fecha_inicio_actividades",
        "otros_regimenes", "vigente_2026",
        "aparece_en_mp", "aparece_en_oc",
        "licitaciones_ganadas", "total_licitaciones",
        "monto_total_adjudicado",
        "total_oc", "monto_total_oc", "monto_prom_oc",
        "ultima_oc", "ultima_licitacion",
        "organismos_distintos", "organismos_oc",
        "rubro_frecuente",
    ]
    cols_features = [
        "f_historial", "f_tramo_ventas", "f_capital_negativo",
        "f_antiguedad", "f_rubro_prioritario",
        "f_volumen_oc", "f_oc_reciente", "f_monto_oc",
        "f_tasa_adjudicacion", "f_especializacion_rubro",
        "f_licitacion_grande_reciente", "f_concentracion_organismo",
        "f_dias_entre_adj_oc",
    ]

    # Solo columnas que existen
    cols_id = [c for c in cols_id if c in df.columns]
    df_out = df[cols_id + cols_features].copy()

    # ── Resumen features ──────────────────────────────────────
    print("\nFeatures construidas:")
    for f in cols_features:
        if f in df_out.columns:
            print(f"  {f}: "
                  f"media={df_out[f].mean():.1f} | "
                  f"max={df_out[f].max():.1f} | "
                  f"min={df_out[f].min():.1f}")

    # ── Guardar ───────────────────────────────────────────────
    df_out.to_sql(
        "features_prospectos", conn,
        if_exists="replace", index=False
    )
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) FROM features_prospectos"
    ).fetchone()[0]
    print(f"\n  features_prospectos: {n:,} registros")
    conn.close()
    print("\ntransform/construir_features.py completado.")


if __name__ == "__main__":
    run()