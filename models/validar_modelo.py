# =============================================================
#  models/validar_modelo.py
#
#  Propósito: Validación temporal del modelo de predicción de
#  adjudicación. Entrena con licitaciones históricas y evalúa
#  en licitaciones futuras (out-of-time), que es el escenario
#  real de uso del modelo.
#
#  Diferencia vs train_test_split random:
#    - Split aleatorio sobreestima el AUC porque mezcla años
#    - Aquí el modelo nunca ve datos de 2025/2026 en entrenamiento
#    - Refleja la pregunta real: ¿predice bien licitaciones futuras?
#
#  Inputs:  training_dataset (tabla SQLite), modelo_adjudicacion.pkl
#  Outputs: imprime reporte completo de métricas
#           data/validacion_temporal.csv (predicciones del test)
#
#  Uso:
#    python models/validar_modelo.py
# =============================================================

import sys
import pickle
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import DB_PATH, DATA_DIR
from models.prediccion_adjudicacion import _aplicar_features, FEATURES_MODELO

VALIDACION_CSV = DATA_DIR / "validacion_temporal.csv"

# Fecha de corte: train = hasta fin de 2024, test = 2025 en adelante
FECHA_CORTE = "2025-01-01"


# ─────────────────────────────────────────────────────────────
# CARGA Y SPLIT TEMPORAL
# ─────────────────────────────────────────────────────────────

def cargar_y_dividir(conn: sqlite3.Connection):
    """
    Carga training_dataset y divide por fecha:
    - Train: fechaadjudicacion < FECHA_CORTE
    - Test:  fechaadjudicacion >= FECHA_CORTE
    """
    df = pd.read_sql("""
        SELECT
            id_licitacion,
            rut_normalizado,
            n_competidores      AS n_oferentes,
            n_competidores_hist,
            region_empresa,
            region_licitacion,
            tramo_ventas,
            tramo_capital_negativo,
            wins_previos        AS licitaciones_ganadas,
            win_rate_organismo,
            match_monto,
            especializacion_tipo,
            es_convenio_marco,
            gano                AS adjudicado,
            fechaadjudicacion
        FROM training_dataset
    """, conn)

    df["fechaadjudicacion"] = pd.to_datetime(df["fechaadjudicacion"], errors="coerce")
    corte = pd.Timestamp(FECHA_CORTE)

    df_train = df[df["fechaadjudicacion"] < corte].copy()
    df_test  = df[df["fechaadjudicacion"] >= corte].copy()

    print(f"  Split temporal: corte = {FECHA_CORTE}")
    print(f"  Train: {len(df_train):,} registros "
          f"({df_train['adjudicado'].mean():.1%} ganadores) "
          f"| {df_train['fechaadjudicacion'].min().date()} → "
          f"{df_train['fechaadjudicacion'].max().date()}")
    print(f"  Test : {len(df_test):,} registros "
          f"({df_test['adjudicado'].mean():.1%} ganadores) "
          f"| {df_test['fechaadjudicacion'].min().date()} → "
          f"{df_test['fechaadjudicacion'].max().date()}")
    print(f"  Licitaciones test : {df_test['id_licitacion'].nunique():,}")
    print(f"  Providers nuevos  : "
          f"{len(set(df_test['rut_normalizado']) - set(df_train['rut_normalizado'])):,} "
          f"(no vistos en train)")

    return df_train, df_test


# ─────────────────────────────────────────────────────────────
# ENTRENAMIENTO SOBRE SPLIT TEMPORAL
# ─────────────────────────────────────────────────────────────

def entrenar_split(df_train: pd.DataFrame):
    from sklearn.ensemble import RandomForestClassifier

    X_tr = _aplicar_features(df_train)
    y_tr = df_train["adjudicado"].astype(int)

    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=8,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    clf.fit(X_tr, y_tr)
    return clf, X_tr, y_tr


# ─────────────────────────────────────────────────────────────
# MÉTRICAS Y REPORTE
# ─────────────────────────────────────────────────────────────

def evaluar(clf, df_test: pd.DataFrame) -> pd.DataFrame:
    from sklearn.metrics import (
        classification_report, roc_auc_score,
        precision_recall_curve, average_precision_score,
        confusion_matrix,
    )

    X_te = _aplicar_features(df_test)
    y_te = df_test["adjudicado"].astype(int)
    y_pred = clf.predict(X_te)
    y_prob = clf.predict_proba(X_te)[:, 1]

    print("\n  === MÉTRICAS EN TEST OUT-OF-TIME (2025+) ===")
    print(classification_report(y_te, y_pred, zero_division=0,
                                target_names=["perdedor", "ganador"]))

    auc = roc_auc_score(y_te, y_prob)
    ap  = average_precision_score(y_te, y_prob)
    print(f"  AUC-ROC           : {auc:.4f}")
    print(f"  Average Precision : {ap:.4f}  (útil con clases desbalanceadas)")

    cm = confusion_matrix(y_te, y_pred)
    print(f"\n  Matriz de confusión:")
    print(f"              Pred=0    Pred=1")
    print(f"  Real=0   {cm[0,0]:>7,}  {cm[0,1]:>7,}  (perdedores)")
    print(f"  Real=1   {cm[1,0]:>7,}  {cm[1,1]:>7,}  (ganadores)")

    # Calibración: ¿a qué umbral está el modelo bien calibrado?
    print("\n  Distribución de probabilidades predichas (ganadores reales vs perdedores):")
    for lim, lbl in [(0.1, "P<10%"), (0.2, "10-20%"), (0.3, "20-30%"),
                      (0.4, "30-40%"), (0.5, "40-50%"), (1.0, "P≥50%")]:
        pass  # calculado abajo

    df_test = df_test.copy()
    df_test["P_win"] = (y_prob * 100).round(1)
    df_test["pred_gano"] = y_pred

    bins = [0, 10, 20, 30, 40, 50, 100]
    labels = ["0-10%", "10-20%", "20-30%", "30-40%", "40-50%", "50-100%"]
    df_test["tramo_prob"] = pd.cut(df_test["P_win"], bins=bins, labels=labels, right=True)
    calibracion = (
        df_test.groupby("tramo_prob", observed=True)["adjudicado"]
        .agg(["sum", "count"])
        .assign(win_rate_real=lambda x: (x["sum"] / x["count"] * 100).round(1))
    )
    print("\n  Calibración (¿coincide P_win con tasa real de victorias?):")
    print(f"  {'Rango P_win':12} | {'Registros':>10} | {'Ganadores':>10} | {'Win rate real':>13}")
    print(f"  {'-'*12}-+-{'-'*10}-+-{'-'*10}-+-{'-'*13}")
    for tramo, row in calibracion.iterrows():
        bar = "█" * int(row["win_rate_real"] / 5)
        print(f"  {str(tramo):12} | {int(row['count']):>10,} | "
              f"{int(row['sum']):>10,} | {row['win_rate_real']:>11.1f}% {bar}")

    # Performance por año-mes (2025/2026)
    df_test["anio_mes"] = df_test["fechaadjudicacion"].dt.to_period("M")
    perf_mensual = (
        df_test.groupby("anio_mes")
        .apply(lambda g: pd.Series({
            "n": len(g),
            "ganadores": int(g["adjudicado"].sum()),
            "auc": roc_auc_score(g["adjudicado"], g["P_win"])
                  if g["adjudicado"].nunique() > 1 else float("nan"),
        }), include_groups=False)
        .dropna()
    )
    print("\n  Performance mensual en test (2025+):")
    print(f"  {'Mes':>8} | {'N':>6} | {'Ganadores':>9} | {'AUC':>6}")
    print(f"  {'-'*8}-+-{'-'*6}-+-{'-'*9}-+-{'-'*6}")
    for mes, row in perf_mensual.iterrows():
        auc_str = f"{row['auc']:.3f}" if not np.isnan(row['auc']) else "  n/a"
        print(f"  {str(mes):>8} | {int(row['n']):>6,} | {int(row['ganadores']):>9,} | {auc_str}")

    return df_test


# ─────────────────────────────────────────────────────────────
# COMPARAR CON MODELO PRODUCCIÓN (random split)
# ─────────────────────────────────────────────────────────────

def comparar_con_produccion(conn: sqlite3.Connection, df_test: pd.DataFrame):
    """Evalúa el modelo de producción (.pkl) sobre el mismo test set."""
    from sklearn.metrics import roc_auc_score, average_precision_score
    from config import DATA_DIR

    pkl_path = DATA_DIR / "modelo_adjudicacion.pkl"
    if not pkl_path.exists():
        print("\n  (sin modelo .pkl para comparar)")
        return

    with open(pkl_path, "rb") as fh:
        paquete = pickle.load(fh)
    clf_prod = paquete["modelo"]

    X_te = _aplicar_features(df_test)
    y_te = df_test["adjudicado"].astype(int)
    y_prob_prod = clf_prod.predict_proba(X_te)[:, 1]

    auc_prod = roc_auc_score(y_te, y_prob_prod)
    ap_prod  = average_precision_score(y_te, y_prob_prod)

    print(f"\n  === COMPARATIVA ===")
    print(f"  {'Modelo':25} | {'AUC-ROC':>8} | {'Avg Precision':>14} | Notas")
    print(f"  {'-'*25}-+-{'-'*8}-+-{'-'*14}-+--------")
    print(f"  {'Producción (random split)':25} | {auc_prod:>8.4f} | {ap_prod:>14.4f} | "
          f"entrenado en {paquete.get('modo','?')} ({paquete.get('fecha','?')[:10]})")

    # AUC del modelo temporal ya fue impreso arriba — recordatorio
    print(f"  {'Temporal (train<2025)':25} | (ver sección anterior)")
    print(f"\n  Interpretación:")
    print(f"  AUC ~0.5 = aleatorio | ~0.7 = aceptable | ~0.8 = bueno | ~0.9+ = excelente")


# ─────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────

def run():
    print("=" * 60)
    print("models/validar_modelo.py — Validación temporal")
    print(f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("=" * 60)

    conn = sqlite3.connect(DB_PATH)

    try:
        n = conn.execute("SELECT COUNT(*) FROM training_dataset").fetchone()[0]
    except Exception:
        print("  ERROR: tabla training_dataset no existe.")
        print("  Ejecuta primero: python models/construir_dataset_training.py")
        conn.close()
        return

    print(f"  training_dataset: {n:,} registros\n")

    # 1. Split temporal
    df_train, df_test = cargar_y_dividir(conn)

    if len(df_test) < 100:
        print("  Test set muy pequeño para métricas confiables.")
        conn.close()
        return

    # 2. Entrenar sobre train
    print("\n  Entrenando modelo (solo datos < 2025)...")
    clf_temporal, X_tr, y_tr = entrenar_split(df_train)

    print(f"  Train — positivos: {int(y_tr.sum()):,} | negativos: {int((y_tr==0).sum()):,}")

    print("\n  Importancia de features (modelo temporal):")
    for feat, imp in sorted(
        zip(FEATURES_MODELO, clf_temporal.feature_importances_),
        key=lambda x: x[1], reverse=True
    ):
        bar = "█" * int(imp * 40)
        print(f"    {feat:30} {imp:.3f} {bar}")

    # 3. Evaluar en test (2025+) — datos nunca vistos
    df_test_pred = evaluar(clf_temporal, df_test)

    # 4. Comparar con modelo de producción
    comparar_con_produccion(conn, df_test)

    # 5. Guardar predicciones del test
    df_test_pred.to_csv(VALIDACION_CSV, index=False, encoding="utf-8-sig")
    print(f"\n  Predicciones del test guardadas: {VALIDACION_CSV}")

    conn.close()
    print("\nmodels/validar_modelo.py completado.")


if __name__ == "__main__":
    run()
