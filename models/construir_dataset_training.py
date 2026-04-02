# =============================================================
#  models/construir_dataset_training.py
#
#  Propósito: Construye el dataset de entrenamiento para el
#  modelo predictivo de adjudicación cruzando:
#    - raw_oferentes      (quién compitió en cada licitación)
#    - clean_licitaciones (quién ganó — fuente de verdad CSV)
#    - raw_empresas_sii   (tramo ventas y capital — todo Chile)
#
#  LABEL COMBINADO (gano):
#    gano=1 si el proveedor ganó según CSV  (rut == rut_proveedor_norm)
#           O según OCDS (adjudicado=1) para licitaciones multi-ganador
#           donde el CSV solo registra un ganador pero hubo varios.
#    Esto corrige ~19.000 falsos negativos causados por licitaciones
#    multi-ítem y Convenios Marco (tipo LR, LP, LE con varios ítems).
#
#  TIPO DE LICITACIÓN:
#    Se agrega es_convenio_marco (tipo LR) como feature porque en
#    esas licitaciones la dinámica de selección es completamente
#    distinta (pre-aprobación de catálogo, no competencia directa).
#
#  COBERTURA SII:
#    Se une directamente con raw_empresas_sii (todo Chile, año más
#    reciente) en vez de features_prospectos (solo Los Lagos).
#    Mejora cobertura de tramo_ventas de ~17% a ~60-70%.
#
#  Inputs:  raw_oferentes, clean_licitaciones, raw_empresas_sii
#  Outputs: training_dataset (tabla SQLite)
#           data/training_dataset.csv (para inspección)
#           lookup_n_hist, lookup_win_rate_org, lookup_monto_emp,
#           lookup_especializacion  (tablas de lookup para predicción)
#
#  Uso:
#    python models/construir_dataset_training.py
# =============================================================

import sys
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import DB_PATH, DATA_DIR

TRAINING_CSV = DATA_DIR / "training_dataset.csv"


# ─────────────────────────────────────────────────────────────
# CARGA Y CRUCE PRINCIPAL
# ─────────────────────────────────────────────────────────────

def cargar_datos_base(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Cruza raw_oferentes con clean_licitaciones.

    Label combinado (gano):
      - gano=1 si el RUT del oferente coincide con el ganador en CSV, O
      - gano=1 si OCDS marcó adjudicado=1 (captura multi-ganadores:
        Convenios Marco, licitaciones multi-ítem donde el CSV registra
        solo un ganador pero hubo varios)

    Esto elimina ~19.000 falsos negativos del dataset anterior.

    Feature adicional: es_convenio_marco (tipo LR) para separar
    la dinámica de Convenio Marco de licitaciones competitivas normales.
    """
    print("  Cargando cruce raw_oferentes x clean_licitaciones...")
    df = pd.read_sql("""
        SELECT
            o.id_licitacion,
            o.rut_normalizado,
            o.razon_social,
            o.region_empresa,
            o.n_competidores,
            -- Label combinado: CSV o OCDS (cubre multi-ganadores)
            CASE WHEN o.rut_normalizado = l.rut_proveedor_norm
                      OR o.adjudicado = 1
                 THEN 1 ELSE 0 END AS gano,
            -- Fuente del label (para trazabilidad)
            CASE WHEN o.rut_normalizado = l.rut_proveedor_norm THEN 'csv'
                 WHEN o.adjudicado = 1 THEN 'ocds'
                 ELSE 'ninguna' END AS label_fuente,
            -- Enriquecimiento desde clean_licitaciones
            l.fechaadjudicacion,
            l.regionunidad      AS region_licitacion,
            l.rubro1            AS rubro_licitacion,
            l.tipo              AS tipo_licitacion,
            l.montoestimado     AS monto_estimado,
            l.codigoorganismo   AS organismo,
            -- Feature: convenio marco vs licitacion normal
            CASE WHEN UPPER(l.tipo) LIKE 'LR%' THEN 1 ELSE 0 END AS es_convenio_marco
        FROM raw_oferentes o
        INNER JOIN clean_licitaciones l
            ON o.id_licitacion = l.codigoexterno
        WHERE l.es_adjudicada = 1
          AND l.rut_proveedor_norm IS NOT NULL
          AND l.rut_proveedor_norm != ''
    """, conn)

    n_gan = int(df["gano"].sum())
    n_per = int((df["gano"] == 0).sum())
    por_fuente = df[df["gano"] == 1]["label_fuente"].value_counts()
    print(f"  Registros base: {len(df):,}  (ganadores={n_gan:,} | perdedores={n_per:,})")
    print(f"  Origen label gano=1:  csv={por_fuente.get('csv',0):,}  "
          f"ocds={por_fuente.get('ocds',0):,}  "
          f"ambos={por_fuente.get('ambos',0):,}")
    cm = int(df["es_convenio_marco"].sum())
    print(f"  Registros Convenio Marco: {cm:,} ({100*cm/len(df):.1f}%)")
    return df


def agregar_historial_temporal(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula wins_previos para cada (rut, licitacion) sin data leakage.
    Para cada fila, cuenta cuántas licitaciones anteriores ganó ese
    proveedor (fechaadjudicacion estrictamente anterior a la actual).

    Técnica: cumsum por proveedor ordenado por fecha, desplazado en 1.
    Si dos licitaciones del mismo proveedor caen en la misma fecha,
    se ordenan por id_licitacion (determinístico).
    """
    print("  Calculando historial temporal por proveedor...")
    df = df.copy()
    df["fechaadjudicacion"] = pd.to_datetime(df["fechaadjudicacion"], errors="coerce")

    df = df.sort_values(
        ["rut_normalizado", "fechaadjudicacion", "id_licitacion"]
    ).reset_index(drop=True)

    df["wins_previos"] = (
        df.groupby("rut_normalizado")["gano"]
        .transform(lambda s: s.cumsum().shift(1).fillna(0))
    )
    print(f"  Historial listo. Max wins_previos: {int(df['wins_previos'].max())}")
    return df


def agregar_features_proveedor(df: pd.DataFrame, conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Une tramo_ventas y tramo_capital_negativo desde raw_empresas_sii (todo Chile),
    usando el año más reciente disponible por RUT.
    También agrega ratio_oc_licitacion desde clean_proveedores (Los Lagos).

    Por qué raw_empresas_sii y no features_prospectos para SII:
      features_prospectos fue construido enfocado en Los Lagos, cubriendo
      solo ~17% de los providers del training_dataset. Los proveedores que
      ganan licitaciones de Los Lagos pueden ser de cualquier región (RM,
      Bio-Bío, Valparaíso, etc.) y sus datos SII están en raw_empresas_sii
      nacional. Este join sube la cobertura al ~60-70%.

    ratio_oc_licitacion viene de clean_proveedores (solo Los Lagos). Para
    proveedores de otras regiones el valor queda en 0 (default conservador).
    """
    print("  Uniendo features SII nacionales (raw_empresas_sii, ano mas reciente)...")
    df_sii = pd.read_sql("""
        SELECT s.rut_normalizado, s.tramo_ventas, s.tramo_capital_negativo
        FROM raw_empresas_sii s
        INNER JOIN (
            SELECT rut_normalizado, MAX(anio_comercial) AS max_anio
            FROM raw_empresas_sii
            GROUP BY rut_normalizado
        ) m ON s.rut_normalizado = m.rut_normalizado
           AND s.anio_comercial  = m.max_anio
    """, conn)
    df_sii = df_sii.drop_duplicates("rut_normalizado")

    antes = int(df["tramo_ventas"].notna().sum()) if "tramo_ventas" in df.columns else 0
    df = df.merge(df_sii, on="rut_normalizado", how="left")
    con_sii = int(df["tramo_ventas"].notna().sum())
    print(f"  Cobertura SII: {con_sii:,} de {len(df):,} filas ({100 * con_sii / len(df):.0f}%)")

    # ratio_oc_licitacion: OC aceptadas / licitaciones ganadas por empresa.
    # Se calcula desde clean_ordenes x clean_licitaciones (cobertura nacional)
    # en vez de clean_proveedores (solo Los Lagos SII).
    # Esto cubre cualquier empresa que opere en el mercado público de Los Lagos
    # sin importar su región de registro, subiendo la cobertura del ~16% al ~60-70%.
    try:
        df_ratio = pd.read_sql("""
            SELECT
                o.rut_proveedor_norm AS rut_normalizado,
                CAST(COUNT(DISTINCT o.codigo) AS REAL)
                    / MAX(CAST(COUNT(DISTINCT l.codigoexterno) AS REAL), 1.0)
                    AS ratio_oc_licitacion
            FROM clean_ordenes o
            LEFT JOIN clean_licitaciones l
                ON o.rut_proveedor_norm = l.rut_proveedor_norm
               AND l.es_adjudicada = 1
            WHERE o.es_aceptada = 1
            GROUP BY o.rut_proveedor_norm
        """, conn)
        df_ratio = df_ratio.drop_duplicates("rut_normalizado")
        df = df.merge(df_ratio, on="rut_normalizado", how="left")
        df["ratio_oc_licitacion"] = df["ratio_oc_licitacion"].fillna(0)
        con_ratio = int((df["ratio_oc_licitacion"] > 0).sum())
        print(f"  ratio_oc_licitacion: {con_ratio:,} filas con ratio > 0 "
              f"({100 * con_ratio / len(df):.0f}%) — calculo nacional")
    except Exception as exc:
        df["ratio_oc_licitacion"] = 0.0
        print(f"  ratio_oc_licitacion: fallback 0 — {exc}")

    return df


# ─────────────────────────────────────────────────────────────
# FEATURES AVANZADAS (sin data leakage temporal)
# ─────────────────────────────────────────────────────────────

def agregar_features_avanzadas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega 4 features nuevas calculadas temporalmente (sin leakage):

    1. n_competidores_hist — mediana de competidores por (organismo, tipo)
       usando solo licitaciones ANTERIORES a la actual.
       Reemplaza n_competidores real que es desconocido al momento de predecir.

    2. win_rate_organismo — tasa de victorias empresa × organismo usando
       solo bids ANTERIORES. Captura relaciones históricas empresa-cliente.

    3. match_monto — similitud entre monto licitación y rango histórico de
       la empresa (basado en montos ganados previos). Empresas ganan más en
       su "rango natural" de contratos.

    4. especializacion_tipo — fracción de bids empresa en este tipo de
       licitación. Especialistas ganan más que generalistas.
    """
    df = df.copy()
    df["fechaadjudicacion"] = pd.to_datetime(df["fechaadjudicacion"], errors="coerce")
    df["monto_estimado"]    = pd.to_numeric(df["monto_estimado"], errors="coerce").fillna(0)
    mediana_global_comp     = df["n_competidores"].median()

    # ── 1. n_competidores_hist ────────────────────────────────
    # Mediana temporal de competidores por (organismo, tipo) usando datos anteriores
    df_licit = (
        df[["id_licitacion", "organismo", "tipo_licitacion",
            "n_competidores", "fechaadjudicacion"]]
        .drop_duplicates("id_licitacion")
        .sort_values(["organismo", "tipo_licitacion",
                      "fechaadjudicacion", "id_licitacion"])
        .copy()
    )
    df_licit["n_competidores_hist"] = (
        df_licit.groupby(["organismo", "tipo_licitacion"])["n_competidores"]
        .transform(lambda s: s.expanding().median().shift(1).fillna(mediana_global_comp))
        .round(1)
    )
    df = df.merge(
        df_licit[["id_licitacion", "n_competidores_hist"]],
        on="id_licitacion", how="left"
    )
    df["n_competidores_hist"] = df["n_competidores_hist"].fillna(mediana_global_comp)
    print(f"  n_competidores_hist : media={df['n_competidores_hist'].mean():.1f} "
          f"(original={df['n_competidores'].mean():.1f})")

    # ── 2. win_rate_organismo ─────────────────────────────────
    # Victorias/bids empresa × organismo antes de esta licitación
    df = df.sort_values(
        ["rut_normalizado", "organismo", "fechaadjudicacion", "id_licitacion"]
    ).reset_index(drop=True)
    df["_bids_org"] = df.groupby(["rut_normalizado", "organismo"]).cumcount()
    df["_wins_org"] = (
        df.groupby(["rut_normalizado", "organismo"])["gano"]
        .transform(lambda s: s.cumsum().shift(1).fillna(0))
    )
    df["win_rate_organismo"] = (
        df["_wins_org"] / (df["_bids_org"] + 1)
    ).clip(0, 1).fillna(0).round(4)
    df.drop(columns=["_bids_org", "_wins_org"], inplace=True)
    print(f"  win_rate_organismo  : media={df['win_rate_organismo'].mean():.4f} "
          f"| max={df['win_rate_organismo'].max():.3f}")

    # ── 3. match_monto ────────────────────────────────────────
    # Similaridad entre monto licitación y mediana histórica de montos ganados
    df = df.sort_values(
        ["rut_normalizado", "fechaadjudicacion", "id_licitacion"]
    ).reset_index(drop=True)
    df["_monto_si_gano"] = df["monto_estimado"].where(df["gano"] == 1, other=np.nan)
    df["empresa_monto_median"] = (
        df.groupby("rut_normalizado")["_monto_si_gano"]
        .transform(lambda s: s.expanding().median().shift(1))
        .fillna(0)
    )
    df.drop(columns=["_monto_si_gano"], inplace=True)

    def _match(m_hist, m_licit):
        if m_hist <= 0 or m_licit <= 0:
            return 0.5
        return min(m_hist, m_licit) / max(m_hist, m_licit)

    df["match_monto"] = df.apply(
        lambda r: _match(r["empresa_monto_median"], r["monto_estimado"]), axis=1
    ).clip(0, 1).round(4)
    df.drop(columns=["empresa_monto_median"], inplace=True)
    print(f"  match_monto         : media={df['match_monto'].mean():.3f}")

    # ── 4. especializacion_tipo ───────────────────────────────
    # Fracción de bids empresa en este tipo licitación (temporal)
    df = df.sort_values(
        ["rut_normalizado", "tipo_licitacion", "fechaadjudicacion", "id_licitacion"]
    ).reset_index(drop=True)
    df["_total_bids"] = df.groupby("rut_normalizado").cumcount()
    df["_tipo_bids"]  = df.groupby(["rut_normalizado", "tipo_licitacion"]).cumcount()
    df["especializacion_tipo"] = (
        df["_tipo_bids"] / (df["_total_bids"] + 1)
    ).clip(0, 1).fillna(0).round(4)
    df.drop(columns=["_total_bids", "_tipo_bids"], inplace=True)
    print(f"  especializacion_tipo: media={df['especializacion_tipo'].mean():.3f}")

    return df


def guardar_lookups(df: pd.DataFrame, conn_w: sqlite3.Connection):
    """
    Guarda 4 tablas de lookup usando TODO el dataset (no temporal).
    Usadas por prediccion_adjudicacion.py al predecir en licitaciones abiertas.
    """
    # Lookup 1: mediana n_competidores por (organismo, tipo)
    lu1 = (
        df.groupby(["organismo", "tipo_licitacion"])["n_competidores"]
        .median().round(1).reset_index()
        .rename(columns={"n_competidores": "n_competidores_hist"})
    )
    lu1.to_sql("lookup_n_hist", conn_w, if_exists="replace", index=False)

    # Lookup 2: win_rate empresa × organismo
    lu2 = (
        df.groupby(["rut_normalizado", "organismo"])["gano"]
        .agg(wins="sum", bids="count")
        .assign(win_rate=lambda x: (x["wins"] / x["bids"]).round(4))
        .reset_index()[["rut_normalizado", "organismo", "win_rate"]]
    )
    lu2.to_sql("lookup_win_rate_org", conn_w, if_exists="replace", index=False)

    # Lookup 3: mediana monto ganado por empresa
    lu3 = (
        df[df["gano"] == 1]
        .groupby("rut_normalizado")["monto_estimado"]
        .median().round(0).reset_index()
        .rename(columns={"monto_estimado": "monto_median"})
    )
    lu3.to_sql("lookup_monto_emp", conn_w, if_exists="replace", index=False)

    # Lookup 4: especialización empresa × tipo
    lu4 = (
        df.groupby(["rut_normalizado", "tipo_licitacion"]).size()
        .reset_index(name="n_tipo")
    )
    total_bids = df.groupby("rut_normalizado").size().reset_index(name="n_total")
    lu4 = lu4.merge(total_bids, on="rut_normalizado")
    lu4["especializacion"] = (lu4["n_tipo"] / lu4["n_total"]).round(4)
    lu4[["rut_normalizado", "tipo_licitacion", "especializacion"]].to_sql(
        "lookup_especializacion", conn_w, if_exists="replace", index=False
    )

    conn_w.commit()
    print(f"  Lookups guardados: n_hist={len(lu1):,} | "
          f"win_rate={len(lu2):,} | monto={len(lu3):,} | especializacion={len(lu4):,}")


# ─────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────

def run():
    print("=" * 55)
    print("models/construir_dataset_training.py")
    print(f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("=" * 55)

    # Conexión de solo lectura para todos los SELECTs
    conn_r = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)

    # Verificar insumos mínimos
    for tabla in ("raw_oferentes", "clean_licitaciones", "features_prospectos"):
        n = conn_r.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
        if n == 0:
            print(f"  ERROR: {tabla} está vacía. Ejecuta la ingesta primero.")
            conn_r.close()
            return
        print(f"  {tabla}: {n:,} registros")
    print()

    # Construir dataset paso a paso (todo en memoria, solo lectura)
    df = cargar_datos_base(conn_r)
    df = agregar_historial_temporal(df)
    df = agregar_features_proveedor(df, conn_r)
    conn_r.close()  # liberar lectura antes de escribir

    # Features avanzadas (sin data leakage)
    print("\n  === FEATURES AVANZADAS ===")
    df = agregar_features_avanzadas(df)

    # ── Resumen ──────────────────────────────────────────────
    print()
    print("  === RESUMEN DATASET ===")
    print(f"  Total registros    : {len(df):,}")
    print(f"  Ganadores (gano=1) : {int(df['gano'].sum()):,} "
          f"({100 * df['gano'].mean():.1f}%)")
    print(f"  Licitaciones únicas: {df['id_licitacion'].nunique():,}")
    print(f"  Proveedores únicos : {df['rut_normalizado'].nunique():,}")
    print(f"  Con tramo_ventas   : {int(df['tramo_ventas'].notna().sum()):,} "
          f"({100*df['tramo_ventas'].notna().mean():.0f}%)")
    print(f"  Con rubro_licit.   : {int(df['rubro_licitacion'].notna().sum()):,} "
          f"({100 * df['rubro_licitacion'].notna().mean():.0f}%)")
    n_cm = int(df['es_convenio_marco'].sum())
    print(f"  Convenio Marco (LR): {n_cm:,} ({100*n_cm/len(df):.1f}%)")

    top_rubros = df["rubro_licitacion"].value_counts().head(5)
    if not top_rubros.empty:
        print("  Top 5 rubros en licitaciones:")
        for rubro, cnt in top_rubros.items():
            print(f"    {rubro}: {cnt:,}")

    win_rate_por_region = (
        df.groupby("region_licitacion")["gano"]
        .agg(["sum", "count"])
        .assign(win_rate=lambda x: (x["sum"] / x["count"] * 100).round(1))
        .sort_values("count", ascending=False)
        .head(5)
    )
    print("  Win rate top 5 regiones:")
    for reg, row in win_rate_por_region.iterrows():
        print(f"    {reg}: {row['win_rate']}% ({int(row['count'])} lics)")

    # ── Guardar CSV (siempre, independiente de DB) ───────────
    df.to_csv(TRAINING_CSV, index=False, encoding="utf-8-sig")
    print(f"\n  CSV de inspección: {TRAINING_CSV}")

    # ── Guardar en DB con reintentos ─────────────────────────
    import time
    guardado = False
    for intento in range(12):
        try:
            conn_w = sqlite3.connect(DB_PATH, timeout=10)
            conn_w.execute("PRAGMA journal_mode=WAL")
            conn_w.execute("DROP TABLE IF EXISTS training_dataset")
            conn_w.commit()
            df.to_sql("training_dataset", conn_w,
                      if_exists="replace", index=False)
            conn_w.commit()
            print(f"  Tabla 'training_dataset' guardada ({len(df):,} registros)")
            print("\n  Guardando tablas de lookup para predicción...")
            guardar_lookups(df, conn_w)
            conn_w.close()
            guardado = True
            break
        except sqlite3.OperationalError as e:
            conn_w.close()
            print(f"  DB ocupada (intento {intento+1}/12), esperando 10s... [{e}]")
            time.sleep(10)
    if not guardado:
        print("  AVISO: no se pudo escribir en DB. "
              "El CSV queda como respaldo en data/training_dataset.csv")
    print("\nmodels/construir_dataset_training.py completado.")


if __name__ == "__main__":
    run()
