# =============================================================
#  models/prediccion_adjudicacion.py
#
#  NOTA: Este modelo mejora significativamente con datos de
#  oferentes OCDS (ingesta/ocds_oferentes.py). Con la tabla
#  raw_oferentes completa predice quién ganará licitaciones
#  activas para contactar ANTES del resultado.
#
#  Inputs:  raw_oferentes, clean_licitaciones, features_prospectos
#  Outputs: predicciones_activas, data/modelo_adjudicacion.pkl
# =============================================================

import sys
import pickle
import logging
import numpy as np
import pandas as pd
import sqlite3
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import DB_PATH, DATA_DIR

logger = logging.getLogger(__name__)

FEATURES_MODELO = [
    # n_competidores_hist reemplaza n_competidores real (no disponible al predecir)
    # Usa mediana histórica por (organismo, tipo) → sin data leakage
    "n_competidores_hist",
    "tramo_ventas_num",
    "mismo_region",
    "licitaciones_previas_log",
    "win_rate_organismo",        # tasa victorias empresa × organismo (temporal)
    "match_monto",               # monto licitación en rango histórico empresa
    "especializacion_tipo",      # fracción bids empresa en este tipo
    "participaciones_org_log",   # log(bids previos empresa×organismo) — experiencia
    "es_convenio_marco",         # dinámica distinta en Convenios Marco
    "ratio_oc_licitacion",       # ratio OC/licitacion empresa — proxy de CM/suministro
    # ELIMINADO: tiene_capital_negativo — capital negativo no predice quién gana
    # una licitación; es señal de necesidad de factoring (scoring), no de capacidad
    # de ganar (predicción). Su inclusión generaba ruido en el modelo.
]

MODELO_PATH      = DATA_DIR / "modelo_adjudicacion.pkl"
MIN_REGISTROS    = 50


# ─────────────────────────────────────────────────────────────
# TRANSFORMACIÓN DE FEATURES
# ─────────────────────────────────────────────────────────────

def _aplicar_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Transforma columnas crudas en FEATURES_MODELO.
    Todas las features tienen defaults seguros para funcionar
    tanto en training como en predicción sobre licitaciones abiertas.
    """
    idx = df.index
    out = pd.DataFrame(index=idx)

    # 1. n_competidores_hist — usa columna histórica si existe,
    #    si no cae a n_competidores / n_oferentes (retrocompatibilidad)
    if "n_competidores_hist" in df.columns:
        n_src = df["n_competidores_hist"]
    elif "n_oferentes" in df.columns:
        n_src = df["n_oferentes"]
    else:
        n_src = df.get("n_competidores", pd.Series(7, index=idx))
    out["n_competidores_hist"] = pd.to_numeric(
        n_src, errors="coerce"
    ).fillna(7).clip(1, 100)

    # 2. tramo_ventas_num
    out["tramo_ventas_num"] = pd.to_numeric(
        df.get("tramo_ventas", pd.Series(0, index=idx)), errors="coerce"
    ).fillna(0)

    # 3. mismo_region
    reg_emp = df.get("region_empresa", pd.Series("", index=idx))
    reg_lit = df.get("region_licitacion", pd.Series("", index=idx))
    out["mismo_region"] = (
        reg_emp.fillna("").str.strip().str.upper() ==
        reg_lit.fillna("").str.strip().str.upper()
    ).astype(int)

    # 4. licitaciones_previas_log
    prev_col = (
        df["wins_previos"] if "wins_previos" in df.columns
        else df.get("licitaciones_ganadas", pd.Series(0, index=idx))
    )
    out["licitaciones_previas_log"] = np.log1p(
        pd.to_numeric(prev_col, errors="coerce").fillna(0).clip(0)
    )

    # 5. win_rate_organismo — default 0 si no disponible
    out["win_rate_organismo"] = pd.to_numeric(
        df.get("win_rate_organismo", pd.Series(0.0, index=idx)),
        errors="coerce"
    ).fillna(0).clip(0, 1)

    # 6. match_monto — default 0.5 (neutral) si no disponible
    out["match_monto"] = pd.to_numeric(
        df.get("match_monto", pd.Series(0.5, index=idx)),
        errors="coerce"
    ).fillna(0.5).clip(0, 1)

    # 7. especializacion_tipo — default 0 si no disponible
    out["especializacion_tipo"] = pd.to_numeric(
        df.get("especializacion_tipo", pd.Series(0.0, index=idx)),
        errors="coerce"
    ).fillna(0).clip(0, 1)

    # 8. participaciones_org_log — experiencia empresa×organismo
    # Cuántas veces ha participado la empresa con este organismo específico.
    # Más participaciones previas = mejor conocimiento del comprador = mayor P(win).
    out["participaciones_org_log"] = np.log1p(
        pd.to_numeric(
            df.get("participaciones_org", pd.Series(0, index=idx)),
            errors="coerce"
        ).fillna(0).clip(0)
    )

    # 9. es_convenio_marco — default 0
    out["es_convenio_marco"] = pd.to_numeric(
        df.get("es_convenio_marco", pd.Series(0, index=idx)),
        errors="coerce"
    ).fillna(0).astype(int)

    # 10. ratio_oc_licitacion — OC recibidas / licitaciones ganadas
    # Empresas con ratio alto ya tienen relación establecida (CM o suministro);
    # tienden a ganar con mayor consistencia en el mismo organismo.
    # Default 0 para proveedores sin historial en clean_proveedores.
    out["ratio_oc_licitacion"] = pd.to_numeric(
        df.get("ratio_oc_licitacion", pd.Series(0.0, index=idx)),
        errors="coerce"
    ).fillna(0).clip(0, 200)

    return out[FEATURES_MODELO]


# ─────────────────────────────────────────────────────────────
# PREPARAR DATASET DE ENTRENAMIENTO
# ─────────────────────────────────────────────────────────────

def preparar_dataset(conn: sqlite3.Connection) -> tuple:
    """
    Carga el dataset de entrenamiento en orden de prioridad:

    1. training_dataset — construido por construir_dataset_training.py.
       Label correcto (cruce RUT vs CSV), historial temporal sin leakage,
       rubro enriquecido desde clean_licitaciones.

    2. Fallback OCDS directo — raw_oferentes con label del campo adjudicado
       (menos fiable: ~35% de inconsistencia por licitaciones multi-item).

    3. Fallback sintético — ganadores históricos + negativos sintéticos.

    Retorna (X, y, modo).
    """
    # ── Prioridad 1: training_dataset ────────────────────────
    # Prefiere el CSV si existe y tiene más filas que la tabla DB
    # (el CSV siempre refleja la última ejecución de construir_dataset_training.py)
    csv_path = DATA_DIR / "training_dataset.csv"
    n_csv = 0
    if csv_path.exists():
        # Contar filas rápido (sin cargar todo)
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            n_csv = sum(1 for _ in f) - 1  # menos cabecera

    try:
        n_db = conn.execute(
            "SELECT COUNT(*) FROM training_dataset"
        ).fetchone()[0]
    except Exception:
        n_db = 0

    df_td = None
    fuente_td = None

    if n_csv >= MIN_REGISTROS and n_csv >= n_db:
        # CSV más actualizado — usarlo
        _raw = pd.read_csv(csv_path, low_memory=False)
        df_td = _raw.rename(columns={
            "n_competidores": "n_oferentes",
            "wins_previos":   "licitaciones_ganadas",
            "gano":           "adjudicado",
        })
        # participaciones_org: si no está en el CSV, usar wins_previos como proxy
        if "participaciones_org" not in df_td.columns:
            df_td["participaciones_org"] = df_td.get(
                "licitaciones_ganadas", pd.Series(0, index=df_td.index)
            )
        fuente_td = f"training_dataset_csv ({n_csv:,} filas)"
    elif n_db >= MIN_REGISTROS:
        df_td = pd.read_sql("""
            SELECT
                n_competidores      AS n_oferentes,
                region_empresa,
                region_licitacion,
                tramo_ventas,
                wins_previos        AS licitaciones_ganadas,
                COALESCE(wins_previos, 0) AS participaciones_org,
                gano                AS adjudicado
            FROM training_dataset
        """, conn)
        fuente_td = f"training_dataset_db ({n_db:,} filas)"

    if df_td is not None:
        print(f"  Fuente: {fuente_td}")
        X = _aplicar_features(df_td)
        y = df_td["adjudicado"].astype(int)
        return X, y, "training_dataset"

    # ── Prioridad 2: OCDS directo (label menos fiable) ──────
    try:
        n_ocds = conn.execute(
            "SELECT COUNT(*) FROM raw_oferentes"
        ).fetchone()[0]
    except Exception:
        n_ocds = 0

    if n_ocds >= MIN_REGISTROS:
        df_o = pd.read_sql("""
            SELECT
                o.n_competidores      AS n_oferentes,
                o.region_empresa,
                p.tramo_ventas,
                p.licitaciones_ganadas,
                l.regionunidad        AS region_licitacion,
                l.codigoorganismo     AS organismo,
                o.adjudicado,
                COUNT(*) OVER (
                    PARTITION BY o.rut_normalizado, l.codigoorganismo
                ) AS participaciones_org
            FROM raw_oferentes o
            LEFT JOIN features_prospectos p
                ON o.rut_normalizado = p.rut_normalizado
            LEFT JOIN clean_licitaciones l
                ON o.id_licitacion = l.codigoexterno
            WHERE o.adjudicado IS NOT NULL
        """, conn)
        X = _aplicar_features(df_o)
        y = pd.to_numeric(df_o["adjudicado"], errors="coerce").fillna(0).astype(int)
        return X, y, "ocds_directo"

    # ── Prioridad 3: fallback sintético ──────────────────────
    df_pos = pd.read_sql("""
        SELECT
            l.rut_proveedor_norm      AS rut_normalizado,
            COALESCE(l.numerooferentes, 3) AS n_oferentes,
            p.tramo_ventas,
            COALESCE(p.licitaciones_ganadas, 1) AS licitaciones_ganadas,
            COALESCE(p.licitaciones_ganadas, 1) AS participaciones_org,
            l.regionunidad            AS region_licitacion,
            'LOS LAGOS'               AS region_empresa,
            1                         AS adjudicado
        FROM clean_licitaciones l
        LEFT JOIN features_prospectos p
            ON l.rut_proveedor_norm = p.rut_normalizado
        WHERE l.rut_proveedor_norm IS NOT NULL
        AND l.monto_total_adjudicado > 0
    """, conn)

    df_neg = pd.read_sql("""
        SELECT
            p.rut_normalizado,
            3           AS n_oferentes,
            p.tramo_ventas,
            0           AS licitaciones_ganadas,
            0           AS participaciones_org,
            'LOS LAGOS' AS region_licitacion,
            'LOS LAGOS' AS region_empresa,
            0           AS adjudicado
        FROM features_prospectos p
        WHERE p.aparece_en_mp = 0
        LIMIT 500
    """, conn)

    df_train = pd.concat([df_pos, df_neg], ignore_index=True)
    X = _aplicar_features(df_train)
    y = df_train["adjudicado"].astype(int)
    return X, y, "fallback"


# ─────────────────────────────────────────────────────────────
# ENTRENAMIENTO
# ─────────────────────────────────────────────────────────────

def entrenar_modelo(conn: sqlite3.Connection):
    """
    Entrena LightGBM + calibración isotónica, imprime métricas y guarda pickle.

    Ventajas vs RandomForest:
    - Mejor AUC en datos tabulares desbalanceados
    - Más rápido con datasets grandes
    - CalibratedClassifierCV(isotonic) corrige miscalibración:
      P_win=45% → win rate real ~45% (antes ~20% con RF)
    """
    try:
        from lightgbm import LGBMClassifier
    except ImportError:
        print("  lightgbm no instalado — usando RandomForestClassifier como fallback")
        print("  Para instalar: pip install lightgbm")
        from sklearn.ensemble import RandomForestClassifier as LGBMClassifier
        _usa_lgbm = False
    else:
        _usa_lgbm = True

    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import classification_report, roc_auc_score

    print("\n  Preparando dataset...")
    X, y, modo = preparar_dataset(conn)
    print(f"  Registros: {len(X):,}  (modo={modo})")
    print(f"  Positivos: {int(y.sum()):,}  |  Negativos: {int((y==0).sum()):,}")

    if len(X) < 20:
        raise RuntimeError("Dataset demasiado pequeño para entrenar")

    stratify = y if y.nunique() > 1 else None
    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=stratify
    )

    if _usa_lgbm:
        # Escala desbalance: n_negativos / n_positivos
        n_pos = int(y_tr.sum())
        n_neg = int((y_tr == 0).sum())
        scale_pos = n_neg / max(n_pos, 1)

        base_clf = LGBMClassifier(
            n_estimators=500,
            max_depth=6,
            learning_rate=0.05,
            num_leaves=31,
            scale_pos_weight=scale_pos,   # maneja desbalance
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1,
            verbose=-1,
        )
    else:
        from sklearn.ensemble import RandomForestClassifier
        base_clf = RandomForestClassifier(
            n_estimators=200, max_depth=8,
            class_weight="balanced", random_state=42, n_jobs=-1,
        )

    # Calibración isotónica: corrige las probabilidades predichas
    # cv=5 usa cross-validation interna sobre X_tr para ajustar la curva de calibración
    clf = CalibratedClassifierCV(base_clf, method="isotonic", cv=5)
    clf.fit(X_tr, y_tr)

    y_pred = clf.predict(X_te)
    y_prob = clf.predict_proba(X_te)[:, 1]

    print("\n  Métricas (test set):")
    print(classification_report(y_te, y_pred, zero_division=0))
    if y_te.nunique() > 1:
        print(f"  AUC-ROC: {roc_auc_score(y_te, y_prob):.3f}")

    # Importancia de features (desde el primer estimador calibrado)
    print("\n  Importancia de features:")
    try:
        if _usa_lgbm:
            imp_arr = clf.calibrated_classifiers_[0].estimator.feature_importances_
        else:
            imp_arr = clf.calibrated_classifiers_[0].estimator.feature_importances_
        imp_total = imp_arr.sum() or 1.0
        for feat, imp in sorted(
            zip(FEATURES_MODELO, imp_arr / imp_total),
            key=lambda x: x[1], reverse=True
        ):
            bar = "█" * int(imp * 30)
            print(f"    {feat:30} {imp:.3f}  {bar}")
    except Exception:
        print("    (importancia no disponible con calibración)")

    tipo_modelo = "LightGBM+isotonic" if _usa_lgbm else "RandomForest+isotonic"
    MODELO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MODELO_PATH, "wb") as fh:
        pickle.dump({
            "modelo":   clf,
            "features": FEATURES_MODELO,
            "modo":     modo,
            "tipo":     tipo_modelo,
            "fecha":    datetime.now().isoformat(),
        }, fh)
    print(f"\n  Modelo guardado: {MODELO_PATH}  (tipo={tipo_modelo})")
    return clf


# ─────────────────────────────────────────────────────────────
# PREDICCIONES EN LICITACIONES ACTIVAS
# ─────────────────────────────────────────────────────────────

def _cargar_lookups(conn: sqlite3.Connection) -> dict:
    """
    Carga las tablas lookup generadas por construir_dataset_training.py.
    Retorna dict vacío si no existen (graceful degradation).
    """
    lookups = {}
    tablas = {
        "n_hist":         "lookup_n_hist",
        "win_rate":       "lookup_win_rate_org",
        "monto_emp":      "lookup_monto_emp",
        "especializacion":"lookup_especializacion",
    }
    for key, tabla in tablas.items():
        try:
            lookups[key] = pd.read_sql(f"SELECT * FROM {tabla}", conn)
        except Exception:
            lookups[key] = pd.DataFrame()
    return lookups


def _enriquecer_cross(df_cross: pd.DataFrame, lookups: dict) -> pd.DataFrame:
    """
    Enriquece el cross-join empresa×licitación con las 4 features avanzadas
    usando las lookup tables. Usa defaults seguros si la lookup está vacía.
    """
    df = df_cross.copy()

    # Normalizar tipo de 'organismo' en lookups para evitar error int/str en merge
    for key in list(lookups.keys()):
        lk = lookups[key]
        if not lk.empty and "organismo" in lk.columns:
            lookups[key] = lk.copy()
            lookups[key]["organismo"] = lookups[key]["organismo"].astype(str)
    if "organismo" in df.columns:
        df = df.copy()
        df["organismo"] = df["organismo"].astype(str)

    # 1. n_competidores_hist: mediana histórica por (organismo, tipo_licitacion)
    lk_n = lookups.get("n_hist", pd.DataFrame())
    if not lk_n.empty and "organismo" in df.columns and "tipo_licitacion" in df.columns:
        df = df.merge(lk_n, on=["organismo", "tipo_licitacion"], how="left")
    if "n_competidores_hist" not in df.columns:
        df["n_competidores_hist"] = 7  # default global

    # 2. win_rate_organismo: tasa de victorias empresa × organismo
    lk_wr = lookups.get("win_rate", pd.DataFrame())
    if not lk_wr.empty and "organismo" in df.columns:
        df = df.merge(lk_wr, on=["rut_normalizado", "organismo"], how="left")
    if "win_rate_organismo" not in df.columns:
        df["win_rate_organismo"] = 0.0

    # 3. match_monto: ¿el monto de la licitación cae en el rango histórico de la empresa?
    lk_m = lookups.get("monto_emp", pd.DataFrame())
    if not lk_m.empty and "monto_estimado" in df.columns:
        df = df.merge(lk_m, on="rut_normalizado", how="left")
        monto = pd.to_numeric(df["monto_estimado"], errors="coerce").fillna(0)
        lo  = pd.to_numeric(df.get("monto_min_hist",  pd.Series(0, index=df.index)), errors="coerce").fillna(0)
        hi  = pd.to_numeric(df.get("monto_max_hist",  pd.Series(0, index=df.index)), errors="coerce").fillna(0)
        rng = (hi - lo).clip(lower=1)
        df["match_monto"] = ((monto - lo) / rng).clip(0, 1).fillna(0.5)
        df.drop(columns=[c for c in ("monto_min_hist","monto_max_hist") if c in df.columns],
                inplace=True)
    if "match_monto" not in df.columns:
        df["match_monto"] = 0.5

    # 4. especializacion_tipo
    lk_e = lookups.get("especializacion", pd.DataFrame())
    if not lk_e.empty and "tipo_licitacion" in df.columns:
        df = df.merge(lk_e, on=["rut_normalizado", "tipo_licitacion"], how="left")
    if "especializacion_tipo" not in df.columns:
        df["especializacion_tipo"] = 0.0

    return df


def predecir_activas(conn: sqlite3.Connection, clf=None):
    """
    Cruza empresas conocidas × licitaciones activas y predice
    probabilidad de adjudicación por empresa.
    Usa lookup tables para enriquecer con features avanzadas sin leakage.
    Guarda resultado en la tabla predicciones_activas.
    """
    if clf is None:
        if not MODELO_PATH.exists():
            print("  Sin modelo guardado — saltando predicciones activas")
            return
        with open(MODELO_PATH, "rb") as fh:
            clf = pickle.load(fh)["modelo"]

    # Cargar lookups (generados por construir_dataset_training.py)
    lookups = _cargar_lookups(conn)
    n_lookups = sum(1 for v in lookups.values() if not v.empty)
    print(f"  Lookups cargadas: {n_lookups}/4 tablas disponibles")

    # Licitaciones con fecha de adjudicación futura
    try:
        df_act = pd.read_sql("""
            SELECT codigoexterno        AS codigo,
                   monto_total_adjudicado AS monto_estimado,
                   COALESCE(numerooferentes, 7) AS n_oferentes,
                   regionunidad         AS region_licitacion,
                   codigoorganismo      AS organismo,
                   tipo                 AS tipo_licitacion,
                   CASE WHEN tipo LIKE '%Convenio%' THEN 1 ELSE 0 END AS es_convenio_marco
            FROM clean_licitaciones
            WHERE fechaadjudicacion >= date('now')
            LIMIT 500
        """, conn)
    except Exception as exc:
        print(f"  Sin licitaciones activas: {exc}")
        return

    if df_act.empty:
        print("  Sin licitaciones activas para predecir")
        return

    # Empresas con historial en Mercado Público
    try:
        df_emp = pd.read_sql("""
            SELECT p.rut_normalizado, p.tramo_ventas,
                   p.tramo_capital_negativo, p.licitaciones_ganadas,
                   COALESCE(f.ratio_oc_licitacion, 0) AS ratio_oc_licitacion
            FROM prospectos_rankeados p
            LEFT JOIN features_prospectos f
                ON p.rut_normalizado = f.rut_normalizado
            WHERE p.nivel IN ('1 - Contactar hoy', '2 - Contactar esta semana')
            ORDER BY p.score DESC
            LIMIT 200
        """, conn)
    except Exception:
        df_emp = pd.read_sql("""
            SELECT rut_normalizado, tramo_ventas,
                   tramo_capital_negativo, licitaciones_ganadas,
                   COALESCE(ratio_oc_licitacion, 0) AS ratio_oc_licitacion
            FROM features_prospectos
            WHERE aparece_en_mp = 1
            LIMIT 200
        """, conn)

    if df_emp.empty:
        print("  Sin empresas para cruzar")
        return

    # Cross join: empresa × licitación activa
    df_cross = df_act.assign(_key=1).merge(
        df_emp.assign(_key=1), on="_key"
    ).drop(columns="_key")
    df_cross["region_empresa"] = "LOS LAGOS"
    # participaciones_org: usar licitaciones_ganadas como proxy de experiencia
    if "participaciones_org" not in df_cross.columns:
        df_cross["participaciones_org"] = df_cross.get(
            "licitaciones_ganadas", pd.Series(0, index=df_cross.index)
        )

    # Enriquecer con features avanzadas desde lookups
    df_cross = _enriquecer_cross(df_cross, lookups)

    X_pred = _aplicar_features(df_cross)
    df_cross["probabilidad_adjudicacion"] = (
        clf.predict_proba(X_pred)[:, 1] * 100
    ).round(1)

    # Probabilidad máxima por empresa
    df_pred = (
        df_cross
        .groupby("rut_normalizado")["probabilidad_adjudicacion"]
        .max()
        .reset_index()
    )

    df_pred.to_sql(
        "predicciones_activas", conn,
        if_exists="replace", index=False
    )
    conn.commit()
    print(f"  predicciones_activas: {len(df_pred):,} empresas")


# ─────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────

def run():
    print("=" * 55)
    print("models/prediccion_adjudicacion.py")
    print("NOTA: Con raw_oferentes (OCDS) el modelo mejora mucho.")
    print("Sin esos datos usa fallback con ganadores históricos.")
    print("=" * 55)

    conn = sqlite3.connect(DB_PATH)
    try:
        clf = entrenar_modelo(conn)
        predecir_activas(conn, clf)
        print("\nmodels/prediccion_adjudicacion.py completado.")
    except Exception as exc:
        print(f"\nERROR: {exc}")
        print("Pipeline continúa sin predicciones de adjudicación.")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
