# =============================================================
#  ingesta/crm_contactos.py
#
#  Propósito: CRM básico para Patagonia Factoring SpA.
#  Registra el estado de seguimiento comercial de cada prospecto
#  para que el equipo de ventas no llame dos veces a la misma
#  empresa ni pierda el historial de contactos.
#
#  Sin este módulo, el modelo de scoring no tiene retroalimentación
#  y no puede distinguir entre un prospecto nuevo y un cliente
#  que ya fue contactado y rechazó el servicio.
#
#  Tabla: crm_contactos
#    rut_normalizado       TEXT PK
#    razon_social          TEXT
#    estado_crm            TEXT  (ver CRM_ESTADOS en config.py)
#    fecha_primer_contacto TEXT
#    fecha_ultimo_contacto TEXT
#    ejecutivo             TEXT  (nombre del ejecutivo a cargo)
#    notas                 TEXT  (última nota de contacto)
#    fecha_proxima_accion  TEXT  (recordatorio)
#    fecha_creacion        TEXT
#
#  ¿Con qué datos?
#  Solo necesita la tabla prospectos_rankeados (ya generada por el pipeline).
#  Los estados los ingresa manualmente el equipo comercial.
#  NO requiere datos externos ni APIs.
#
#  Uso desde terminal:
#    python ingesta/crm_contactos.py --init
#    python ingesta/crm_contactos.py --listar
#    python ingesta/crm_contactos.py --listar --estado INTERESADO
#    python ingesta/crm_contactos.py --actualizar --rut 12345678-9 \
#        --estado INTERESADO --ejecutivo "María" --notas "Llamó, quiere reunión"
#    python ingesta/crm_contactos.py --proximas          (acciones del día)
# =============================================================

import sys
import argparse
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from config import DB_PATH, CRM_ESTADOS
from utils.helpers import normalizar_rut


# ─────────────────────────────────────────────────────────────
# DDL
# ─────────────────────────────────────────────────────────────

DDL_CRM = """
CREATE TABLE IF NOT EXISTS crm_contactos (
    rut_normalizado       TEXT PRIMARY KEY,
    razon_social          TEXT,
    estado_crm            TEXT DEFAULT 'PENDIENTE',
    fecha_primer_contacto TEXT,
    fecha_ultimo_contacto TEXT,
    ejecutivo             TEXT,
    notas                 TEXT,
    fecha_proxima_accion  TEXT,
    fecha_creacion        TEXT
);
"""


# ─────────────────────────────────────────────────────────────
# INIT — puebla CRM con todos los prospectos nivel 1+2
# ─────────────────────────────────────────────────────────────

def init_crm(conn: sqlite3.Connection, forzar: bool = False):
    """
    Crea la tabla crm_contactos si no existe y la puebla con los
    prospectos de nivel 1 y 2 que aún no tienen registro CRM.

    Si forzar=True, inserta TODOS (sobreescribe solo los PENDIENTE).
    No sobreescribe registros con estado != PENDIENTE.
    """
    conn.execute(DDL_CRM)
    conn.commit()

    # Cargar prospectos nivel 1 + 2
    try:
        df = pd.read_sql("""
            SELECT rut_normalizado, razon_social, nivel, score
            FROM prospectos_rankeados
            WHERE nivel IN ('1 - Contactar hoy', '2 - Contactar esta semana')
            ORDER BY score DESC
        """, conn)
    except Exception as exc:
        print(f"  Sin prospectos_rankeados: {exc}")
        print("  Ejecutar primero: python run_pipeline.py --paso score")
        return 0

    if df.empty:
        print("  prospectos_rankeados vacío.")
        return 0

    ahora = datetime.now().isoformat()
    insertados = 0

    for _, row in df.iterrows():
        rut = str(row["rut_normalizado"])
        # Solo insertar si no existe ya con estado activo
        existing = conn.execute(
            "SELECT estado_crm FROM crm_contactos WHERE rut_normalizado = ?",
            (rut,)
        ).fetchone()

        if existing is None:
            conn.execute("""
                INSERT INTO crm_contactos
                    (rut_normalizado, razon_social, estado_crm, fecha_creacion)
                VALUES (?, ?, 'PENDIENTE', ?)
            """, (rut, row.get("razon_social", ""), ahora))
            insertados += 1
        elif forzar and existing[0] == "PENDIENTE":
            # Solo actualizar razon_social si está desactualizada
            conn.execute("""
                UPDATE crm_contactos SET razon_social = ? WHERE rut_normalizado = ?
            """, (row.get("razon_social", ""), rut))

    conn.commit()
    print(f"  crm_contactos: {insertados} prospectos nuevos agregados")

    # Resumen de estados actuales
    resumen = pd.read_sql("""
        SELECT estado_crm, COUNT(*) AS n
        FROM crm_contactos
        GROUP BY estado_crm
        ORDER BY n DESC
    """, conn)
    print("\n  Estado actual del CRM:")
    for _, r in resumen.iterrows():
        print(f"    {r['estado_crm']:20} {int(r['n']):5,}")

    return insertados


# ─────────────────────────────────────────────────────────────
# ACTUALIZAR — cambia estado de un prospecto
# ─────────────────────────────────────────────────────────────

def actualizar_estado(conn: sqlite3.Connection,
                      rut: str,
                      estado: str,
                      ejecutivo: str = None,
                      notas: str = None,
                      proxima_accion: str = None):
    """
    Actualiza el estado CRM de un prospecto.
    Si el rut no existe, lo inserta como nuevo registro.
    """
    if estado not in CRM_ESTADOS:
        print(f"  Estado inválido: '{estado}'")
        print(f"  Estados válidos: {', '.join(CRM_ESTADOS)}")
        return False

    rut_norm = normalizar_rut(rut)
    ahora    = datetime.now().strftime("%Y-%m-%d %H:%M")

    existing = conn.execute(
        "SELECT rut_normalizado, fecha_primer_contacto FROM crm_contactos "
        "WHERE rut_normalizado = ?", (rut_norm,)
    ).fetchone()

    if existing:
        primer_contacto = existing[1] or ahora
        conn.execute("""
            UPDATE crm_contactos SET
                estado_crm            = ?,
                fecha_ultimo_contacto = ?,
                fecha_primer_contacto = COALESCE(fecha_primer_contacto, ?),
                ejecutivo             = COALESCE(?, ejecutivo),
                notas                 = COALESCE(?, notas),
                fecha_proxima_accion  = COALESCE(?, fecha_proxima_accion)
            WHERE rut_normalizado = ?
        """, (estado, ahora, primer_contacto, ejecutivo, notas,
              proxima_accion, rut_norm))
    else:
        # Insertar si no existe (empresa fuera del ranking pero contactada)
        conn.execute("""
            INSERT INTO crm_contactos
                (rut_normalizado, estado_crm, fecha_primer_contacto,
                 fecha_ultimo_contacto, ejecutivo, notas,
                 fecha_proxima_accion, fecha_creacion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (rut_norm, estado, ahora, ahora, ejecutivo, notas,
              proxima_accion, ahora))

    conn.commit()
    print(f"  RUT {rut_norm} → {estado}"
          + (f" | {ejecutivo}" if ejecutivo else "")
          + (f" | {notas[:50]}" if notas else ""))
    return True


# ─────────────────────────────────────────────────────────────
# LISTAR — ver estado del CRM
# ─────────────────────────────────────────────────────────────

def listar(conn: sqlite3.Connection, estado: str = None, limite: int = 50):
    """Lista prospectos del CRM, opcionalmente filtrados por estado."""
    query = """
        SELECT
            c.rut_normalizado,
            COALESCE(c.razon_social, pr.razon_social) AS empresa,
            COALESCE(pr.comuna, '-')                  AS ciudad,
            c.estado_crm,
            c.ejecutivo,
            c.fecha_ultimo_contacto,
            c.fecha_proxima_accion,
            COALESCE(pr.score, '-')                   AS score,
            c.notas
        FROM crm_contactos c
        LEFT JOIN prospectos_rankeados pr
            ON c.rut_normalizado = pr.rut_normalizado
    """
    params = []
    if estado:
        query += " WHERE c.estado_crm = ?"
        params.append(estado)
    query += f" ORDER BY c.fecha_ultimo_contacto DESC LIMIT {limite}"

    df = pd.read_sql(query, conn, params=params)
    if df.empty:
        print("  Sin registros.")
        return

    print(f"\n  {'RUT':14} {'Empresa':35} {'Ciudad':15} "
          f"{'Estado':20} {'Score':6} {'Ult.contacto':14}")
    print("  " + "-" * 110)
    for _, r in df.iterrows():
        print(f"  {str(r['rut_normalizado']):14} "
              f"{str(r['empresa'])[:34]:35} "
              f"{str(r['ciudad'])[:14]:15} "
              f"{str(r['estado_crm']):20} "
              f"{str(r['score']):6} "
              f"{str(r['fecha_ultimo_contacto'] or '-')[:13]:14}")
        if r.get("notas"):
            print(f"  {'':14}   Nota: {str(r['notas'])[:80]}")


def proximas_acciones(conn: sqlite3.Connection):
    """Muestra recordatorios con fecha_proxima_accion <= hoy."""
    hoy = datetime.now().strftime("%Y-%m-%d")
    df = pd.read_sql(f"""
        SELECT
            c.rut_normalizado,
            COALESCE(c.razon_social, pr.razon_social) AS empresa,
            c.estado_crm,
            c.ejecutivo,
            c.fecha_proxima_accion,
            c.notas
        FROM crm_contactos c
        LEFT JOIN prospectos_rankeados pr ON c.rut_normalizado = pr.rut_normalizado
        WHERE c.fecha_proxima_accion IS NOT NULL
          AND c.fecha_proxima_accion <= '{hoy}'
          AND c.estado_crm NOT IN ('CLIENTE', 'RECHAZADO', 'NO_APLICA')
        ORDER BY c.fecha_proxima_accion
    """, conn)

    if df.empty:
        print("  Sin acciones pendientes para hoy.")
        return

    print(f"\n  ACCIONES PENDIENTES AL {hoy}")
    print("  " + "=" * 70)
    for _, r in df.iterrows():
        print(f"  [{r['fecha_proxima_accion']}] "
              f"{str(r['empresa'])[:35]:35} "
              f"{r['estado_crm']:20} "
              f"Ej: {r.get('ejecutivo') or '-'}")
        if r.get("notas"):
            print(f"    Nota: {str(r['notas'])[:70]}")


# ─────────────────────────────────────────────────────────────
# RUN — init automático en pipeline
# ─────────────────────────────────────────────────────────────

def run():
    """
    Punto de entrada del pipeline: crea la tabla si no existe
    y agrega nuevos prospectos nivel 1+2 con estado PENDIENTE.
    No modifica registros existentes.
    """
    print("=" * 55)
    print("ingesta/crm_contactos.py — sincronizar CRM")
    print("=" * 55)

    conn = sqlite3.connect(DB_PATH)
    init_crm(conn, forzar=False)
    conn.close()
    print("\ningesta/crm_contactos.py completado.")


# ─────────────────────────────────────────────────────────────
# CLI — uso manual desde terminal
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="CRM Patagonia Factoring — seguimiento de prospectos",
        epilog="""
ejemplos:
  python ingesta/crm_contactos.py --init
  python ingesta/crm_contactos.py --listar
  python ingesta/crm_contactos.py --listar --estado INTERESADO
  python ingesta/crm_contactos.py --proximas
  python ingesta/crm_contactos.py --actualizar --rut 12345678-9 \\
      --estado INTERESADO --ejecutivo "María" \\
      --notas "Llamó, tiene OC de $8M, quiere reunión" \\
      --proxima 2026-04-07
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--init",       action="store_true",
                        help="Crear/poblar CRM desde prospectos_rankeados")
    parser.add_argument("--listar",     action="store_true",
                        help="Listar registros CRM")
    parser.add_argument("--proximas",   action="store_true",
                        help="Mostrar acciones con fecha <= hoy")
    parser.add_argument("--actualizar", action="store_true",
                        help="Actualizar estado de un prospecto")
    parser.add_argument("--estado",     type=str,
                        help=f"Estado CRM ({', '.join(CRM_ESTADOS)})")
    parser.add_argument("--rut",        type=str, help="RUT del prospecto")
    parser.add_argument("--ejecutivo",  type=str, help="Nombre del ejecutivo")
    parser.add_argument("--notas",      type=str, help="Nota de contacto")
    parser.add_argument("--proxima",    type=str,
                        help="Fecha próxima acción (YYYY-MM-DD)")

    args = parser.parse_args()

    if not any([args.init, args.listar, args.proximas, args.actualizar]):
        parser.print_help()
        sys.exit(0)

    conn = sqlite3.connect(DB_PATH)

    if args.init:
        print("Inicializando CRM...")
        init_crm(conn, forzar=True)

    if args.listar:
        listar(conn, estado=args.estado)

    if args.proximas:
        proximas_acciones(conn)

    if args.actualizar:
        if not args.rut or not args.estado:
            print("ERROR: --actualizar requiere --rut y --estado")
            sys.exit(1)
        actualizar_estado(
            conn,
            rut        = args.rut,
            estado     = args.estado,
            ejecutivo  = args.ejecutivo,
            notas      = args.notas,
            proxima_accion = args.proxima,
        )

    conn.close()
