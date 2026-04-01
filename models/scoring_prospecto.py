# =============================================================
#  models/scoring_prospecto.py — Versión 2
#
#  CORRECCIONES:
#  - Porcentaje de adjudicación calculado correctamente
#  - Motivo con montos legibles en millones
#  - Score ponderado con 8 features
#  - Nivel máximo 3 para empresas no vigentes
#
#  Genera: prospectos_rankeados
# =============================================================

import sys
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))

# Fix encoding en Windows (consola cp1252 no soporta tildes)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from config import (
    DB_PATH, SCORE_NIVEL_1, SCORE_NIVEL_2, PESOS_SCORING
)
from utils.helpers import mapa_tramo


def calcular_score(df: pd.DataFrame) -> pd.DataFrame:
    score = pd.Series(0.0, index=df.index)
    for feature, peso in PESOS_SCORING.items():
        if feature in df.columns:
            score += df[feature].fillna(0) * peso
    df["score"] = score.round(1)
    return df


def asignar_nivel(row) -> str:
    # Empresa no vigente → siempre nivel 3
    if row.get("vigente_2026", 1) == 0:
        return "3 - Empresa cerrada"

    score = row["score"]
    en_mp = int(row.get("aparece_en_mp", 0))

    if en_mp == 0:
        return "3 - Solo SII"
    if score >= SCORE_NIVEL_1:
        return "1 - Contactar hoy"
    if score >= SCORE_NIVEL_2:
        return "2 - Contactar esta semana"
    return "3 - Solo SII"


def generar_motivo(row) -> str:
    motivos = []

    # Vigencia
    if row.get("vigente_2026", 1) == 0:
        return "EMPRESA CERRADA — excluir"

    # Historial MP
    n_lit = int(row.get("licitaciones_ganadas", 0))
    n_oc  = int(row.get("total_oc", 0))

    if n_lit > 0:
        motivos.append(f"{n_lit} licitaciones ganadas")
    if n_oc > 0:
        motivos.append(f"{n_oc} OC recibidas")

    # Monto OC en formato legible
    monto_oc = float(row.get("monto_total_oc", 0) or 0)
    if monto_oc > 0:
        if monto_oc >= 1_000_000_000:
            motivos.append(f"${monto_oc/1_000_000_000:.1f}B en OC")
        elif monto_oc >= 1_000_000:
            motivos.append(f"${monto_oc/1_000_000:.1f}M en OC")
        else:
            motivos.append(f"${monto_oc:,.0f} en OC")

    # OC reciente
    if row.get("f_oc_reciente", 0) > 0:
        motivos.append("OC activa últimos 12 meses")

    # Capital negativo
    if row.get("f_capital_negativo", 0) > 0:
        motivos.append("capital negativo")

    # Tamaño empresa
    tramo = str(row.get("tramo_ventas", "")).strip()
    if tramo in mapa_tramo:
        motivos.append(mapa_tramo[tramo])

    return " | ".join(motivos) if motivos else "Sin datos MP"


def asignar_urgencia(row, hoy: pd.Timestamp) -> str:
    nivel = row.get("nivel", "")
    if "cerrada" in nivel.lower() or "solo sii" in nivel.lower():
        return "BAJA"

    monto = float(row.get("monto_total_oc", 0) or 0)
    ultima_oc = pd.to_datetime(row.get("ultima_oc"), errors="coerce")
    dias_oc = int((hoy - ultima_oc).days) if pd.notna(ultima_oc) else 9999

    if "1 -" in nivel:
        if dias_oc <= 30 or (monto > 100_000_000 and dias_oc <= 90):
            return "ALTA"
        if dias_oc <= 90:
            return "MEDIA"

    if "2 -" in nivel:
        if dias_oc <= 60:
            return "MEDIA"

    return "BAJA"


def generar_motivo_urgencia(row, hoy: pd.Timestamp) -> str:
    urgencia = row.get("urgencia_contacto", "BAJA")
    monto    = float(row.get("monto_total_oc", 0) or 0)

    ultima_oc = pd.to_datetime(row.get("ultima_oc"), errors="coerce")
    dias_oc   = int((hoy - ultima_oc).days) if pd.notna(ultima_oc) else None

    ultima_lit = pd.to_datetime(row.get("ultima_licitacion"), errors="coerce")
    dias_lit   = int((hoy - ultima_lit).days) if pd.notna(ultima_lit) else None

    if urgencia == "ALTA":
        if dias_oc is not None and dias_oc <= 30:
            return f"OC hace {dias_oc}d — ventana de contacto abierta"
        if monto > 100_000_000:
            return f"${monto/1_000_000:.0f}M en OC — alta necesidad de liquidez"
        return "Nivel 1 — contactar hoy"

    if urgencia == "MEDIA":
        if dias_oc is not None and dias_oc <= 60:
            return f"OC hace {dias_oc}d — contactar esta semana"
        return "Nivel 2 con actividad reciente"

    if dias_lit is not None:
        return f"Última licitación hace {dias_lit}d"
    return "Seguimiento rutinario"


def run():
    print("=" * 55)
    print("models/scoring_prospecto.py — Versión 2")
    print("Con porcentajes y montos corregidos")
    print("=" * 55)

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM features_prospectos", conn)
    print(f"\nEmpresas a scorear: {len(df):,}")

    # ── Calcular score ────────────────────────────────────────
    df = calcular_score(df)

    # ── Asignar nivel ─────────────────────────────────────────
    df["nivel"] = df.apply(asignar_nivel, axis=1)

    # ── Motivo legible ────────────────────────────────────────
    df["motivo"] = df.apply(generar_motivo, axis=1)

    # ── Urgencia de contacto ──────────────────────────────────
    hoy = pd.Timestamp(datetime.now())
    df["urgencia_contacto"] = df.apply(
        lambda row: asignar_urgencia(row, hoy), axis=1
    )
    df["motivo_urgencia"] = df.apply(
        lambda row: generar_motivo_urgencia(row, hoy), axis=1
    )

    # ── Ordenar ───────────────────────────────────────────────
    _sort_map = {"ALTA": 1, "MEDIA": 2, "BAJA": 3}
    df["_urgencia_sort"] = df["urgencia_contacto"].map(_sort_map).fillna(3)
    df = df.sort_values(
        ["_urgencia_sort", "score", "monto_total_oc"],
        ascending=[True, False, False]
    ).reset_index(drop=True)
    df["ranking"] = df.index + 1
    df = df.drop(columns=["_urgencia_sort"])

    # ── Columnas output ───────────────────────────────────────
    COLS = [
        "ranking", "nivel", "urgencia_contacto", "score",
        "rut_normalizado", "razon_social",
        "comuna", "region",
        "actividad_economica", "actividad_2026", "rubro_economico",
        "tramo_ventas", "num_trabajadores",
        "tramo_capital_negativo",
        "fecha_inicio_actividades",
        "otros_regimenes", "vigente_2026",
        "aparece_en_mp", "aparece_en_oc",
        "licitaciones_ganadas",
        "monto_total_adjudicado",
        "total_oc", "monto_total_oc", "monto_prom_oc",
        "ultima_oc", "ultima_licitacion",
        "organismos_distintos", "organismos_oc",
        "motivo", "motivo_urgencia",
    ]
    COLS = [c for c in COLS if c in df.columns]
    df_out = df[COLS].copy()

    # ── Guardar ───────────────────────────────────────────────
    df_out.to_sql(
        "prospectos_rankeados", conn,
        if_exists="replace", index=False
    )
    conn.commit()

    # ── Resumen ───────────────────────────────────────────────
    print("\nDistribución por nivel:")
    for nivel, cnt in (df_out["nivel"]
                       .value_counts().sort_index().items()):
        print(f"  {nivel}: {cnt:,}")

    print(f"\nTop 15 prospectos:")
    preview = ["ranking", "nivel", "score",
               "razon_social", "comuna", "motivo"]
    preview = [c for c in preview if c in df_out.columns]
    print(df_out[preview].head(15).to_string(index=False))

    conn.close()
    print("\nmodels/scoring_prospecto.py completado.")
    print("Siguiente: python reports/exportar_excel.py")


if __name__ == "__main__":
    run()