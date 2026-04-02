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

    # ── FEATURE 14: Ratio OC / licitación (0-100) ───────────────
    # Revela el TIPO de relación empresa-Estado:
    #   CONVENIO MARCO (ratio ≥ 8): empresa pre-aprobada, OC fluyen sin
    #     licitar cada vez. Facturando recurrentemente. Score alto porque
    #     la necesidad de capital de trabajo es CONSTANTE y predecible.
    #   SUMINISTRO (ratio 3-8): contrato de provisión periódica. OC llegan
    #     con regularidad bajo el mismo contrato. También muy atractivo.
    #   LICITACIÓN TRADICIONAL (ratio < 3): gana licitaciones puntuales.
    #     OC llegan en picos. Menos predecible pero tickets más grandes.
    #
    # ¿Con qué datos?
    # ratio_oc_licitacion viene de cruzar_fuentes.py (ya calculado).
    # Disponible con los datos actuales del CSV histórico.
    def _score_ratio(ratio):
        if ratio == 0:    return 0
        if ratio >= 10:   return 100   # CM claro
        if ratio >= 6:    return 88
        if ratio >= 3:    return 72    # suministro
        if ratio >= 1.5:  return 50    # licitación con varios ítems
        return 30                      # licitación 1:1

    df["f_ratio_oc_licitacion"] = (
        df["ratio_oc_licitacion"].fillna(0).apply(_score_ratio)
    )
    print(f"  f_ratio_oc_licitacion: CM={( df['ratio_oc_licitacion'].fillna(0) >= 8).sum():,} "
          f"| suministro={((df['ratio_oc_licitacion'].fillna(0) >= 3) & (df['ratio_oc_licitacion'].fillna(0) < 8)).sum():,} "
          f"| tradicional={((df['ratio_oc_licitacion'].fillna(0) > 0) & (df['ratio_oc_licitacion'].fillna(0) < 3)).sum():,}")

    # ── FEATURE 15: Estacionalidad presupuestaria (0-100) ────────
    # El presupuesto público chileno se concentra en ciertos meses:
    #   Oct-Dic: cierre de año presupuestario → máximo gasto (score 100)
    #   Mar-Abr: inicio de proyectos post-verano → alto (75)
    #   May-Jun: actividad normal → medio (60)
    #   Ene-Feb: presupuesto recién aprobado, trámites lentos → bajo (45)
    #   Jul-Sep: mitad de año, lentitud en muchos organismos → bajo (40)
    #
    # ¿Con qué datos?
    # Esta feature NO necesita datos históricos — se calcula sobre el mes
    # ACTUAL de ejecución. Es correcta con los datos que ya tenemos.
    # Se recalcula automáticamente cada vez que corre el pipeline.
    _mes_actual = hoy.month
    _score_mes = {
        1: 45,  # Enero  — presupuesto aprobado, tramitación lenta
        2: 45,  # Febrero — aún arranque del año
        3: 75,  # Marzo  — proyectos nuevos activos
        4: 75,  # Abril  — proyectos nuevos activos
        5: 60,  # Mayo   — actividad normal
        6: 60,  # Junio  — actividad normal
        7: 40,  # Julio  — lentitud mid-year
        8: 40,  # Agosto — lentitud mid-year
        9: 50,  # Sept   — preparación cierre año
        10: 90, # Oct    — cierre presupuestario inminente
        11: 100,# Nov    — máximo gasto público del año
        12: 95, # Dic    — último mes presupuesto, gastar o perder
    }
    df["f_estacionalidad"] = _score_mes.get(_mes_actual, 50)
    print(f"  f_estacionalidad: mes={_mes_actual} → score={_score_mes.get(_mes_actual,50)}")

    # ── FEATURE 15: Actividad histórica en el trimestre actual (0-100) ──
    # ¿Esta empresa suele tener OC en los meses del trimestre actual?
    # Alta concentración histórica en el trimestre actual = mayor probabilidad
    # de que necesite factoring ahora.
    #
    # ¿Con qué datos?
    # Requiere fechas de OC históricas (clean_ordenes). Disponible.
    # LIMITACIÓN: si la empresa tiene pocas OC (<3), este feature es poco
    # confiable y se imputa con 50 (neutral).
    _trimestre_actual = (_mes_actual - 1) // 3 + 1  # 1,2,3,4
    _meses_trimestre  = list(range((_trimestre_actual-1)*3+1,
                                   _trimestre_actual*3+1))
    try:
        df_mes = pd.read_sql(f"""
            SELECT
                rut_proveedor_norm AS rut_normalizado,
                SUM(CASE WHEN CAST(strftime('%m', fechaaceptacion) AS INT)
                         IN ({','.join(str(m) for m in _meses_trimestre)})
                         THEN 1 ELSE 0 END) AS oc_este_trimestre,
                COUNT(*) AS total_oc_hist
            FROM clean_ordenes
            WHERE es_aceptada = 1
            GROUP BY rut_proveedor_norm
            HAVING total_oc_hist >= 3
        """, conn)
        df_mes["f_mes_activo"] = (
            df_mes["oc_este_trimestre"] / df_mes["total_oc_hist"] * 100
        ).clip(0, 100).round(1)
        df = df.merge(
            df_mes[["rut_normalizado", "f_mes_activo"]],
            on="rut_normalizado", how="left"
        )
        df["f_mes_activo"] = df["f_mes_activo"].fillna(50)
        print(f"  f_mes_activo: trimestre {_trimestre_actual} "
              f"(meses {_meses_trimestre}) — {len(df_mes):,} empresas")
    except Exception as exc:
        df["f_mes_activo"] = 50
        print(f"  f_mes_activo: fallback 50 — {exc}")

    # ── FEATURE 16: Crecimiento OC año a año (0-100) ────────────
    # Empresa que aumenta sus OC necesita más capital de trabajo.
    # Compara OC de los últimos 12 meses vs los 12 meses anteriores.
    try:
        df_growth = pd.read_sql("""
            SELECT
                rut_proveedor_norm AS rut_normalizado,
                SUM(CASE WHEN fechaaceptacion >= date('now', '-12 months')
                         THEN 1 ELSE 0 END) AS oc_12m,
                SUM(CASE WHEN fechaaceptacion >= date('now', '-24 months')
                          AND fechaaceptacion < date('now', '-12 months')
                         THEN 1 ELSE 0 END) AS oc_12m_ant
            FROM clean_ordenes
            WHERE es_aceptada = 1
            GROUP BY rut_proveedor_norm
            HAVING oc_12m + oc_12m_ant > 0
        """, conn)

        def _score_crecimiento(row):
            prev = row["oc_12m_ant"]
            curr = row["oc_12m"]
            if prev == 0 and curr == 0:
                return 0
            if prev == 0:
                return 75   # empresa que recién empieza a tener OC
            ratio = curr / prev
            if ratio >= 3.0: return 100
            if ratio >= 2.0: return 85
            if ratio >= 1.5: return 70
            if ratio >= 1.0: return 55
            if ratio >= 0.5: return 30
            return 10   # contracción fuerte

        df_growth["f_crecimiento_oc_yoy"] = df_growth.apply(
            _score_crecimiento, axis=1
        )
        df = df.merge(
            df_growth[["rut_normalizado", "f_crecimiento_oc_yoy"]],
            on="rut_normalizado", how="left"
        )
        df["f_crecimiento_oc_yoy"] = df["f_crecimiento_oc_yoy"].fillna(0)
        print(f"  f_crecimiento_oc_yoy: {len(df_growth):,} empresas")
    except Exception as exc:
        df["f_crecimiento_oc_yoy"] = 0
        print(f"  f_crecimiento_oc_yoy: fallback 0 — {exc}")

    # ── FEATURE 15: Velocidad de pago de los organismos cliente (0-100) ──
    # Empresa que vende a organismos que pagan rápido = mejor prospecto
    # de factoring (el factor puede cobrar en menos tiempo).
    # Usa tabla plazos_pago_organismos generada por calcular_plazos_pago.py
    try:
        df_plazo_cli = pd.read_sql("""
            SELECT
                o.rut_proveedor_norm  AS rut_normalizado,
                AVG(p.score_velocidad_pago) AS f_plazo_pago_cliente
            FROM clean_ordenes o
            JOIN plazos_pago_organismos p
                ON o.codigoorganismopublico = p.codigoorganismo
            WHERE o.es_aceptada = 1
            GROUP BY o.rut_proveedor_norm
        """, conn)
        df = df.merge(df_plazo_cli, on="rut_normalizado", how="left")
        df["f_plazo_pago_cliente"] = (
            df["f_plazo_pago_cliente"].fillna(50).round(1)
        )
        print(f"  f_plazo_pago_cliente: {len(df_plazo_cli):,} empresas")
    except Exception as exc:
        df["f_plazo_pago_cliente"] = 50
        print(f"  f_plazo_pago_cliente: fallback 50 — {exc}")

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
        "ratio_oc_licitacion", "tipo_relacion_estado",
        "pct_oc_convenio_marco",
        "oc_30d_monto", "oc_12m_monto",
    ]
    cols_features = [
        "f_historial", "f_tramo_ventas", "f_capital_negativo",
        "f_antiguedad", "f_rubro_prioritario",
        "f_volumen_oc", "f_oc_reciente", "f_monto_oc",
        "f_tasa_adjudicacion", "f_especializacion_rubro",
        "f_licitacion_grande_reciente", "f_concentracion_organismo",
        "f_ratio_oc_licitacion",
        "f_dias_entre_adj_oc",
        "f_crecimiento_oc_yoy", "f_plazo_pago_cliente",
        "f_estacionalidad", "f_mes_activo",
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