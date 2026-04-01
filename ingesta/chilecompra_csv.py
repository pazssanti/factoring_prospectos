# =============================================================
#  ingesta/chilecompra_csv.py
#
#  Lee los ZIPs de datos.chilecompra.cl, extrae los CSV,
#  filtra SOLO registros de la Región de Los Lagos durante
#  la lectura (nunca carga todo Chile en memoria),
#  y guarda en SQLite mes a mes.
#
#  Separador confirmado: ";"
#  Columnas región confirmadas:
#    licitaciones  → "regionunidad" y/o "region_proveedor"
#    ordenes_compra → "regionunidadcompra" y/o "regionproveedor"
# =============================================================

import sys
import sqlite3
import zipfile
import pandas as pd
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import RAW_LICITACIONES_DIR, RAW_ORDENES_DIR, DB_PATH

SEP      = ";"
ENCODING = "latin-1"

# Textos a buscar en columnas de región (case insensitive)
FILTRO_REGION = ["lagos", "LAGOS", "X REGION"]


# ─────────────────────────────────────────────────────────────
# DESCOMPRIMIR ZIP
# ─────────────────────────────────────────────────────────────

def descomprimir_zip(zip_path: Path) -> Path | None:
    """
    Extrae el CSV de un ZIP en la misma carpeta.
    Retorna el Path del CSV extraído, o None si falla.
    Si el CSV ya existe, lo retorna directamente sin extraer.
    """
    csv_esperado = zip_path.parent / f"{zip_path.stem}.csv"
    if csv_esperado.exists():
        return csv_esperado

    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            csvs = [f for f in z.namelist() if f.lower().endswith(".csv")]
            if not csvs:
                print(f"  AVISO: {zip_path.name} no contiene CSV")
                return None
            z.extract(csvs[0], path=zip_path.parent)
            extraido = zip_path.parent / csvs[0]
            if extraido != csv_esperado:
                extraido.rename(csv_esperado)
        return csv_esperado
    except Exception as e:
        print(f"  ERROR extrayendo {zip_path.name}: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# DETECTAR COLUMNAS DE REGIÓN EN EL CSV
# ─────────────────────────────────────────────────────────────

def detectar_cols_region(columnas: list, tipo: str) -> list:
    """
    Busca qué columnas contienen información de región.
    Retorna lista de nombres de columnas a filtrar.
    """
    candidatos = [c for c in columnas if "region" in c.lower()]
    if candidatos:
        return candidatos

    # Si no hay columna 'region', buscar alternativas
    if tipo == "licitacion":
        alt = ["comunaunidad", "direccionunidad"]
    else:
        alt = ["comunaunidadcompra", "comunaproveedor"]

    return [c for c in columnas if c.lower() in alt]


# ─────────────────────────────────────────────────────────────
# LEER CSV Y FILTRAR POR LOS LAGOS (sin cargar todo en memoria)
# ─────────────────────────────────────────────────────────────

def leer_y_filtrar_csv(csv_path: Path, tipo: str,
                       anio_mes: str) -> pd.DataFrame:
    """
    Lee el CSV en chunks de 50,000 filas.
    Por cada chunk filtra solo Los Lagos.
    Nunca tiene más de 50,000 filas en memoria a la vez.
    """
    chunks_filtrados = []
    total_leidas  = 0
    total_filtradas = 0

    try:
        reader = pd.read_csv(
            csv_path,
            sep=SEP,
            encoding=ENCODING,
            low_memory=False,
            dtype=str,
            on_bad_lines="skip",
            chunksize=50_000      # ← clave: leer de a 50k filas
        )

        cols_region_detectadas = None

        for chunk in reader:
            # Limpiar nombres de columnas
            chunk.columns = [
                c.strip().strip('"').lower()
                 .replace(" ", "_").replace("/", "_")
                 .replace("(","").replace(")","")
                 .replace("á","a").replace("é","e")
                 .replace("í","i").replace("ó","o")
                 .replace("ú","u").replace("ñ","n")
                for c in chunk.columns
            ]

            total_leidas += len(chunk)

            # Detectar columnas de región en el primer chunk
            if cols_region_detectadas is None:
                cols_region_detectadas = detectar_cols_region(
                    chunk.columns.tolist(), tipo
                )
                if not cols_region_detectadas:
                    print(f"    AVISO: no se detectaron columnas "
                          f"de región en {csv_path.name}")
                    print(f"    Columnas disponibles: "
                          f"{chunk.columns.tolist()[:20]}")

            # Filtrar filas que contengan "lagos" en alguna
            # columna de región
            if cols_region_detectadas:
                mask = pd.Series([False] * len(chunk),
                                 index=chunk.index)
                for col in cols_region_detectadas:
                    if col in chunk.columns:
                        mask |= chunk[col].str.contains(
                            "lagos", case=False, na=False
                        )
                chunk_filtrado = chunk[mask]
            else:
                # Si no hay columna región, guardar todo
                # (mejor tener de más que de menos)
                chunk_filtrado = chunk

            if not chunk_filtrado.empty:
                chunk_filtrado = chunk_filtrado.copy()
                chunk_filtrado["anio_mes"]      = anio_mes
                chunk_filtrado["tipo_registro"] = tipo
                chunk_filtrado["fecha_carga"]   = datetime.now().isoformat()
                chunks_filtrados.append(chunk_filtrado)
                total_filtradas += len(chunk_filtrado)

        if not chunks_filtrados:
            return pd.DataFrame()

        df = pd.concat(chunks_filtrados, ignore_index=True)
        return df

    except Exception as e:
        print(f"    ERROR leyendo {csv_path.name}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────
# GUARDAR EN SQLITE
# ─────────────────────────────────────────────────────────────

def guardar_mes(df: pd.DataFrame, tabla: str,
                anio_mes: str, conn: sqlite3.Connection):
    """Guarda un mes en SQLite. Alinea columnas automáticamente."""
    if df.empty:
        return

    # Verificar si este mes ya está en la tabla
    try:
        ya_existe = conn.execute(
            f"SELECT COUNT(*) FROM {tabla} "
            f"WHERE anio_mes=?", (anio_mes,)
        ).fetchone()[0]
        if ya_existe > 0:
            print(f"    {anio_mes}: ya existe ({ya_existe:,} filas) — saltando")
            return
    except Exception:
        pass  # tabla no existe aún, continuar

    # Verificar si la tabla ya existe con columnas definidas
    try:
        cols_existentes = [
            r[1] for r in conn.execute(
                f"PRAGMA table_info({tabla})"
            ).fetchall()
        ]
    except Exception:
        cols_existentes = []

    if cols_existentes:
        # Tabla ya existe — usar solo columnas en común
        # Columnas nuevas del CSV se descartan
        # Columnas faltantes en el CSV se rellenan con None
        cols_comunes = [c for c in cols_existentes if c in df.columns]
        cols_nuevas  = [c for c in df.columns if c not in cols_existentes]

        if cols_nuevas:
            print(f"    {anio_mes}: {len(cols_nuevas)} columnas nuevas ignoradas: "
                  f"{cols_nuevas[:3]}{'...' if len(cols_nuevas)>3 else ''}")

        df_guardar = df[cols_comunes]
    else:
        # Primera vez — crear tabla con todas las columnas del primer CSV
        df_guardar = df

    df_guardar.to_sql(tabla, conn, if_exists="append", index=False)
    conn.commit()
    print(f"    {anio_mes}: {len(df_guardar):,} filas guardadas en Los Lagos")


# ─────────────────────────────────────────────────────────────
# PROCESAR UNA CARPETA COMPLETA
# ─────────────────────────────────────────────────────────────

def procesar_carpeta(carpeta: Path, tipo: str,
                     tabla: str, conn: sqlite3.Connection):
    """
    Procesa todos los ZIPs de una carpeta uno por uno.
    Extrae → filtra Los Lagos → guarda en SQLite → borra CSV temporal.
    """
    zips = sorted(carpeta.glob("*.zip"))

    if not zips:
        print(f"  No hay ZIPs en {carpeta.name}")
        return

    print(f"  {len(zips)} ZIPs a procesar en {carpeta.name}")
    total_guardado = 0

    for i, zip_path in enumerate(zips):
        anio_mes = zip_path.stem   # '2022-1', '2023-10', etc.
        print(f"  [{i+1}/{len(zips)}] {zip_path.name}...", end=" ")

        # 1. Extraer ZIP
        csv_path = descomprimir_zip(zip_path)
        if csv_path is None:
            print("ERROR al extraer")
            continue

        # 2. Leer y filtrar solo Los Lagos
        df = leer_y_filtrar_csv(csv_path, tipo, anio_mes)

        # 3. Guardar en SQLite
        if not df.empty:
            guardar_mes(df, tabla, anio_mes, conn)
            total_guardado += len(df)
        else:
            print(f"    {anio_mes}: sin registros de Los Lagos")

        # 4. Borrar CSV temporal para liberar espacio
        try:
            csv_path.unlink()
        except Exception:
            pass

    # Resumen
    try:
        n = conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
        meses = conn.execute(
            f"SELECT COUNT(DISTINCT anio_mes) FROM {tabla}"
        ).fetchone()[0]
        print(f"\n  Total en {tabla}: {n:,} filas | {meses} meses")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────

def run():
    print("=" * 55)
    print("ingesta/chilecompra_csv.py")
    print("Procesando ZIPs — filtrando SOLO Región de Los Lagos")
    print("=" * 55)

    conn = sqlite3.connect(DB_PATH)

    # ── LICITACIONES ─────────────────────────────────────────
    print(f"\n[1/2] LICITACIONES")
    procesar_carpeta(
        RAW_LICITACIONES_DIR,
        "licitacion",
        "raw_licitaciones_csv",
        conn
    )

    # ── ÓRDENES DE COMPRA ────────────────────────────────────
    print(f"\n[2/2] ÓRDENES DE COMPRA")
    procesar_carpeta(
        RAW_ORDENES_DIR,
        "orden_compra",
        "raw_ordenes_csv",
        conn
    )

    # ── Resumen final ─────────────────────────────────────────
    print("\n" + "=" * 55)
    print("RESUMEN FINAL — Solo Región de Los Lagos")
    for tabla in ["raw_licitaciones_csv", "raw_ordenes_csv"]:
        try:
            n = conn.execute(
                f"SELECT COUNT(*) FROM {tabla}"
            ).fetchone()[0]
            meses = conn.execute(
                f"SELECT COUNT(DISTINCT anio_mes) FROM {tabla}"
            ).fetchone()[0]
            print(f"  {tabla}: {n:,} registros | {meses} meses")
        except Exception:
            print(f"  {tabla}: no creada")

    conn.close()
    print("\ningesta/chilecompra_csv.py completado.")
    print("Los CSV temporales fueron eliminados automáticamente.")


if __name__ == "__main__":
    run()