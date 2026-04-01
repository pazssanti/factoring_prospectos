# =============================================================
#  ingesta/enriquecer_licitaciones_activas.py
#
#  Enriquece raw_licitaciones_activas con datos de detalle
#  (región, organismo, monto) y filtra las de Los Lagos.
#  Luego cruza con empresas conocidas para predecir ganadores.
#
#  Inputs:  raw_licitaciones_activas (cargada por mercadopublico_api.py activas)
#  Outputs: licitaciones_activas_lagos  — licitaciones abiertas en Los Lagos
#           predicciones_activas_lagos  — empresas × licitación con P(win)
#
#  Uso:
#    python ingesta/enriquecer_licitaciones_activas.py
# =============================================================

import sys
import time
import sqlite3
import pickle
import pandas as pd
import requests
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import TICKET_API, API_BASE_URL, DB_PATH, DATA_DIR

MODELO_PATH = DATA_DIR / "modelo_adjudicacion.pkl"


# ─────────────────────────────────────────────────────────────
# DETALLE LICITACION
# ─────────────────────────────────────────────────────────────

def get_detalle_licitacion(codigo: str) -> dict | None:
    try:
        r = requests.get(
            f"{API_BASE_URL}/licitaciones.json",
            params={"ticket": TICKET_API, "codigo": codigo},
            timeout=20,
        )
        if r.status_code == 200:
            listado = r.json().get("Listado", [])
            return listado[0] if listado else None
    except Exception:
        pass
    return None


def parsear_detalle(item: dict) -> dict:
    comp = item.get("Comprador") or {}
    return {
        "codigo_externo":    item.get("CodigoExterno"),
        "nombre":            item.get("Nombre"),
        "estado":            item.get("Estado"),
        "fecha_cierre":      item.get("FechaCierre"),
        "monto_estimado":    item.get("MontoEstimado"),
        "tipo":              item.get("Tipo"),
        "codigo_organismo":  comp.get("CodigoOrganismo"),
        "nombre_organismo":  comp.get("NombreOrganismo"),
        "nombre_unidad":     comp.get("NombreUnidad"),
        "region":            comp.get("RegionUnidad"),
        "comuna":            comp.get("ComunaUnidad"),
        "dias_cierre":       item.get("DiasCierreLicitacion"),
        "fecha_extraccion":  datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────
# DESCARGAR Y FILTRAR LOS LAGOS
# ─────────────────────────────────────────────────────────────

def descargar_detalle_activas(conn: sqlite3.Connection) -> pd.DataFrame:
    codigos = [
        r[0] for r in conn.execute(
            "SELECT codigo_externo FROM raw_licitaciones_activas"
        ).fetchall()
    ]

    if not codigos:
        print("  raw_licitaciones_activas esta vacia. Ejecuta primero:")
        print("  python ingesta/mercadopublico_api.py activas")
        return pd.DataFrame()

    print(f"  Descargando detalle de {len(codigos)} licitaciones activas...")
    registros = []
    ok = 0
    err = 0

    for i, codigo in enumerate(codigos, 1):
        detalle = get_detalle_licitacion(codigo)
        if detalle:
            registros.append(parsear_detalle(detalle))
            ok += 1
        else:
            err += 1

        if i % 50 == 0:
            pct = round(100 * i / len(codigos), 1)
            print(f"    [{pct}%] {i}/{len(codigos)} — OK:{ok} ERR:{err}")

        time.sleep(0.3)

    print(f"  Completado: {ok} ok, {err} errores")
    return pd.DataFrame(registros)


def filtrar_lagos(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    mask = df["region"].fillna("").str.upper().str.contains("LAGO")
    df_lagos = df[mask].copy()
    print(f"  Licitaciones activas en Los Lagos: {len(df_lagos)}")
    return df_lagos


# ─────────────────────────────────────────────────────────────
# PREDECIR GANADORES PARA LICITACIONES DE LOS LAGOS
# ─────────────────────────────────────────────────────────────

def predecir_ganadores_lagos(df_lagos: pd.DataFrame,
                              conn: sqlite3.Connection) -> pd.DataFrame:
    if df_lagos.empty:
        print("  Sin licitaciones Lagos para predecir")
        return pd.DataFrame()

    if not MODELO_PATH.exists():
        print("  Sin modelo guardado. Ejecuta primero:")
        print("  python models/prediccion_adjudicacion.py")
        return pd.DataFrame()

    with open(MODELO_PATH, "rb") as fh:
        paquete = pickle.load(fh)
    clf = paquete["modelo"]

    # Empresas con historial relevante
    try:
        df_emp = pd.read_sql("""
            SELECT rut_normalizado, razon_social, tramo_ventas,
                   tramo_capital_negativo, licitaciones_ganadas,
                   score, nivel
            FROM prospectos_rankeados
            WHERE nivel IN ('1 - Contactar hoy', '2 - Contactar esta semana')
            ORDER BY score DESC
            LIMIT 300
        """, conn)
    except Exception:
        df_emp = pd.read_sql("""
            SELECT p.rut_normalizado,
                   s.razon_social,
                   p.tramo_ventas,
                   p.tramo_capital_negativo,
                   p.licitaciones_ganadas,
                   NULL AS score,
                   NULL AS nivel
            FROM features_prospectos p
            LEFT JOIN raw_empresas_sii s ON p.rut_normalizado = s.rut_normalizado
            WHERE p.aparece_en_mp = 1
            LIMIT 300
        """, conn)

    if df_emp.empty:
        print("  Sin empresas para cruzar")
        return pd.DataFrame()

    # Cargar lookup tables para features avanzadas (sin data leakage)
    from models.prediccion_adjudicacion import _aplicar_features, _cargar_lookups, _enriquecer_cross
    lookups = _cargar_lookups(conn)
    n_lookups = sum(1 for v in lookups.values() if not v.empty)
    print(f"  Lookups disponibles: {n_lookups}/4")

    # Preparar licitaciones para cross join
    df_lit = df_lagos[["codigo_externo", "nombre", "nombre_organismo",
                        "codigo_organismo", "tipo",
                        "monto_estimado", "fecha_cierre", "dias_cierre",
                        "region", "comuna"]].copy()
    df_lit.rename(columns={
        "codigo_organismo": "organismo",
        "tipo":             "tipo_licitacion",
    }, inplace=True)
    df_lit["organismo"] = df_lit["organismo"].astype(str)
    df_lit["n_oferentes"] = 7  # default
    df_lit["es_convenio_marco"] = df_lit["tipo_licitacion"].fillna("").str.contains(
        "Convenio", case=False).astype(int)

    # Enriquecer n_competidores_hist desde lookup si está disponible
    lk_n = lookups.get("n_hist", pd.DataFrame())
    if not lk_n.empty:
        lk_n = lk_n.copy()
        lk_n["organismo"] = lk_n["organismo"].astype(str)
        df_lit = df_lit.merge(lk_n, on=["organismo", "tipo_licitacion"], how="left")
    df_lit["n_competidores_hist"] = df_lit.get(
        "n_competidores_hist", pd.Series(7.0, index=df_lit.index)
    ).fillna(7)

    # Intentar obtener n_oferentes reales desde OCDS si existe
    try:
        ocds_n = pd.read_sql("""
            SELECT id_licitacion, COUNT(*) AS n_oferentes
            FROM raw_oferentes GROUP BY id_licitacion
        """, conn)
        df_lit = df_lit.merge(
            ocds_n.rename(columns={"id_licitacion": "codigo_externo"}),
            on="codigo_externo", how="left", suffixes=("_def", "")
        )
        df_lit["n_oferentes"] = df_lit["n_oferentes"].fillna(7)
        if "n_oferentes_def" in df_lit.columns:
            df_lit.drop(columns=["n_oferentes_def"], inplace=True)
    except Exception:
        pass

    # Cross join empresa × licitacion
    df_cross = df_lit.assign(_k=1).merge(
        df_emp.assign(_k=1), on="_k"
    ).drop(columns="_k")

    df_cross["region_empresa"] = "LOS LAGOS"
    df_cross["region_licitacion"] = df_cross["region"].fillna("LOS LAGOS")

    # Enriquecer con win_rate, match_monto, especializacion desde lookups
    df_cross = _enriquecer_cross(df_cross, lookups)

    # Aplicar features del modelo
    X = _aplicar_features(df_cross)
    df_cross["P_win"] = (clf.predict_proba(X)[:, 1] * 100).round(1)

    # Mejor licitacion por empresa
    resultado = (
        df_cross
        .sort_values("P_win", ascending=False)
        .groupby("rut_normalizado")
        .agg(
            razon_social         =("razon_social", "first"),
            mejor_licitacion     =("nombre", "first"),
            codigo_licitacion    =("codigo_externo", "first"),
            organismo            =("nombre_organismo", "first"),
            monto_estimado_MM    =("monto_estimado",
                                   lambda x: round(float(x.iloc[0] or 0) / 1e6, 1)),
            fecha_cierre         =("fecha_cierre", "first"),
            dias_para_cierre     =("dias_cierre", "first"),
            P_win                =("P_win", "max"),
            score_prospecto      =("score", "first"),
            nivel_prospecto      =("nivel", "first"),
        )
        .reset_index()
        .sort_values("P_win", ascending=False)
    )

    resultado["accion"] = resultado.apply(
        lambda r: "CONTACTAR HOY" if r["P_win"] >= 80 and (r["dias_para_cierre"] or 99) <= 3
        else ("CONTACTAR ESTA SEMANA" if r["P_win"] >= 60
              else "MONITOREAR"),
        axis=1
    )

    print(f"  Predicciones generadas: {len(resultado)} empresas")
    return resultado


# ─────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────

def run():
    print("=" * 55)
    print("ingesta/enriquecer_licitaciones_activas.py")
    print(f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("=" * 55)

    conn = sqlite3.connect(DB_PATH)

    # 1. Descargar detalle y filtrar Lagos
    df_todas = descargar_detalle_activas(conn)
    if df_todas.empty:
        conn.close()
        return

    df_lagos = filtrar_lagos(df_todas)

    # 2. Guardar en DB
    df_todas.to_sql("licitaciones_activas_detalle", conn,
                    if_exists="replace", index=False)
    conn.commit()
    print(f"  licitaciones_activas_detalle: {len(df_todas)} registros guardados")

    if not df_lagos.empty:
        df_lagos.to_sql("licitaciones_activas_lagos", conn,
                        if_exists="replace", index=False)
        conn.commit()
        print(f"  licitaciones_activas_lagos: {len(df_lagos)} registros")

        # Mostrar resumen
        print("\n  Licitaciones abiertas en Los Lagos:")
        for _, row in df_lagos.iterrows():
            monto = row.get("monto_estimado")
            monto_str = f"${float(monto)/1e6:.1f}M" if monto else "monto N/D"
            print(f"    {row['codigo_externo']} | {row['nombre_organismo']} | "
                  f"{monto_str} | cierre: {str(row['fecha_cierre'])[:10]}")

        # 3. Predecir ganadores
        print("\n  Prediccion de ganadores para licitaciones Lagos...")
        df_pred = predecir_ganadores_lagos(df_lagos, conn)

        if not df_pred.empty:
            df_pred.to_sql("predicciones_licitaciones_lagos", conn,
                           if_exists="replace", index=False)
            conn.commit()
            print(f"  predicciones_licitaciones_lagos: {len(df_pred)} empresas")

            print("\n  TOP 10 empresas con mayor probabilidad de ganar:")
            top = df_pred[df_pred["P_win"] >= 60].head(10)
            for _, r in top.iterrows():
                print(f"    {r['razon_social'] or r['rut_normalizado'][:12]} | "
                      f"P={r['P_win']}% | ${r['monto_estimado_MM']}M | "
                      f"{r['accion']} | cierre: {str(r['fecha_cierre'])[:10]}")

    conn.close()
    print("\ningesta/enriquecer_licitaciones_activas.py completado.")


if __name__ == "__main__":
    run()
