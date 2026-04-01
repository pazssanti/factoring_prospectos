# =============================================================
#  ingesta/sii_nomina.py — Versión 3
#  Columnas confirmadas por inspección real:
#
#  nomina_sii_razon_social.txt:
#    RUT, DV, COD_SUBTIPO, RAZON_SOCIAL,
#    FECHA_INICIO_VIG, FECHA_TG_VIG
#
#  nomina_sii_actividades.txt:
#    RUT, DV, CODIGO ACTIVIDAD,
#    DESC. ACTIVIDAD ECONOMICA, FECHA,
#    AFECTA A IVA, CATEGORIA TRIBUTARIA
# =============================================================

import sys
import sqlite3
import pandas as pd
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import (
    DB_PATH, SII_TXT_PATH, SII_RAZON_SOCIAL,
    SII_ACTIVIDADES, SII_SEP, SII_ENCODING
)
from utils.helpers import normalizar_rut_serie


def leer_txt(path: Path, nombre: str) -> pd.DataFrame:
    """Lee TXT con fallback de encoding."""
    if not path.exists():
        print(f"  AVISO: no se encontró {path.name}")
        return pd.DataFrame()
    for enc in [SII_ENCODING, "latin-1", "utf-8", "cp1252"]:
        try:
            df = pd.read_csv(
                path, sep=SII_SEP, encoding=enc,
                low_memory=False, dtype=str
            )
            print(f"  {nombre}: {len(df):,} filas ({enc})")
            print(f"  Columnas: {df.columns.tolist()}")
            return df
        except UnicodeDecodeError:
            continue
        except Exception as e:
            print(f"  ERROR: {e}")
            return pd.DataFrame()
    return pd.DataFrame()



# ─────────────────────────────────────────────────────────────
# MAPA NÓMINA PRINCIPAL
# ─────────────────────────────────────────────────────────────
MAPA_PRINCIPAL = {
    "Año comercial":                    "anio_comercial",
    "RUT":                              "rut",
    "DV":                               "dv",
    "Razón social":                     "razon_social",
    "Tramo según ventas":               "tramo_ventas",
    "Número de trabajadores dependie":  "num_trabajadores",
    "Fecha inicio de actividades vige": "fecha_inicio_actividades",
    "Fecha término de giro":            "fecha_termino_giro",
    "Fecha primera inscripción de ac":  "fecha_primera_inscripcion",
    "Tipo término de giro":             "tipo_termino_giro",
    "Tipo de contribuyente":            "tipo_contribuyente",
    "Subtipo de contribuyente":         "subtipo_contribuyente",
    "Tramo capital propio positivo":    "tramo_capital_positivo",
    "Tramo capital propio negativo":    "tramo_capital_negativo",
    "Rubro económico":                  "rubro_economico",
    "Subrubro económico":               "subrubro_economico",
    "Actividad económica":              "actividad_economica",
    "Región":                           "region",
    "Provincia":                        "provincia",
    "Comuna":                           "comuna",
    "R_PRESUNTA":                       "r_presunta",
    "OTROS_REGIMENES":                  "otros_regimenes",
}


def cargar_principal() -> pd.DataFrame:
    print("\n[1/3] Nómina principal 2020-2024")
    df = leer_txt(SII_TXT_PATH, "nomina_sii.txt")
    if df.empty:
        return df
    df = df.rename(columns=MAPA_PRINCIPAL)
    df["rut_normalizado"] = normalizar_rut_serie(df["rut"], df["dv"])
    return df


def cargar_razon_social() -> pd.DataFrame:
    """
    Columnas confirmadas:
    RUT, DV, COD_SUBTIPO, RAZON_SOCIAL,
    FECHA_INICIO_VIG, FECHA_TG_VIG
    FECHA_TG_VIG = Fecha Término Giro Vigente
    Vacío = empresa vigente / con valor = empresa cerrada
    """
    print("\n[2/3] Nómina razón social (feb 2026)")
    df = leer_txt(SII_RAZON_SOCIAL, "nomina_sii_razon_social.txt")
    if df.empty:
        return df

    # Normalizar nombres de columnas
    df.columns = [c.strip().upper() for c in df.columns]

    df["rut_normalizado"] = normalizar_rut_serie(
        df["RUT"], df["DV"]
    )

    # FECHA_TG_VIG = término de giro
    # Vacío = vigente, con fecha = cerrada
    if "FECHA_TG_VIG" in df.columns:
        df["vigente_2026"] = (
            df["FECHA_TG_VIG"].isna() |
            (df["FECHA_TG_VIG"].astype(str).str.strip() == "") |
            (df["FECHA_TG_VIG"].astype(str).str.strip() == "nan")
        ).astype(int)
        n_vig  = df["vigente_2026"].sum()
        n_cerr = len(df) - n_vig
        print(f"  Vigentes feb 2026: {n_vig:,}")
        print(f"  Cerradas:          {n_cerr:,}")
    else:
        print("  AVISO: no se encontró FECHA_TG_VIG")
        df["vigente_2026"] = 1

    cols_keep = ["rut_normalizado", "vigente_2026"]

    if "RAZON_SOCIAL" in df.columns:
        df = df.rename(columns={"RAZON_SOCIAL": "razon_social_2026"})
        cols_keep.append("razon_social_2026")

    if "FECHA_INICIO_VIG" in df.columns:
        df = df.rename(columns={"FECHA_INICIO_VIG": "fecha_inicio_2026"})
        cols_keep.append("fecha_inicio_2026")

    if "FECHA_TG_VIG" in df.columns:
        df = df.rename(columns={"FECHA_TG_VIG": "fecha_termino_2026"})
        cols_keep.append("fecha_termino_2026")

    return df[cols_keep].drop_duplicates("rut_normalizado")


def cargar_actividades() -> pd.DataFrame:
    """
    Columnas confirmadas:
    RUT, DV, CODIGO ACTIVIDAD, DESC. ACTIVIDAD ECONOMICA,
    FECHA, AFECTA A IVA, CATEGORIA TRIBUTARIA
    """
    print("\n[3/3] Nómina actividades económicas (feb 2026)")
    df = leer_txt(SII_ACTIVIDADES, "nomina_sii_actividades.txt")
    if df.empty:
        return df

    # Limpiar nombres: mayúsculas, sin puntos ni espacios extra
    df.columns = [
        c.strip().upper()
         .replace(".", "")
         .replace(" ", "_")
        for c in df.columns
    ]
    print(f"  Columnas normalizadas: {df.columns.tolist()}")

    if "RUT" not in df.columns:
        print("  AVISO: no se encontró columna RUT")
        return pd.DataFrame()

    df["rut_normalizado"] = normalizar_rut_serie(
        df["RUT"],
        df["DV"] if "DV" in df.columns else None
    )

    cols_keep = ["rut_normalizado"]

    # DESC_ACTIVIDAD_ECONOMICA (sin el punto del original)
    col_desc = next(
        (c for c in df.columns if "DESC" in c and "ACTIVIDAD" in c),
        None
    )
    if col_desc:
        df = df.rename(columns={col_desc: "actividad_2026"})
        cols_keep.append("actividad_2026")

    # CODIGO_ACTIVIDAD
    col_cod = next(
        (c for c in df.columns if "CODIGO" in c and "ACTIVIDAD" in c),
        None
    )
    if col_cod:
        df = df.rename(columns={col_cod: "codigo_actividad_2026"})
        cols_keep.append("codigo_actividad_2026")

    # CATEGORIA_TRIBUTARIA
    if "CATEGORIA_TRIBUTARIA" in df.columns:
        cols_keep.append("CATEGORIA_TRIBUTARIA")
        df = df.rename(columns={
            "CATEGORIA_TRIBUTARIA": "categoria_tributaria"
        })
        cols_keep[-1] = "categoria_tributaria"

    df_out = df[cols_keep].drop_duplicates("rut_normalizado")
    print(f"  RUTs únicos: {len(df_out):,}")
    return df_out


# ─────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────

def run():
    print("=" * 55)
    print("ingesta/sii_nomina.py — Versión 3")
    print("Tres archivos SII — columnas confirmadas")
    print("=" * 55)

    df_principal   = cargar_principal()
    df_razon       = cargar_razon_social()
    df_actividades = cargar_actividades()

    if df_principal.empty:
        print("\nERROR: no se pudo cargar nómina principal")
        return

    # ── Cruzar con razón social feb 2026 ─────────────────────
    print("\nCruzando archivos...")
    if not df_razon.empty:
        df = df_principal.merge(
            df_razon, on="rut_normalizado", how="left"
        )
        df["vigente_2026"] = df["vigente_2026"].fillna(1).astype(int)
        print(f"  Cruce razón social: {len(df):,} filas")
    else:
        df = df_principal.copy()
        df["vigente_2026"] = 1

    # ── Cruzar con actividades feb 2026 ──────────────────────
    if not df_actividades.empty:
        df = df.merge(
            df_actividades, on="rut_normalizado", how="left"
        )
        print(f"  Cruce actividades:  {len(df):,} filas")

    df["fecha_carga"] = datetime.now().isoformat()

    # ── Estadísticas ─────────────────────────────────────────
    print(f"\nTotal empresas: {len(df):,}")

    if "region" in df.columns:
        lagos = df[df["region"].str.contains(
            "LAGOS", na=False, case=False
        )]
        print(f"Empresas Los Lagos: {len(lagos):,}")

        if "vigente_2026" in df.columns:
            vig  = lagos[lagos["vigente_2026"] == 1]
            cerr = lagos[lagos["vigente_2026"] == 0]
            print(f"  Vigentes feb 2026: {len(vig):,}")
            print(f"  Cerradas:          {len(cerr):,}")

    if "tramo_ventas" in df.columns:
        print("\nTramos ventas (todo Chile):")
        print(df["tramo_ventas"].value_counts()
              .head(10).to_string())

    # ── Guardar ───────────────────────────────────────────────
    conn = sqlite3.connect(DB_PATH)
    df.to_sql("raw_empresas_sii", conn,
              if_exists="replace", index=False)
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) FROM raw_empresas_sii"
    ).fetchone()[0]
    print(f"\nraw_empresas_sii guardada: {n:,} registros")
    conn.close()
    print("\ningesta/sii_nomina.py completado.")


if __name__ == "__main__":
    run()