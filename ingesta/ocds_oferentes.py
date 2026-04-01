# =============================================================
#  ingesta/ocds_oferentes.py — Versión 2
#
#  Captura TODOS los oferentes de cada licitación:
#  ganadores Y perdedores, con datos completos para el
#  modelo predictivo.
#
#  Datos capturados por oferente:
#    - RUT, razón social, región, comuna
#    - Si ganó o no (adjudicado 0/1)
#    - Monto del contrato (si ganó)
#    - Datos de la licitación: fecha, organismo, rubro, monto
#    - Número de competidores en esa licitación
# =============================================================

import sys
import sqlite3
import requests
import pandas as pd
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import OCDS_BASE_URL, DB_PATH, SLEEP_OCDS
from utils.helpers import normalizar_rut
import time


def get_oferentes(id_licitacion: str) -> list:
    """
    Llama a OCDS /award/{id} y retorna todos los participantes
    con datos completos incluyendo los perdedores.
    """
    url = f"{OCDS_BASE_URL}/award/{id_licitacion}"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 404:
            return []
        if r.status_code != 200:
            return []

        data     = r.json()
        releases = data.get("releases", [])
        if not releases:
            return []

        release = releases[0]
        parties = release.get("parties", [])
        awards  = release.get("awards", [])
        tender  = release.get("tender", {})

        # ── Datos de la licitación ────────────────────────────
        fecha_pub    = release.get("date", "")[:10]
        monto_licit  = None
        fecha_adj    = None
        titulo_adj   = None
        status_adj   = None

        if awards:
            award       = awards[0]
            monto_licit = award.get("value", {}).get("amount")
            fecha_adj   = award.get("date", "")[:10]
            titulo_adj  = award.get("title", "")
            status_adj  = award.get("status", "")

        # Monto estimado de la licitación
        monto_estimado = tender.get("value", {}).get("amount")

        # Rubro / categoría
        items = tender.get("items", [])
        rubro = ""
        if items:
            clasificacion = items[0].get(
                "classification", {}
            )
            rubro = clasificacion.get("description", "")

        # Organismo comprador
        buyer_id     = release.get("buyer", {}).get("id", "")
        nombre_org   = ""
        region_org   = ""
        for party in parties:
            if party.get("id") == buyer_id:
                nombre_org = party.get("name", "").split("|")[0].strip()
                region_org = party.get(
                    "address", {}
                ).get("region", "")
                break

        # Total de oferentes (competidores)
        n_tenderers = sum(
            1 for p in parties
            if "tenderer" in p.get("roles", [])
        )

        # ── Extraer cada oferente ─────────────────────────────
        resultado = []
        for party in parties:
            roles = party.get("roles", [])

            # Saltar al comprador
            if "buyer" in roles and "tenderer" not in roles:
                continue
            if "tenderer" not in roles:
                continue

            es_ganador = "supplier" in roles
            identifier = party.get("identifier", {})
            address    = party.get("address", {})

            rut_raw  = identifier.get("id", "")
            rut_norm = normalizar_rut(rut_raw)

            resultado.append({
                # Identificación del oferente
                "id_licitacion":      id_licitacion,
                "rut_normalizado":    rut_norm,
                "rut_raw":            rut_raw,
                "razon_social":       identifier.get("legalName", ""),
                "region_empresa":     address.get("region", ""),
                "comuna_empresa":     address.get("locality", ""),

                # Resultado
                "adjudicado":         1 if es_ganador else 0,
                "monto_contrato":     monto_licit if es_ganador
                                      else None,

                # Datos de la licitación
                "fecha_publicacion":  fecha_pub,
                "fecha_adjudicacion": fecha_adj,
                "nombre_organismo":   nombre_org,
                "region_organismo":   region_org,
                "rubro_licitacion":   rubro,
                "monto_estimado":     monto_estimado,
                "n_competidores":     n_tenderers,
                "status_adjudicacion":status_adj,

                "fecha_extraccion":   datetime.now().isoformat(),
            })

        return resultado

    except requests.exceptions.Timeout:
        return []
    except Exception:
        return []


def crear_tabla(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_oferentes (
            id_licitacion       TEXT,
            rut_normalizado     TEXT,
            rut_raw             TEXT,
            razon_social        TEXT,
            region_empresa      TEXT,
            comuna_empresa      TEXT,
            adjudicado          INTEGER,
            monto_contrato      REAL,
            fecha_publicacion   TEXT,
            fecha_adjudicacion  TEXT,
            nombre_organismo    TEXT,
            region_organismo    TEXT,
            rubro_licitacion    TEXT,
            monto_estimado      REAL,
            n_competidores      INTEGER,
            status_adjudicacion TEXT,
            fecha_extraccion    TEXT,
            PRIMARY KEY (id_licitacion, rut_normalizado)
        )
    """)
    conn.commit()


def get_ids_pendientes(conn: sqlite3.Connection) -> list:
    try:
        procesados = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT id_licitacion FROM raw_oferentes"
            ).fetchall()
        }
    except Exception:
        procesados = set()

    ids = set()
    for tabla, col in [
        ("raw_licitaciones_csv", "codigoexterno"),
        ("raw_licitaciones_api", "codigo_externo"),
    ]:
        try:
            rows = conn.execute(
                f'SELECT DISTINCT "{col}" FROM {tabla} '
                f'WHERE "{col}" IS NOT NULL'
            ).fetchall()
            ids.update(r[0] for r in rows)
        except Exception:
            pass

    return sorted(ids - procesados)


def flush_buffer(buffer: list, conn: sqlite3.Connection):
    if not buffer:
        return
    df = pd.DataFrame(buffer)
    df.to_sql("raw_oferentes_tmp", conn,
              if_exists="replace", index=False)
    conn.execute("""
        INSERT OR IGNORE INTO raw_oferentes
        SELECT * FROM raw_oferentes_tmp
    """)
    conn.execute("DROP TABLE IF EXISTS raw_oferentes_tmp")
    conn.commit()


def run(modo: str = "incremental"):
    print("=" * 55)
    print(f"ingesta/ocds_oferentes.py v2 — modo={modo}")
    print("Capturando ganadores Y perdedores con datos completos")
    print("=" * 55)

    conn = sqlite3.connect(DB_PATH)
    crear_tabla(conn)

    if modo == "incremental":
        try:
            rows = conn.execute("""
                SELECT DISTINCT codigo_externo
                FROM raw_licitaciones_api
                WHERE codigo_externo NOT IN (
                    SELECT DISTINCT id_licitacion
                    FROM raw_oferentes
                )
            """).fetchall()
            ids = [r[0] for r in rows]
        except Exception:
            ids = []
    else:
        ids = get_ids_pendientes(conn)

    total = len(ids)
    print(f"\nIDs pendientes: {total:,}")

    if total == 0:
        print("Todo actualizado.")
        conn.close()
        return

    if modo == "full" and total > 500:
        mins = round(total * SLEEP_OCDS / 60)
        print(f"Tiempo estimado: ~{mins} minutos")
        print("Si interrumpes, retoma automáticamente.\n")

    buffer   = []
    LOTE     = 300
    n_ok     = 0
    n_vacios = 0

    for i, id_lit in enumerate(ids):
        oferentes = get_oferentes(id_lit)

        if oferentes:
            buffer.extend(oferentes)
            n_ok += 1
        else:
            n_vacios += 1

        if (i + 1) % 100 == 0:
            pct = round(100 * (i + 1) / total, 1)
            mins_rest = round(
                (total - i - 1) * SLEEP_OCDS / 60
            )
            print(f"  [{pct}%] {i+1}/{total} — "
                  f"con datos: {n_ok} | "
                  f"sin datos: {n_vacios} | "
                  f"~{mins_rest} min restantes")

        if len(buffer) >= LOTE:
            flush_buffer(buffer, conn)
            buffer = []

        time.sleep(SLEEP_OCDS)

    flush_buffer(buffer, conn)

    # Estadísticas finales
    try:
        n_total = conn.execute(
            "SELECT COUNT(*) FROM raw_oferentes"
        ).fetchone()[0]
        n_gan   = conn.execute(
            "SELECT COUNT(*) FROM raw_oferentes "
            "WHERE adjudicado=1"
        ).fetchone()[0]
        n_perd  = n_total - n_gan

        print(f"\n{'='*55}")
        print(f"COMPLETADO")
        print(f"  Total oferentes:   {n_total:,}")
        print(f"  Ganadores:         {n_gan:,}")
        print(f"  Perdedores:        {n_perd:,}")

        # Muestra de perdedores capturados
        sample = conn.execute("""
            SELECT razon_social, region_empresa,
                   nombre_organismo, monto_estimado,
                   n_competidores, fecha_adjudicacion
            FROM raw_oferentes
            WHERE adjudicado = 0
            AND razon_social != ''
            LIMIT 5
        """).fetchall()

        if sample:
            print("\nMuestra perdedores capturados:")
            for s in sample:
                print(f"  - {s[0][:40]} | "
                      f"{s[1][:20]} | "
                      f"org: {str(s[2])[:25]} | "
                      f"competidores: {s[4]}")
    except Exception as e:
        print(f"Error en estadísticas: {e}")

    conn.close()
    print("\ningesta/ocds_oferentes.py completado.")


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "incremental"
    run(modo)