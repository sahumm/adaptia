"""
AdaptIA: aplicación de consola que aprende a qué hora te conviene hacer cada hábito.

Especificaciones de referencia: algoritmo.md (predicción y aprendizaje) y flujo.md (interacción).

Organización del archivo:
    1.  CONFIGURACIÓN            constantes y parámetros del programa
    2.  BASE DE DATOS            conexión, verificación del esquema, lectura y escritura
    3.  UTILIDADES               contraseñas, formato de horas, entrada validada, tablas y bloques horarios
    4.  MOTOR DEL ALGORITMO      ventanas recomendadas, restricciones, competencia entre hábitos y propuestas
    5.  REGISTRO DE USUARIO      captura de datos, validaciones y guardado del usuario nuevo
    6.  INICIO DE SESIÓN         verificación de credenciales
    7.  REGISTRO DE HÁBITOS      cumplido / no hecho y recomendación según la emoción
    8.  MURO DE RECORDATORIOS    generación, archivo y resolución de pendientes
    9.  CONSULTAS                tablas, plan de hoy y gráfica de emociones
    10. GESTIÓN                  agregar hábitos y rutinas, activar / desactivar
    11. MENÚ PRINCIPAL           menús y punto de entrada

Convenciones del esquema:
    - Todas las horas (rutinas y hábitos) se guardan como minutos desde medianoche (0 a 1439).
    - Dormir se registra como una rutina fija más (opcional). Una rutina que cruza la medianoche
      se guarda partida en dos bloques: ej. 23:00-07:00 del lunes = lunes 23:00-23:59 + martes 00:00-07:00.
    - Un hábito tiene UN solo horario, igual para todos sus días (algoritmo.md, sección 9).
      Su hora de fin es opcional: si varía, 'duracion' se guarda en NULL (None).
    - Los días de la semana se guardan como entero: 0 = Lunes ... 6 = Domingo.
    - registros.cumplio: 1 = hecho, -1 = no hecho (sin hora ni emoción).
    - horarios_habito.estado: 'declarado', 'bueno', 'malo' o 'en_prueba'.
    - recordatorios.estado: 'pendiente', 'pospuesto', 'hecho', 'descartado' o 'archivado'.
    - La contraseña se guarda como hash PBKDF2 ("sal$hash"), nunca en texto plano.
"""

import hashlib
import hmac
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from getpass import getpass
from pathlib import Path


# =============================================================================
# 1. CONFIGURACIÓN
# =============================================================================

# --- 1.1 Base de datos --------------------------------------------------------

# Por defecto "adaptia-db" junto a este script. Se puede indicar otro archivo
# (por ejemplo una copia de respaldo para pruebas) como argumento:
#     python3 adaptia.py adaptia-db.respaldo
RUTA_BD = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "adaptia-db"

# Columnas que este código escribe o lee en cada tabla. Si el esquema real usa otros
# nombres, hay que corregirlos aquí y en las consultas SQL correspondientes.
COLUMNAS_USADAS = {
    "usuarios": ["usuario", "contrasena_hash", "ultimo_login"],
    "rutinas_fijas": ["usuario_id", "rutina", "activo"],
    "rutina_dias": ["rutina_id", "dia", "hora_inicio", "hora_fin"],
    "habitos": ["usuario_id", "habito", "peso", "duracion", "hora_esperada", "activo", "fecha_creacion"],
    "habito_dias": ["habito_id", "dia"],
    "horarios_habito": ["habito_id", "hora_central", "margen", "estado", "ultima_vez"],
    "predicciones": ["habito_id", "horario_id", "fecha", "hora_predicha_inicio", "hora_predicha_final", "acierto"],
    "registros": ["habito_id", "fecha", "hora", "cumplio", "emocion"],
    "recordatorios": ["habito_id", "fecha_objetivo", "estado", "fecha_resuelto"],
}

# --- 1.2 Tiempo y días --------------------------------------------------------

ULTIMO_MINUTO = 1439  # 23:59, el último minuto del día (las horas se guardan de 0 a 1439)

# La posición en la lista es el índice que se guarda en la BD (0 = Lunes).
DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
DIAS_CORTOS = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"]

# --- 1.3 Límites de captura y seguridad ---------------------------------------

MAX_RUTINAS = 20
MAX_HABITOS = 20
MIN_LONGITUD_CONTRASENA = 8
MAX_INTENTOS_LOGIN = 3
ITERACIONES_HASH = 200_000

# --- 1.4 Algoritmo (algoritmo.md, secciones 3, 5, 7 y 9; flujo.md 5.4) --------

MARGEN_INICIAL = 30            # min; margen de un horario nuevo (§3 y §9)
REGISTROS_VIGENTES = 14        # solo los últimos 14 registros de cada hábito entran en los cálculos (§7)
DIAS_ARCHIVO = 7               # un recordatorio sin resolver se archiva como "no hecho" a los 7 días (flujo 5.4)
DURACION_POR_DEFECTO = 30      # min; se usa para buscar huecos de hábitos sin hora de fin (decisión pendiente)
BUSQUEDA_MAX_HORAS = 12        # hasta cuántas horas antes/después del horario original se busca un hueco (§5.3)

# Emociones (algoritmo.md, sección 2): opción del menú -> (nombre, valor guardado en registros.emocion).
EMOCIONES = [("Felicidad", 5), ("Satisfacción", 4), ("Nada", 3), ("Frustración", 2), ("Enojo", 1)]

# Recomendación según la emoción (algoritmo.md, sección 2). Felicidad y satisfacción (4-5) = seguir a esa
# hora; "nada" (3) = seguir o probar otra; frustración y enojo (1-2) = probar una hora distinta
# (ver proponer_horario). {hora} se reemplaza por la hora real en que se hizo el hábito.
RECOMENDACIONES = {
    5: "Te sentiste muy bien: sigue haciéndolo a las {hora}, es una buena hora para ti.",
    4: "Te sentiste bien: sigue haciéndolo a las {hora}, es una buena hora para ti.",
    3: "Puedes seguir haciéndolo a las {hora} o intentar otra hora.",
}

# --- 1.5 Presentación ---------------------------------------------------------

SIN_HORARIO = "Sin horario"  # texto que se muestra cuando un horario es nulo o no existe
ANCHO_BARRA = 0.35           # grosor de las barras de la gráfica de emociones (1.0 = pegadas)


# =============================================================================
# 2. BASE DE DATOS: conexión, esquema, lectura y escritura
# =============================================================================

# --- 2.1 Conexión y verificación del esquema -----------------------------------


def verificar_esquema(conexion):
    """
    Compara el esquema real de la BD contra las columnas que el código necesita.
    Devuelve una lista de textos con los problemas encontrados (vacía si todo coincide):
    tablas faltantes, columnas faltantes y columnas NOT NULL que el código no llena.
    """
    problemas = []
    for tabla, columnas in COLUMNAS_USADAS.items():
        info = conexion.execute(f"PRAGMA table_info({tabla})").fetchall()
        if not info:
            problemas.append(f"Falta la tabla '{tabla}'.")
            continue

        existentes = {fila[1] for fila in info}
        faltantes = set(columnas) - existentes
        if faltantes:
            problemas.append(f"'{tabla}' no tiene las columnas: {', '.join(sorted(faltantes))}.")

        # fila = (cid, nombre, tipo, notnull, valor_por_defecto, pk)
        obligatorias = {f[1] for f in info if f[3] and f[4] is None and not f[5]}
        sin_llenar = obligatorias - set(columnas)
        if sin_llenar:
            problemas.append(
                f"'{tabla}' tiene columnas obligatorias que el código no llena: "
                f"{', '.join(sorted(sin_llenar))}."
            )

    # La hora de fin de un hábito es opcional: 'duracion' debe aceptar NULL.
    for fila in conexion.execute("PRAGMA table_info(habitos)").fetchall():
        if fila[1] == "duracion" and fila[3]:
            problemas.append(
                "'habitos.duracion' es NOT NULL, pero la hora de fin de un hábito es opcional. "
                "Quita NOT NULL a esa columna (DB Browser > Modify Table)."
            )
    return problemas

def conectar():
    """
    Abre la conexión a SQLite con llaves foráneas activadas y verifica el esquema.
    Si el archivo no existe o el esquema no coincide, avisa y termina el programa.
    """
    if not RUTA_BD.exists():
        # sqlite3.connect crearía una BD vacía en silencio; mejor avisar.
        print(f"No se encontró la base de datos en: {RUTA_BD}")
        sys.exit(1)

    conexion = sqlite3.connect(RUTA_BD)
    conexion.execute("PRAGMA foreign_keys = ON")
    print(f"Usando la base de datos: {RUTA_BD.resolve()}\n")

    problemas = verificar_esquema(conexion)
    if problemas:
        print("El esquema de la base de datos no coincide con el código:")
        for problema in problemas:
            print(f"  - {problema}")
        print("\nRevisa que 'adaptia-db' tenga el esquema más reciente.")
        conexion.close()
        sys.exit(1)
    return conexion

# --- 2.2 Lectura de datos del usuario ------------------------------------------


def obtener_habitos(conexion, usuario_id):
    """
    Devuelve los hábitos ACTIVOS del usuario como lista de diccionarios
    {id, nombre, peso, duracion, hora_esperada, dias, veces}, de mayor a menor importancia.
    'veces' es cuántas veces lo ha cumplido (registros con cumplio = 1).
    """
    filas = conexion.execute(
        "SELECT id, habito, peso, duracion, hora_esperada FROM habitos "
        "WHERE usuario_id = ? AND activo = 1 ORDER BY peso DESC, habito",
        (usuario_id,),
    ).fetchall()

    try:
        veces = dict(conexion.execute(
            "SELECT habito_id, COUNT(*) FROM registros WHERE cumplio = 1 GROUP BY habito_id"
        ).fetchall())
    except sqlite3.OperationalError as error:
        # Si la tabla 'registros' usa otros nombres de columna, hay que corregir la consulta de arriba.
        print(f"No se pudieron leer los registros ({error}). Se mostrarán 0 veces.")
        veces = {}

    habitos = []
    for id_, nombre, peso, duracion, hora in filas:
        dias = [d for (d,) in conexion.execute(
            "SELECT dia FROM habito_dias WHERE habito_id = ? ORDER BY dia", (id_,)
        )]
        habitos.append({
            "id": id_, "nombre": nombre, "peso": peso, "duracion": duracion,
            "hora_esperada": hora, "dias": dias, "veces": veces.get(id_, 0),
        })
    return habitos

def cargar_rutinas(conexion, usuario_id):
    """
    Devuelve las rutinas fijas ACTIVAS del usuario como {"id", "nombre", "bloques"},
    con 'bloques' = [(dia, inicio, fin)]. Es el mismo formato que usa crear_validador_habitos.
    """
    rutinas = []
    filas = conexion.execute(
        "SELECT id, rutina FROM rutinas_fijas WHERE usuario_id = ? AND activo = 1 ORDER BY rutina",
        (usuario_id,),
    ).fetchall()
    for id_, nombre in filas:
        bloques = conexion.execute(
            "SELECT dia, hora_inicio, hora_fin FROM rutina_dias WHERE rutina_id = ?", (id_,)
        ).fetchall()
        rutinas.append({"id": id_, "nombre": nombre, "bloques": bloques})
    return rutinas

def ultimos_registros(conexion, habito_id, n=REGISTROS_VIGENTES):
    """
    Últimos n registros de un hábito como (fecha, hora, cumplio, emocion), del más reciente al más antiguo
    (algoritmo.md, sección 7). Los anteriores se conservan en la BD pero no participan en los cálculos.
    """
    return conexion.execute(
        "SELECT fecha, hora, cumplio, emocion FROM registros WHERE habito_id = ? "
        "ORDER BY fecha DESC, id DESC LIMIT ?", (habito_id, n),
    ).fetchall()

# --- 2.3 Escritura de rutinas, hábitos y recordatorios -------------------------


def insertar_rutina(conexion, usuario_id, rutina):
    """
    Inserta una rutina fija y sus bloques horarios (activo = 1 por defecto en la BD).
    No hace commit: debe llamarse dentro de un 'with conexion:'.
    """
    cur = conexion.execute(
        "INSERT INTO rutinas_fijas (usuario_id, rutina) VALUES (?, ?)",
        (usuario_id, rutina["nombre"]),
    )
    conexion.executemany(
        "INSERT INTO rutina_dias (rutina_id, dia, hora_inicio, hora_fin) VALUES (?, ?, ?, ?)",
        [(cur.lastrowid, d, ini, fin) for d, ini, fin in rutina["bloques"]],
    )

def insertar_habito(conexion, usuario_id, habito, hoy):
    """
    Inserta un hábito con sus días, su horario 'declarado' (hora central ± MARGEN_INICIAL)
    y su primera predicción de 'hoy' (algoritmo.md, sección 3).
    No hace commit: debe llamarse dentro de un 'with conexion:'.
    """
    cur = conexion.execute(
        "INSERT INTO habitos (usuario_id, habito, peso, duracion, hora_esperada) "
        "VALUES (?, ?, ?, ?, ?)",
        (usuario_id, habito["nombre"], habito["peso"], habito["duracion"], habito["hora_esperada"]),
    )
    habito_id = cur.lastrowid
    conexion.executemany(
        "INSERT INTO habito_dias (habito_id, dia) VALUES (?, ?)",
        [(habito_id, d) for d in habito["dias"]],
    )

    central = habito["hora_central"]
    cur = conexion.execute(
        "INSERT INTO horarios_habito (habito_id, hora_central, margen, estado) "
        "VALUES (?, ?, ?, 'declarado')",
        (habito_id, central, MARGEN_INICIAL),
    )
    conexion.execute(
        "INSERT INTO predicciones (habito_id, horario_id, fecha, hora_predicha_inicio, "
        "hora_predicha_final) VALUES (?, ?, ?, ?, ?)",
        (habito_id, cur.lastrowid, hoy, *limites_ventana(central, MARGEN_INICIAL)),
    )

def crear_recordatorio(conexion, habito_id, fecha_iso):
    """
    Crea el recordatorio pendiente de un hábito para una fecha ISO. Si ya existe uno, no hace nada
    (un solo recordatorio por hábito y día, flujo.md 5.4). No hace commit: usar dentro de 'with conexion:'.
    """
    conexion.execute(
        "INSERT OR IGNORE INTO recordatorios (habito_id, fecha_objetivo) VALUES (?, ?)", (habito_id, fecha_iso)
    )


# =============================================================================
# 3. UTILIDADES COMPARTIDAS
# =============================================================================

# --- 3.1 Contraseñas -----------------------------------------------------------


def hashear_contrasena(contrasena):
    """Convierte una contraseña en 'sal$hash' (PBKDF2-SHA256 con sal aleatoria de 16 bytes)."""
    sal = os.urandom(16)
    hash_ = hashlib.pbkdf2_hmac("sha256", contrasena.encode(), sal, ITERACIONES_HASH)
    return f"{sal.hex()}${hash_.hex()}"

def verificar_contrasena(contrasena, guardado):
    """
    Comprueba una contraseña contra el valor 'sal$hash' guardado en la BD.
    Devuelve True si coincide; False si no coincide o el valor guardado es inválido.
    """
    try:
        sal_hex, hash_hex = guardado.split("$")
        sal = bytes.fromhex(sal_hex)
        esperado = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    calculado = hashlib.pbkdf2_hmac("sha256", contrasena.encode(), sal, ITERACIONES_HASH)
    return hmac.compare_digest(calculado, esperado)

# --- 3.2 Formato de horas, fechas y fragmentos de texto ------------------------


def _a_minutos(texto):
    """Convierte 'HH:MM' (24 h) a minutos desde medianoche. Devuelve None si el formato es inválido."""
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", texto)
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None

def a_texto(minutos):
    """Convierte minutos desde medianoche a texto 'HH:MM' (ejemplo: 450 -> '07:30')."""
    return f"{minutos // 60:02d}:{minutos % 60:02d}"

def texto_hora(minutos):
    """'HH:MM', o SIN_HORARIO si la hora es None."""
    return SIN_HORARIO if minutos is None else a_texto(minutos)

def texto_intervalo(inicio, fin):
    """'HH:MM-HH:MM', o SIN_HORARIO si falta alguno de los dos extremos (None)."""
    if inicio is None or fin is None:
        return SIN_HORARIO
    return f"{a_texto(inicio)}-{a_texto(fin)}"

def formatear_dias(dias):
    """Convierte [0, 2, 4] en 'Lun,Mie,Vie' (o 'Todos' si son los 7 días)."""
    if len(dias) == 7:
        return "Todos"
    return ",".join(DIAS_CORTOS[d] for d in dias)

def ahora_iso():
    """Fecha y hora actuales como texto ISO sin fracciones (ej. '2026-11-15T09:30:00'), formato guardado en la BD."""
    return datetime.now().isoformat(timespec="seconds")

def limites_ventana(central, margen):
    """Ventana (inicio, fin) = central ± margen, recortada a los límites del día (0 a ULTIMO_MINUTO)."""
    return max(0, central - margen), min(ULTIMO_MINUTO, central + margen)

# --- 3.3 Entrada de datos con validación ---------------------------------------


def pedir_texto(mensaje, minimo=1, maximo=50):
    """Pide un texto y lo repite hasta que su longitud (sin espacios en los extremos) esté en el rango."""
    while True:
        texto = input(mensaje).strip()
        if minimo <= len(texto) <= maximo:
            return texto
        print(f"Debe tener entre {minimo} y {maximo} caracteres.")

def pedir_entero(mensaje, minimo, maximo):
    """Pide un número entero y lo repite hasta que sea válido y esté entre minimo y maximo."""
    while True:
        try:
            valor = int(input(mensaje).strip())
        except ValueError:
            print("Ingresa un número entero.")
            continue
        if minimo <= valor <= maximo:
            return valor
        print(f"El número debe estar entre {minimo} y {maximo}.")

def pedir_si_no(mensaje):
    """Hace una pregunta con opciones 1.-SI / 2.-NO. Devuelve True para SI y False para NO."""
    return pedir_entero(f"{mensaje}\n1.-SI\n2.-NO\n> ", 1, 2) == 1

def pedir_hora(mensaje):
    """Pide una hora en formato HH:MM (24 h) y la devuelve en minutos desde medianoche."""
    while True:
        minutos = _a_minutos(input(mensaje).strip())
        if minutos is not None:
            return minutos
        print("Formato inválido. Usa HH:MM en 24 horas (ejemplo: 07:30 o 18:45).")

def pedir_hora_opcional(mensaje):
    """Como pedir_hora, pero Enter vacío significa 'no aplica' y devuelve None."""
    while True:
        texto = input(mensaje).strip()
        if texto == "":
            return None
        minutos = _a_minutos(texto)
        if minutos is not None:
            return minutos
        print("Formato inválido. Usa HH:MM en 24 horas (ejemplo: 23:30) o Enter para omitir.")

def pedir_hora_fin(mensaje, hora_inicio, permitir_cruce=False):
    """
    Pide una hora de fin y la repite hasta que sea válida.
    Normalmente debe ser posterior a hora_inicio. Con permitir_cruce=True puede ser anterior
    (la actividad cruza la medianoche, ej. 23:00 a 07:00), pero nunca igual al inicio.
    """
    while True:
        fin = pedir_hora(mensaje)
        if permitir_cruce and fin != hora_inicio:
            return fin
        if not permitir_cruce and fin > hora_inicio:
            return fin
        if permitir_cruce:
            print("La hora de fin no puede ser igual a la de inicio.")
        else:
            print("La hora de fin debe ser posterior a la de inicio.")

def pedir_hora_fin_opcional(mensaje, hora_inicio):
    """
    Como pedir_hora_fin (sin cruce de medianoche), pero Enter vacío significa 'sin hora de fin'
    (hábitos cuya duración varía) y devuelve None.
    """
    while True:
        texto = input(mensaje).strip()
        if texto == "":
            return None
        fin = _a_minutos(texto)
        if fin is None:
            print("Formato inválido. Usa HH:MM en 24 horas (ejemplo: 18:45) o Enter si no tiene hora de fin.")
        elif fin <= hora_inicio:
            print("La hora de fin debe ser posterior a la de inicio.")
        else:
            return fin

def pedir_emocion():
    """Pide la emoción que sintió el usuario al hacer el hábito y devuelve su valor de 1 a 5."""
    print("\n¿Cómo te sentiste al hacerlo?")
    for i, (nombre, valor) in enumerate(EMOCIONES, 1):
        print(f"{i}.-{nombre}")
    return EMOCIONES[pedir_entero("> ", 1, len(EMOCIONES)) - 1][1]

# --- 3.4 Captura de nombres, días y horarios -----------------------------------


def pedir_nombre_unico(mensaje, ya_usados):
    """Pide un nombre y lo repite si ya está en 'ya_usados' (conjunto en minúsculas)."""
    while True:
        nombre = pedir_texto(mensaje)
        if nombre.lower() in ya_usados:
            print("Ya registraste uno con ese nombre. Usa otro.")
            continue
        return nombre

def pedir_dias(nombre):
    """
    Pide los días de la semana en que aplica una actividad.
    Acepta números del 1 al 7 separados por coma (ej. 1,3,5) o la palabra 'todos'.
    Devuelve una lista ordenada de índices 0-6, sin repetidos.
    """
    while True:
        print(f"¿Qué días aplica '{nombre}'?")
        print("  " + "  ".join(f"{i + 1}.-{d}" for i, d in enumerate(DIAS_SEMANA)))
        texto = input("Escribe los números separados por coma (ej. 1,3,5) o 'todos'\n> ").strip().lower()
        if texto in ("todos", "todo"):
            return list(range(7))
        partes = [p for p in re.split(r"[,\s]+", texto) if p]
        if partes and all(p.isdigit() and 1 <= int(p) <= 7 for p in partes):
            return sorted({int(p) - 1 for p in partes})
        print("Entrada inválida. Usa números del 1 al 7 o la palabra 'todos'.")

def pedir_intervalo(etiqueta, dias, validador=None, permitir_cruce=False, fin_opcional=False):
    """
    Pide hora de inicio y de fin (ambas obligatorias, salvo con fin_opcional=True: Enter en el
    fin significa 'sin hora de fin' y se devuelve None como fin; se usa en los hábitos).
    Si se recibe un 'validador', lo ejecuta y vuelve a pedir el intervalo mientras devuelva un error.
    Con permitir_cruce=True el fin puede ser anterior al inicio (cruza la medianoche).
    Devuelve (inicio, fin) en minutos (fin puede ser None si fin_opcional=True).
    """
    while True:
        inicio = pedir_hora(f"[{etiqueta}] Hora de inicio (HH:MM)\n> ")
        if fin_opcional:
            fin = pedir_hora_fin_opcional(
                f"[{etiqueta}] Hora de fin (HH:MM, o Enter si varía / no tiene hora de fin)\n> ", inicio
            )
        else:
            fin = pedir_hora_fin(f"[{etiqueta}] Hora de fin (HH:MM)\n> ", inicio, permitir_cruce)
        error = validador(dias, inicio, fin) if validador else None
        if error is None:
            return inicio, fin
        print(error)

def pedir_horarios(nombre, dias):
    """
    Pide el horario de una rutina: el mismo para todos sus días o uno distinto por día.
    El fin puede ser anterior al inicio (rutinas que cruzan la medianoche, como Dormir).
    Devuelve un diccionario {indice_dia: (inicio, fin)}.
    """
    cambia = len(dias) > 1 and pedir_si_no(f"¿'{nombre}' cambia de horario según el día?")
    if cambia:
        return {d: pedir_intervalo(f"{nombre} - {DIAS_SEMANA[d]}", [d], permitir_cruce=True) for d in dias}
    intervalo = pedir_intervalo(nombre, dias, permitir_cruce=True)
    return {d: intervalo for d in dias}

# --- 3.5 Salida: tablas de texto -----------------------------------------------


def imprimir_tabla(encabezados, filas):
    """Imprime una tabla de texto con bordes estilo Markdown (+----+), columnas al ancho del contenido."""
    anchos = [max(len(str(x)) for x in columna) for columna in zip(encabezados, *filas)]
    borde = "+" + "+".join("-" * (a + 2) for a in anchos) + "+"

    def linea(celdas):
        return "| " + " | ".join(str(c).ljust(a) for c, a in zip(celdas, anchos)) + " |"

    print(borde)
    print(linea(encabezados))
    print(borde)
    for fila in filas:
        print(linea(fila))
    print(borde)

# --- 3.6 Bloques horarios y sueño (formato de la BD <-> formato de pantalla) ---


def expandir_bloques(horarios):
    """
    Convierte {dia: (inicio, fin)} en una lista de bloques (dia, inicio, fin) sin cruces de medianoche.
    Si fin < inicio, el horario se parte en dos: del inicio a 23:59 de ese día y de 00:00 al fin
    del día siguiente (la semana es circular: después del Domingo viene el Lunes).
    Un fin a las 00:00 se trata como "hasta el final del día".
    """
    bloques = []
    for dia, (inicio, fin) in horarios.items():
        if fin > inicio:
            bloques.append((dia, inicio, fin))
        else:
            bloques.append((dia, inicio, ULTIMO_MINUTO))
            if fin > 0:
                bloques.append(((dia + 1) % 7, 0, fin))
    return bloques

def reconstruir_horarios(bloques):
    """
    Deshace lo que hace expandir_bloques: une los tramos partidos en la medianoche.
    Un tramo que termina a las 23:59 se une con el tramo del día siguiente que empieza a las 00:00.
    Devuelve una lista ordenada de (dia, inicio, fin); si fin < inicio, el horario cruza la medianoche.
    """
    bloques = sorted(set(bloques))
    continuaciones = {}  # tramo de inicio -> tramo del día siguiente que lo continúa
    usados = set()
    for b in bloques:
        dia, inicio, fin = b
        if inicio > 0 and fin == ULTIMO_MINUTO:
            siguiente = next((c for c in bloques
                              if c[0] == (dia + 1) % 7 and c[1] == 0 and c not in usados), None)
            if siguiente:
                continuaciones[b] = siguiente
                usados.add(siguiente)

    horarios = []
    for b in bloques:
        if b in usados:
            continue
        dia, inicio, fin = b
        horarios.append((dia, inicio, continuaciones[b][2] if b in continuaciones else fin))
    return horarios

def agrupar_por_horario(horarios):
    """Agrupa [(dia, inicio, fin)] por horario igual: devuelve [((inicio, fin), [dias])] en orden."""
    grupos = {}
    for dia, inicio, fin in horarios:
        grupos.setdefault((inicio, fin), []).append(dia)
    return [(h, sorted(dias)) for h, dias in grupos.items()]

def bloques_sueno(horas):
    """
    Convierte {dia: (despertar, dormir)} en bloques de sueño (dia, inicio, fin) dentro del mismo día:
      - despertar definido: bloque de 00:00 a la hora de despertar (el sueño de la noche anterior);
      - dormir definido: bloque de la hora de dormir a 23:59 (el sueño de esa noche).
    Cada hora es independiente: un día puede tener solo una de las dos.
    """
    bloques = []
    for dia, (despertar, dormir) in horas.items():
        if despertar is not None and despertar > 0:
            bloques.append((dia, 0, despertar))
        if dormir is not None and dormir < ULTIMO_MINUTO:
            bloques.append((dia, dormir, ULTIMO_MINUTO))
    return bloques

def horas_sueno_por_dia(rutinas):
    """
    Lee la rutina 'Dormir' y devuelve {dia: (despertar, dormir)} para los 7 días (None si no hay dato).
    Despertar = fin del tramo que empieza a las 00:00; dormir = inicio del tramo que termina a las 23:59.
    """
    horas = {d: [None, None] for d in range(7)}
    for rutina in rutinas:
        if rutina["nombre"].lower() != "dormir":
            continue
        for dia, inicio, fin in rutina["bloques"]:
            if inicio == 0:
                horas[dia][0] = fin
            elif fin == ULTIMO_MINUTO:
                horas[dia][1] = inicio
    return {d: tuple(v) for d, v in horas.items()}


# =============================================================================
# 4. MOTOR DEL ALGORITMO: ventanas, restricciones, competencia y propuestas
# =============================================================================

# --- 4.1 Datos vigentes y restricciones (algoritmo.md, secciones 7 y 8) --------


def aciertos_recientes(conexion, habito_id):
    """Aciertos del hábito en sus últimos REGISTROS_VIGENTES registros cumplidos (desempate de la sección 6)."""
    fechas = sorted({f for f, _, cumplio, _ in ultimos_registros(conexion, habito_id) if cumplio == 1})
    if not fechas:
        return 0
    marcas = ",".join("?" * len(fechas))
    return conexion.execute(
        f"SELECT COUNT(*) FROM predicciones WHERE habito_id = ? AND acierto = 1 AND fecha IN ({marcas})",
        (habito_id, *fechas),
    ).fetchone()[0]

def bloques_del_dia(rutinas, dia):
    """
    Bloques restringidos (inicio, fin) de un día de la semana: todas las rutinas fijas activas, incluida
    'Dormir' (algoritmo.md, sección 8, puntos 1 y 2).
    """
    return sorted((i, f) for r in rutinas for (d, i, f) in r["bloques"] if d == dia)

def recortar_ventana(inicio, fin, central, bloques):
    """
    Recorta una ventana que invade un bloque restringido (sección 8, punto 3). Devuelve (inicio, fin), o
    None si un bloque cubre la hora central o no queda ventana.
    """
    for b_ini, b_fin in bloques:
        if b_ini <= central < b_fin:
            return None
        if b_fin <= inicio or b_ini >= fin:
            continue
        if b_ini > central:
            fin = min(fin, b_ini)
        else:
            inicio = max(inicio, b_fin)
    return (inicio, fin) if inicio <= fin else None

# --- 4.2 Ventana recomendada y búsqueda de huecos (secciones 3 y 5) ------------


def ventana_recomendada(conexion, habito_id):
    """
    Ventana recomendada vigente de un hábito (sin restricciones aplicadas): el horario que no esté 'malo',
    prefiriendo 'bueno' > 'en_prueba' > 'declarado' y, a igualdad, el más reciente. Devuelve un diccionario
    {horario_id, central, margen, estado, inicio, fin} o None si el hábito no tiene ningún horario utilizable.
    """
    fila = conexion.execute(
        "SELECT id, hora_central, margen, estado FROM horarios_habito "
        "WHERE habito_id = ? AND estado != 'malo' "
        "ORDER BY CASE estado WHEN 'bueno' THEN 0 WHEN 'en_prueba' THEN 1 ELSE 2 END, id DESC LIMIT 1",
        (habito_id,),
    ).fetchone()
    if fila is None:
        return None
    horario_id, central, margen, estado = fila
    inicio, fin = limites_ventana(central, margen)
    return {"horario_id": horario_id, "central": central, "margen": margen, "estado": estado,
            "inicio": inicio, "fin": fin}

def _choque(tramo, ocupados):
    """Nombre del primer hábito ya asignado cuyo tramo (inicio, fin, nombre) se traslapa con 'tramo', o None."""
    for ini, fin, nombre in ocupados:
        if tramo[0] < fin and ini < tramo[1]:
            return nombre
    return None

def buscar_hueco(conexion, habito_id, v, duracion, bloques, ocupados, evitar_hora=None):
    """
    Exploración por huecos libres (algoritmo.md, sección 5, paso 3): prueba horarios a ±1 h, ±2 h, ... del
    original, alternando antes y después. Un hueco sirve si cabe dentro del día, admite ventana + duración, no
    toca rutinas ni hábitos ya asignados y no cae en un horario marcado 'malo'. Si se indica 'evitar_hora', la
    ventana tampoco puede contenerla. Devuelve (inicio, fin, central) o None.
    """
    malos = conexion.execute(
        "SELECT hora_central, margen FROM horarios_habito WHERE habito_id = ? AND estado = 'malo'", (habito_id,)
    ).fetchall()
    for k in range(1, BUSQUEDA_MAX_HORAS + 1):
        for signo in (-1, 1):
            central = v["central"] + signo * 60 * k
            inicio, fin = central - v["margen"], central + v["margen"]
            if inicio < 0 or fin + duracion > ULTIMO_MINUTO:
                continue
            tramo = (inicio, fin + duracion)
            if any(tramo[0] < b_fin and b_ini < tramo[1] for b_ini, b_fin in bloques):
                continue
            if _choque(tramo, ocupados) is not None:
                continue
            if any(abs(central - c) <= m for c, m in malos):
                continue
            if evitar_hora is not None and inicio <= evitar_hora <= fin:
                continue
            return inicio, fin, central
    return None

# --- 4.3 Plan del día y competencia entre hábitos (sección 6) ------------------


def plan_del_dia(conexion, usuario_id, fecha):
    """
    Ventana recomendada de cada hábito activo programado en 'fecha' (un date), aplicando las restricciones
    (sección 8) y la competencia entre hábitos (sección 6): los hábitos se atienden de mayor a menor
    importancia y, a igualdad, con más aciertos recientes; el que choca con una rutina o con otro hábito ya
    asignado busca un hueco alternativo. Devuelve {habito_id: {habito, inicio, fin, estado, nota}} con
    estado 'ok', 'recortada', 'movida', 'sin_hueco' o 'sin_horario'.
    """
    dia = fecha.weekday()
    habitos = [h for h in obtener_habitos(conexion, usuario_id) if dia in h["dias"]]
    bloques = bloques_del_dia(cargar_rutinas(conexion, usuario_id), dia)
    for h in habitos:
        h["aciertos"] = aciertos_recientes(conexion, h["id"])
    plan, ocupados = {}, []
    for h in sorted(habitos, key=lambda h: (-h["peso"], -h["aciertos"], h["id"])):
        entrada = {"habito": h, "inicio": None, "fin": None, "estado": "sin_horario", "nota": ""}
        plan[h["id"]] = entrada
        v = ventana_recomendada(conexion, h["id"])
        if v is None:
            continue
        duracion = h["duracion"] or DURACION_POR_DEFECTO
        ventana = recortar_ventana(v["inicio"], v["fin"], v["central"], bloques)
        rival = _choque((ventana[0], ventana[1] + duracion), ocupados) if ventana else None
        if ventana and rival is None:
            entrada.update(inicio=ventana[0], fin=ventana[1], estado="ok")
            if ventana != (v["inicio"], v["fin"]):
                entrada.update(estado="recortada", nota="Recortada por una rutina")
        else:
            motivo = f"cede ante {rival}" if rival else "choca con una rutina"
            hueco = buscar_hueco(conexion, h["id"], v, duracion, bloques, ocupados)
            if hueco is None:
                entrada.update(estado="sin_hueco", nota=f"{motivo}; sin hueco, revisa importancias o rutinas")
                continue
            entrada.update(inicio=hueco[0], fin=hueco[1], estado="movida", nota=f"Movida: {motivo}")
        ocupados.append((entrada["inicio"], entrada["fin"] + duracion, h["nombre"]))
    return plan

# --- 4.4 Propuesta de un horario alternativo (sección 5) -----------------------


def proximo_dia_programado(dias, desde):
    """Primer día posterior a 'desde' (un date) cuyo día de la semana está en 'dias', o None."""
    for k in range(1, 8):
        dia = desde + timedelta(days=k)
        if dia.weekday() in dias:
            return dia
    return None

def proponer_horario(conexion, usuario_id, habito_id, hora_real):
    """
    Propone una hora distinta para un hábito cuando la emoción fue negativa (algoritmo.md, sección 5): busca
    un hueco libre en el siguiente día programado (sin chocar con rutinas, Dormir ni otros hábitos, sin tocar
    horarios 'malos' ni la hora en que se acaba de hacer) y lo guarda como horario 'en_prueba' con margen de 30 min.
    También crea la predicción de ese día en esa hora y su recordatorio en el muro. Si ya hay un horario
    'en_prueba' sin evaluar, se vuelve a recomendar ese. Devuelve (fecha, hora_central) o None si no hay hueco.
    """
    habito = next((h for h in obtener_habitos(conexion, usuario_id) if h["id"] == habito_id), None)
    objetivo = proximo_dia_programado(habito["dias"], date.today()) if habito else None
    if objetivo is None:
        return None
    existente = conexion.execute(
        "SELECT id, hora_central, margen FROM horarios_habito WHERE habito_id = ? AND estado = 'en_prueba' "
        "ORDER BY id DESC LIMIT 1", (habito_id,),
    ).fetchone()
    if existente:
        horario_id, central, margen = existente
        inicio, fin = limites_ventana(central, margen)
    else:
        bloques = bloques_del_dia(cargar_rutinas(conexion, usuario_id), objetivo.weekday())
        ocupados = [
            (e["inicio"], e["fin"] + (e["habito"]["duracion"] or DURACION_POR_DEFECTO), e["habito"]["nombre"])
            for e in plan_del_dia(conexion, usuario_id, objetivo).values()
            if e["habito"]["id"] != habito_id and e["inicio"] is not None
        ]
        vigente = ventana_recomendada(conexion, habito_id)
        base = {"central": vigente["central"] if vigente else hora_real, "margen": MARGEN_INICIAL}
        hueco = buscar_hueco(conexion, habito_id, base, habito["duracion"] or DURACION_POR_DEFECTO,
                             bloques, ocupados, evitar_hora=hora_real)
        if hueco is None:
            return None
        inicio, fin, central = hueco
    dia = objetivo.isoformat()
    with conexion:
        if not existente:
            horario_id = conexion.execute(
                "INSERT INTO horarios_habito (habito_id, hora_central, margen, estado) VALUES (?, ?, ?, 'en_prueba')",
                (habito_id, central, MARGEN_INICIAL),
            ).lastrowid
        actualizadas = conexion.execute(
            "UPDATE predicciones SET horario_id = ?, hora_predicha_inicio = ?, hora_predicha_final = ?, "
            "acierto = NULL WHERE habito_id = ? AND fecha = ?", (horario_id, inicio, fin, habito_id, dia),
        ).rowcount
        if actualizadas == 0:
            conexion.execute(
                "INSERT INTO predicciones (habito_id, horario_id, fecha, hora_predicha_inicio, "
                "hora_predicha_final) VALUES (?, ?, ?, ?, ?)", (habito_id, horario_id, dia, inicio, fin),
            )
        crear_recordatorio(conexion, habito_id, dia)
    return objetivo, central


# =============================================================================
# 5. REGISTRO DE USUARIO (flujo.md, secciones 2 y 4)
# =============================================================================

# --- 5.1 Cuenta: usuario y contraseña ------------------------------------------


def pedir_usuario_nuevo(conexion):
    """Pide un nombre de usuario válido (3-30 caracteres) que todavía no exista en la BD."""
    while True:
        usuario = input("Ingresa tu nombre de usuario (3-30 letras, números, _ o .)\n> ").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.]{3,30}", usuario):
            print("Nombre inválido: usa solo letras, números, _ o . (3 a 30 caracteres).")
            continue
        existe = conexion.execute(
            "SELECT 1 FROM usuarios WHERE LOWER(usuario) = LOWER(?)", (usuario,)
        ).fetchone()
        if existe:
            print("Ese nombre de usuario ya existe. Elige otro.")
            continue
        return usuario

def pedir_contrasena_nueva():
    """Pide la contraseña (oculta) y su confirmación. Repite todo hasta que sea larga y coincida."""
    while True:
        contrasena = getpass(f"Ingresa tu contraseña (mínimo {MIN_LONGITUD_CONTRASENA} caracteres)\n> ")
        if len(contrasena) < MIN_LONGITUD_CONTRASENA:
            print(f"La contraseña debe tener al menos {MIN_LONGITUD_CONTRASENA} caracteres.")
            continue
        confirmacion = getpass("Confirma tu contraseña por favor\n> ")
        if contrasena == confirmacion:
            return contrasena
        print("Las contraseñas no coinciden. Intenta de nuevo.")

# --- 5.2 Configuración inicial: sueño, rutinas y hábitos -----------------------


def crear_validador_habitos(rutinas):
    """
    Construye la función que valida el horario de un hábito (flujo.md 4.4):
    no debe solaparse con ninguna rutina fija (incluida Dormir) en los días indicados.
    La función devuelta recibe (dias, inicio, fin) y regresa None si es válido o un mensaje de error.
    Si fin es None (hábito sin hora de fin), solo se comprueba que la hora de inicio no caiga
    dentro de una rutina.
    """
    def validador(dias, inicio, fin):
        if fin is None:
            fin = inicio + 1
        for d in dias:
            for rutina in rutinas:
                for dia_bloque, r_inicio, r_fin in rutina["bloques"]:
                    if dia_bloque == d and inicio < r_fin and r_inicio < fin:
                        return (f"Coincide con tu rutina '{rutina['nombre']}' el {DIAS_SEMANA[d]} "
                                f"({a_texto(r_inicio)}-{a_texto(r_fin)}). Elige otro horario.")
        return None

    return validador

def pedir_horas_sueno_dia(etiqueta):
    """
    Pide, en este orden, la hora de despertar y la de dormir de un día (o de 'normalmente').
    Cada una es opcional de forma independiente: Enter = no definida (devuelve None).
    Si se dan las dos, dormir debe ser posterior a despertar del mismo día; si no, se piden de nuevo.
    Devuelve (despertar, dormir).
    """
    while True:
        despertar = pedir_hora_opcional(
            f"¿A qué hora te despiertas {etiqueta}? (HH:MM, o Enter si no la tienes definida)\n> "
        )
        dormir = pedir_hora_opcional(
            f"¿A qué hora te duermes {etiqueta}? (HH:MM, o Enter si no la tienes definida)\n> "
        )
        if despertar is None or dormir is None or dormir > despertar:
            return despertar, dormir
        print("Tu hora de dormir debe ser posterior a tu hora de despertar del mismo día.\n"
              "Si te duermes pasada la medianoche, escribe 23:59.")

def pedir_rutina_dormir():
    """
    Pregunta si el usuario quiere registrar su horario de dormir y lo convierte en la rutina fija
    'Dormir' (así el algoritmo no recomienda nada mientras duerme). Siempre se pide primero la
    hora de despertar y luego la de dormir:
      - si el horario varía cada día: se piden las dos horas de cada día de la semana;
      - si no varía: se piden una sola vez y aplican a los 7 días.
    Cada hora es opcional (Enter). Devuelve la rutina {"nombre", "bloques"} o None si no se
    registró ninguna hora.
    """
    print("\nTu horario de dormir (para que Adaptia no te recomiende nada mientras duermes)")
    if not pedir_si_no("¿Quieres registrar tu horario de dormir?"):
        return None

    if pedir_si_no("¿Tu horario de dormir varía cada día?"):
        horas = {d: pedir_horas_sueno_dia(f"el {dia}") for d, dia in enumerate(DIAS_SEMANA)}
    else:
        despertar, dormir = pedir_horas_sueno_dia("normalmente")
        horas = {d: (despertar, dormir) for d in range(7)}

    bloques = bloques_sueno(horas)
    if not bloques:
        print("No registraste ninguna hora; no se guardó el horario de dormir.")
        return None
    return {"nombre": "Dormir", "bloques": bloques}

def recolectar_rutinas(previas):
    """
    Pregunta cuántas rutinas fijas más tiene el usuario y, por cada una, su nombre, días y horario.
    'previas' son las rutinas ya capturadas (ej. Dormir): sus nombres no se pueden repetir.
    El horario puede cruzar la medianoche y variar según el día.
    Devuelve solo las rutinas nuevas, como {"nombre", "dias": {dia: (inicio, fin)},
    "bloques": [(dia, inicio, fin)]}, donde "bloques" es el horario partido en tramos que no
    cruzan medianoche. No toca la BD.
    """
    cantidad = pedir_entero(
        f"\n¿Cuántas rutinas más tienes? (lo que haces y no puedes cambiar; 0 a {MAX_RUTINAS})\n> ",
        0, MAX_RUTINAS,
    )
    rutinas, nombres = [], {r["nombre"].lower() for r in previas}
    for i in range(cantidad):
        nombre = pedir_nombre_unico(f"Escribe tu rutina {i + 1}\n> ", nombres)
        nombres.add(nombre.lower())
        dias = pedir_dias(nombre)
        horarios = pedir_horarios(nombre, dias)
        rutinas.append({"nombre": nombre, "dias": horarios, "bloques": expandir_bloques(horarios)})
    return rutinas

def construir_habito(nombre, peso, dias, inicio, fin):
    """
    Arma el diccionario de un hábito a partir de su horario declarado.
    Si fin es None (el hábito no tiene hora de fin fija), la duración queda en None y la hora
    central es la hora de inicio; el algoritmo la irá aprendiendo con los registros.
    """
    if fin is None:
        duracion, central = None, inicio
    else:
        duracion, central = fin - inicio, (inicio + fin) // 2
    return {
        "nombre": nombre, "peso": peso, "dias": dias, "hora_esperada": inicio,
        "duracion": duracion, "hora_central": central,
    }

def recolectar_habitos(validador):
    """
    Pregunta cuántos hábitos quiere el usuario y, por cada uno: nombre, importancia, días y UN
    horario (igual para todos los días). El horario se valida con 'validador' (sin choques con
    rutinas ni fuera de las horas despierto). Devuelve una lista de diccionarios con los datos
    del hábito y su horario declarado, usado para la primera predicción. No toca la BD.
    """
    cantidad = pedir_entero(
        f"\n¿Cuántos hábitos quieres generar? (como leer, estudiar, etc; 0 a {MAX_HABITOS})\n> ",
        0, MAX_HABITOS,
    )
    habitos, nombres = [], set()
    for i in range(cantidad):
        nombre = pedir_nombre_unico(f"Escribe tu hábito {i + 1}\n> ", nombres)
        nombres.add(nombre.lower())
        peso = pedir_entero(f"Del 1 al 10, ¿qué tan importante es el hábito '{nombre}'?\n> ", 1, 10)
        dias = pedir_dias(nombre)
        inicio, fin = pedir_intervalo(nombre, dias, validador, fin_opcional=True)
        habitos.append(construir_habito(nombre, peso, dias, inicio, fin))
    return habitos

# --- 5.3 Guardado y flujo completo ---------------------------------------------


def guardar_usuario_completo(conexion, usuario, contrasena, rutinas, habitos):
    """
    Guarda al usuario y toda su configuración en UNA sola transacción
    usuarios, rutinas_fijas, rutina_dias, habitos, habito_dias, horarios_habito y predicciones.
    Si algo falla, se deshace todo (rollback) y no queda nada a medias.
    Devuelve el id del usuario creado.
    """
    ahora = ahora_iso()
    hoy = ahora[:10]

    with conexion:  # commit si todo sale bien, rollback si hay error
        # Usuario (la contraseña se guarda como hash).
        cur = conexion.execute(
            "INSERT INTO usuarios (usuario, contrasena_hash, ultimo_login) VALUES (?, ?, ?)",
            (usuario, hashear_contrasena(contrasena), ahora),
        )
        usuario_id = cur.lastrowid

        for rutina in rutinas:
            insertar_rutina(conexion, usuario_id, rutina)

        for habito in habitos:
            insertar_habito(conexion, usuario_id, habito, hoy)
    return usuario_id

def registrarse(conexion):
    """
    Flujo completo de registro (flujo.md, secciones 2 y 4):
    cuenta -> horario de dormir -> otras rutinas fijas -> hábitos -> guardado.
    Devuelve el id del usuario creado, o None si no se pudo guardar.
    """
    usuario = pedir_usuario_nuevo(conexion)
    contrasena = pedir_contrasena_nueva()
    print(f"\n\n***** Bienvenid@ {usuario}, configuremos tu perfil *****\n")

    dormir = pedir_rutina_dormir()
    rutinas = ([dormir] if dormir else [])
    rutinas += recolectar_rutinas(rutinas)
    habitos = recolectar_habitos(crear_validador_habitos(rutinas))

    try:
        usuario_id = guardar_usuario_completo(conexion, usuario, contrasena, rutinas, habitos)
    except sqlite3.IntegrityError as error:
        # UNIQUE en usuarios.usuario = nombre repetido; cualquier otra restricción se muestra tal cual.
        if "usuarios.usuario" in str(error):
            print("No se pudo registrar: ese usuario ya existe. No se guardó nada.")
        else:
            print(f"No se pudo registrar, la base de datos rechazó los datos: {error}\nNo se guardó nada.")
        return None
    except sqlite3.Error as error:
        print(f"Error de base de datos, no se guardó nada: {error}")
        return None

    print(f"\nPerfil guardado: {len(rutinas)} rutina(s) y {len(habitos)} hábito(s).")
    print("\nBienvenid@ a AdaptIA, la aplicación que te ayuda a tener una rutina "
          "que se adapte a ti, no tú a ella\n")
    return usuario_id


# =============================================================================
# 6. INICIO DE SESIÓN (flujo.md, sección 3)
# =============================================================================


def inicio_sesion(conexion):
    """
    Flujo de inicio de sesión (flujo.md, sección 3), con un máximo de MAX_INTENTOS_LOGIN intentos.
    Si las credenciales son correctas, actualiza ultimo_login y devuelve (id del usuario, ultimo_login anterior).
    Si no, devuelve (None, None). El mensaje de error es genérico para no revelar si falló el usuario
    o la contraseña.
    """
    for intento in range(1, MAX_INTENTOS_LOGIN + 1):
        usuario = pedir_texto("Ingresa tu nombre de usuario\n> ", maximo=30)
        contrasena = getpass("Ingresa tu contraseña\n> ")

        fila = conexion.execute(
            "SELECT id, contrasena_hash, ultimo_login FROM usuarios WHERE LOWER(usuario) = LOWER(?)", (usuario,)
        ).fetchone()

        if fila and verificar_contrasena(contrasena, fila[1]):
            with conexion:
                conexion.execute(
                    "UPDATE usuarios SET ultimo_login = ? WHERE id = ?",
                    (ahora_iso(), fila[0]),
                )
            print(f"\n\n***** Bienvenid@ {usuario}, puedes continuar en tu perfil *****\n")
            return fila[0], fila[2]

        print(f"Usuario o contraseña incorrectos (intento {intento}/{MAX_INTENTOS_LOGIN}).")

    print("Demasiados intentos fallidos.")
    return None, None


# =============================================================================
# 7. REGISTRO DE HÁBITOS: cumplido / no hecho (flujo.md, 6.1)
# =============================================================================


def marcar_no_hecho(conexion, habito_id, fecha):
    """
    Deja constancia de 'no hecho' (registro con cumplio = -1, sin hora ni emoción) para ese hábito y día,
    si todavía no existe. No hace commit: debe llamarse dentro de un 'with conexion:'.
    """
    existe = conexion.execute(
        "SELECT 1 FROM registros WHERE habito_id = ? AND fecha = ? AND cumplio = -1",
        (habito_id, fecha.isoformat()),
    ).fetchone()
    if not existe:
        conexion.execute(
            "INSERT INTO registros (habito_id, fecha, hora, cumplio, emocion) VALUES (?, ?, NULL, -1, NULL)",
            (habito_id, fecha.isoformat()),
        )

def guardar_cumplimiento(conexion, usuario_id, habito_id, fecha, hora, emocion):
    """
    Guarda que un hábito se cumplió en 'fecha' (un date) a las 'hora' (minutos) con 'emocion' (1-5):
    inserta el registro (cumplio = 1), marca el acierto en predicciones según la ventana del plan de ese día
    y cierra el recordatorio de esa fecha, si existía. Devuelve (ventana, acierto); ambos None si ese día
    el hábito no tenía ventana. El ajuste de márgenes y horarios (casos A-G) es la etapa V1 del algoritmo.
    """
    entrada = plan_del_dia(conexion, usuario_id, fecha).get(habito_id)
    ventana = (entrada["inicio"], entrada["fin"]) if entrada and entrada["inicio"] is not None else None
    acierto = None if ventana is None else int(ventana[0] <= hora <= ventana[1])
    dia = fecha.isoformat()
    vigente = ventana_recomendada(conexion, habito_id)
    with conexion:
        # Horario propuesto 'en_prueba' y cumplido dentro de su ventana (algoritmo.md, sección 5): una emoción
        # positiva lo vuelve 'bueno' y una negativa 'malo'; la neutral lo deja en prueba.
        if acierto and vigente and vigente["estado"] == "en_prueba" and emocion != 3:
            conexion.execute(
                "UPDATE horarios_habito SET estado = ?, ultima_vez = ? WHERE id = ?",
                ("bueno" if emocion >= 4 else "malo", dia, vigente["horario_id"]),
            )
        conexion.execute(
            "INSERT INTO registros (habito_id, fecha, hora, cumplio, emocion) VALUES (?, ?, ?, 1, ?)",
            (habito_id, dia, hora, emocion),
        )
        if ventana is not None:
            # Una predicción por hábito y día: se actualiza la de esa fecha o se crea si no existía.
            actualizadas = conexion.execute(
                "UPDATE predicciones SET hora_predicha_inicio = ?, hora_predicha_final = ?, acierto = ? "
                "WHERE habito_id = ? AND fecha = ?", (ventana[0], ventana[1], acierto, habito_id, dia),
            ).rowcount
            if actualizadas == 0:
                conexion.execute(
                    "INSERT INTO predicciones (habito_id, horario_id, fecha, hora_predicha_inicio, "
                    "hora_predicha_final, acierto) VALUES (?, ?, ?, ?, ?, ?)",
                    (habito_id, vigente["horario_id"] if vigente else None, dia, ventana[0], ventana[1], acierto),
                )
        conexion.execute(
            "UPDATE recordatorios SET estado = 'hecho', fecha_resuelto = ? "
            "WHERE habito_id = ? AND fecha_objetivo = ? AND estado IN ('pendiente', 'pospuesto')",
            (ahora_iso(), habito_id, dia),
        )
    return ventana, acierto

def recomendar(conexion, usuario_id, habito_id, hora, emocion):
    """
    Muestra la recomendación según la emoción registrada. Con frustración o enojo propone otra hora (que
    queda como predicción y recordatorio del siguiente día programado).
    """
    if emocion in RECOMENDACIONES:
        print("Recomendación: " + RECOMENDACIONES[emocion].format(hora=a_texto(hora)))
        return
    propuesta = proponer_horario(conexion, usuario_id, habito_id, hora)
    if propuesta is None:
        print("Recomendación: la hora a la que haces este hábito puede afectar cómo te sientes, pero no encontré "
              "un hueco libre para otra hora. Revisa las importancias o tus rutinas.")
        return
    fecha, central = propuesta
    print("Recomendación: la hora a la que haces este hábito puede afectar cómo te sientes. "
          f"Prueba hacerlo a las {a_texto(central)} ({DIAS_CORTOS[fecha.weekday()]} {fecha.isoformat()}). "
          "Quedó en tu muro de recordatorios.")

def registrar_habito(conexion, usuario_id, habitos):
    """
    Registrar hábito (flujo.md, 6.1): se elige el hábito y si lo realizó. Si sí: hora real (ahora u otra) y
    emoción. Si no: queda como "no hecho" y entra al muro de recordatorios (algoritmo.md, caso A).
    El registro es siempre de HOY; los días anteriores se resuelven desde el muro.
    """
    if not habitos:
        print("No tienes hábitos activos para registrar.")
        return
    print("\n¿Qué hábito quieres registrar?")
    for i, h in enumerate(habitos, 1):
        print(f"{i}.-{h['nombre']}")
    print("0.-Cancelar")
    opcion = pedir_entero("> ", 0, len(habitos))
    if opcion == 0:
        return
    habito = habitos[opcion - 1]
    hoy = date.today()

    if not pedir_si_no(f"\n¿Realizaste '{habito['nombre']}'?"):
        with conexion:
            marcar_no_hecho(conexion, habito["id"], hoy)
            crear_recordatorio(conexion, habito["id"], hoy.isoformat())
        print(f"Registrado como no hecho. '{habito['nombre']}' queda en tu muro de recordatorios.")
        return

    ya_hecho = conexion.execute(
        "SELECT 1 FROM registros WHERE habito_id = ? AND fecha = ? AND cumplio = 1",
        (habito["id"], hoy.isoformat()),
    ).fetchone()
    if ya_hecho and not pedir_si_no(f"Ya registraste '{habito['nombre']}' hoy. ¿Registrarlo otra vez?"):
        return

    ahora = datetime.now()
    ahora_min = ahora.hour * 60 + ahora.minute
    print(f"\n¿A qué hora lo hiciste?\n1.-AHORA ({a_texto(ahora_min)})\n2.-OTRA HORA")
    hora = ahora_min if pedir_entero("> ", 1, 2) == 1 else pedir_hora("Hora en que lo hiciste (HH:MM)\n> ")
    emocion = pedir_emocion()

    ventana, acierto = guardar_cumplimiento(conexion, usuario_id, habito["id"], hoy, hora, emocion)
    print(f"\nRegistrado: '{habito['nombre']}' a las {a_texto(hora)}.")
    if ventana is None:
        print("Hoy este hábito no tiene una ventana recomendada.")
    else:
        resultado = "Dentro" if acierto else "Fuera"
        print(f"{resultado} de tu ventana recomendada ({texto_intervalo(*ventana)}).")
    recomendar(conexion, usuario_id, habito["id"], hora, emocion)


# =============================================================================
# 8. MURO DE RECORDATORIOS (flujo.md, sección 5)
# =============================================================================


def archivar_vencidos(conexion, usuario_id, hoy):
    """Archiva como "no hecho" los recordatorios sin resolver con DIAS_ARCHIVO días o más (flujo 5.4)."""
    limite = (hoy - timedelta(days=DIAS_ARCHIVO)).isoformat()
    vencidos = conexion.execute(
        "SELECT r.id, r.habito_id, r.fecha_objetivo FROM recordatorios r "
        "JOIN habitos h ON h.id = r.habito_id "
        "WHERE h.usuario_id = ? AND r.estado IN ('pendiente', 'pospuesto') AND r.fecha_objetivo <= ?",
        (usuario_id, limite),
    ).fetchall()
    with conexion:
        for id_, habito_id, fecha in vencidos:
            marcar_no_hecho(conexion, habito_id, date.fromisoformat(fecha))
            conexion.execute(
                "UPDATE recordatorios SET estado = 'archivado', fecha_resuelto = ? WHERE id = ?",
                (ahora_iso(), id_),
            )

def generar_recordatorios(conexion, usuario_id, ultimo_login, hoy):
    """
    Genera el muro al iniciar sesión (flujo.md, 5.1): para cada día desde el último inicio de sesión hasta
    ayer y cada hábito activo programado ese día sin cumplimiento registrado, crea un recordatorio
    pendiente (uno por hábito y día). Los pospuestos vuelven a pendientes y se archivan los vencidos.
    Los hábitos no generan recordatorios de días anteriores a su creación.
    """
    try:
        desde = date.fromisoformat(ultimo_login[:10])
    except (TypeError, ValueError):
        desde = hoy  # primera vez que inicia sesión: no hay días por revisar
    with conexion:
        conexion.execute(
            "UPDATE recordatorios SET estado = 'pendiente' WHERE estado = 'pospuesto' AND habito_id IN "
            "(SELECT id FROM habitos WHERE usuario_id = ?)", (usuario_id,),
        )
        for habito in obtener_habitos(conexion, usuario_id):
            creado = conexion.execute(
                "SELECT fecha_creacion FROM habitos WHERE id = ?", (habito["id"],)
            ).fetchone()[0]
            try:
                dia = max(desde, date.fromisoformat(creado[:10]))
            except (TypeError, ValueError):
                dia = desde
            while dia < hoy:
                cumplido = conexion.execute(
                    "SELECT 1 FROM registros WHERE habito_id = ? AND fecha = ? AND cumplio = 1",
                    (habito["id"], dia.isoformat()),
                ).fetchone()
                if dia.weekday() in habito["dias"] and not cumplido:
                    crear_recordatorio(conexion, habito["id"], dia.isoformat())
                dia += timedelta(days=1)
    archivar_vencidos(conexion, usuario_id, hoy)

def cargar_pendientes(conexion, usuario_id):
    """
    Recordatorios pendientes como (id, habito_id, nombre, importancia, fecha, hora_prueba): mayor importancia
    y más antiguo primero. 'hora_prueba' es la hora central que el algoritmo propone probar ese día (horario
    'en_prueba' de la predicción de esa fecha) o None.
    """
    return conexion.execute(
        "SELECT r.id, r.habito_id, h.habito, h.peso, r.fecha_objetivo, hh.hora_central FROM recordatorios r "
        "JOIN habitos h ON h.id = r.habito_id "
        "LEFT JOIN predicciones p ON p.habito_id = r.habito_id AND p.fecha = r.fecha_objetivo "
        "LEFT JOIN horarios_habito hh ON hh.id = p.horario_id AND hh.estado = 'en_prueba' "
        "WHERE h.usuario_id = ? AND h.activo = 1 AND r.estado = 'pendiente' "
        "ORDER BY h.peso DESC, r.fecha_objetivo ASC, h.habito",
        (usuario_id,),
    ).fetchall()

def resolver_pendiente(conexion, usuario_id, pendiente):
    """Acciones sobre un recordatorio (flujo.md, 5.3): Ya lo hice / Posponer / Descartar."""
    id_, habito_id, nombre, _, fecha, _ = pendiente
    fecha_d = date.fromisoformat(fecha)
    print(f"\n'{nombre}' del {DIAS_CORTOS[fecha_d.weekday()]} {fecha}")
    opcion = pedir_entero("1.-YA LO HICE\n2.-POSPONER\n3.-DESCARTAR\n0.-VOLVER\n> ", 0, 3)
    ahora = ahora_iso()
    futuro = fecha_d > date.today()
    if opcion == 1 and futuro:
        print("Ese día todavía no llega; podrás registrarlo cuando llegue.")
    elif opcion == 1:
        hora = pedir_hora("¿A qué hora lo hiciste? (HH:MM)\n> ")
        emocion = pedir_emocion()
        guardar_cumplimiento(conexion, usuario_id, habito_id, fecha_d, hora, emocion)
        print(f"Registrado. '{nombre}' sale del muro.")
        recomendar(conexion, usuario_id, habito_id, hora, emocion)
    elif opcion == 2:
        with conexion:
            conexion.execute("UPDATE recordatorios SET estado = 'pospuesto' WHERE id = ?", (id_,))
        print("Pospuesto: volverá a aparecer en tu siguiente inicio de sesión.")
    elif opcion == 3:
        with conexion:
            if not futuro:  # un día que aún no llega no puede quedar como "no hecho"
                marcar_no_hecho(conexion, habito_id, fecha_d)
            conexion.execute(
                "UPDATE recordatorios SET estado = 'descartado', fecha_resuelto = ? WHERE id = ?", (ahora, id_)
            )
        print("Descartado." if futuro else "Descartado: se conserva como no hecho.")

def muro_de_recordatorios(conexion, usuario_id):
    """
    Muro de recordatorios (flujo.md, 5.2): sección 'Pendientes' (si hay) y sección 'Hoy'. Permite resolver
    los pendientes uno por uno; con 0 se continúa al menú.
    """
    while True:
        pendientes = cargar_pendientes(conexion, usuario_id)
        print("\n" + "=" * 50)
        print("MURO DE RECORDATORIOS")
        print("=" * 50)
        if pendientes:
            print("PENDIENTES")
            imprimir_tabla(
                ("#", "Hábito", "Imp.", "Día", "Recomendación"),
                [(i, nombre, peso, f"{DIAS_CORTOS[date.fromisoformat(f).weekday()]} {f}",
                  f"Prueba a las {a_texto(prueba)}" if prueba is not None else "-")
                 for i, (_, _, nombre, peso, f, prueba) in enumerate(pendientes, 1)],
            )
            print()
        print("HOY")
        mostrar_plan_hoy(conexion, usuario_id)
        if not pendientes:
            return
        opcion = pedir_entero("Elige un pendiente para resolverlo (0 para continuar)\n> ", 0, len(pendientes))
        if opcion == 0:
            return
        resolver_pendiente(conexion, usuario_id, pendientes[opcion - 1])


# =============================================================================
# 9. CONSULTAS: tablas, plan de hoy y gráfica de emociones
# =============================================================================

# --- 9.1 Tablas de datos -------------------------------------------------------


def mostrar_tabla_sueno(rutinas):
    """Tabla de despertar y dormir, agrupando los días que comparten las mismas horas."""
    horas = horas_sueno_por_dia(rutinas)
    grupos = {}
    for dia, par in horas.items():
        grupos.setdefault(par, []).append(dia)
    filas = [(formatear_dias(dias), texto_hora(despertar), texto_hora(dormir))
             for (despertar, dormir), dias in grupos.items()]
    imprimir_tabla(("Días", "Despertar", "Dormir"), filas)
    print()

def mostrar_tabla_rutinas(rutinas):
    """
    Tabla de rutinas fijas activas (sin Dormir, que va en su propia tabla): nombre, días y horario.
    Si una rutina tiene horarios distintos según el día, ocupa una fila por cada horario.
    """
    filas = []
    for r in rutinas:
        if r["nombre"].lower() == "dormir":
            continue
        grupos = agrupar_por_horario(reconstruir_horarios(r["bloques"]))
        if not grupos:
            filas.append((r["nombre"], "-", SIN_HORARIO))
        for i, ((inicio, fin), dias) in enumerate(grupos):
            filas.append((r["nombre"] if i == 0 else "", formatear_dias(dias), texto_intervalo(inicio, fin)))
    if not filas:
        print("Aún no tienes rutinas activas (aparte de Dormir). Agrega una desde el menú.\n")
        return
    imprimir_tabla(("Rutina", "Días", "Horario"), filas)
    print()

def mostrar_tabla_habitos(habitos):
    """Muestra la tabla de hábitos activos: nombre, importancia, días, horario, duración y veces cumplido."""
    if not habitos:
        print("Aún no tienes hábitos activos. Agrega uno desde el menú.\n")
        return
    filas = []
    for h in habitos:
        hora, duracion = h["hora_esperada"], h["duracion"]
        if hora is None:
            horario = SIN_HORARIO
        elif duracion is None:
            horario = f"{a_texto(hora)}-variable"  # sin hora de fin fija
        else:
            horario = texto_intervalo(hora, hora + duracion)
        filas.append((
            h["nombre"], h["peso"], formatear_dias(h["dias"]) if h["dias"] else "-",
            horario,
            "Variable" if duracion is None else f"{duracion} min",
            h["veces"],
        ))
    imprimir_tabla(("Hábito", "Imp.", "Días", "Horario", "Duración", "Veces"), filas)
    print()

# --- 9.2 Plan de hoy -----------------------------------------------------------


def nota_recomendacion(conexion, habito_id):
    """
    Recomendación corta para la columna 'Nota' del plan, según la emoción del último cumplimiento del hábito
    (de hoy o de un día anterior). Devuelve '' si el hábito nunca se ha cumplido.
    """
    ultimo = conexion.execute(
        "SELECT hora, emocion FROM registros WHERE habito_id = ? AND cumplio = 1 AND emocion IS NOT NULL "
        "ORDER BY fecha DESC, id DESC LIMIT 1", (habito_id,),
    ).fetchone()
    if ultimo is None:
        return ""
    hora, emocion = ultimo
    if emocion >= 4:
        return f"Sigue a las {a_texto(hora)}"
    if emocion == 3:
        return f"Sigue a las {a_texto(hora)} o prueba otra"
    prueba = conexion.execute(
        "SELECT p.fecha, hh.hora_central FROM predicciones p JOIN horarios_habito hh ON hh.id = p.horario_id "
        "WHERE p.habito_id = ? AND hh.estado = 'en_prueba' AND p.fecha > ? ORDER BY p.fecha LIMIT 1",
        (habito_id, date.today().isoformat()),
    ).fetchone()
    if prueba is None:
        return "Prueba otra hora"
    fecha = date.fromisoformat(prueba[0])
    return f"Prueba a las {a_texto(prueba[1])} ({DIAS_CORTOS[fecha.weekday()]} {fecha.day:02d}/{fecha.month:02d})"

def mostrar_plan_hoy(conexion, usuario_id):
    """
    Tabla del día: para cada hábito programado hoy, su ventana recomendada (con restricciones y competencia
    ya aplicadas), la hora real y su estado (Pendiente / Dentro / Fuera / No hecho), ordenados por hora.
    """
    hoy = date.today().isoformat()
    plan = plan_del_dia(conexion, usuario_id, date.today())
    if not plan:
        print("No tienes hábitos programados para hoy.\n")
        return
    filas = []
    for e in sorted(plan.values(), key=lambda e: (e["inicio"] is None, e["inicio"] or 0, -e["habito"]["peso"])):
        h = e["habito"]
        hecho = conexion.execute(
            "SELECT hora FROM registros WHERE habito_id = ? AND fecha = ? AND cumplio = 1 "
            "ORDER BY id DESC LIMIT 1", (h["id"], hoy),
        ).fetchone()
        no_hecho = conexion.execute(
            "SELECT 1 FROM registros WHERE habito_id = ? AND fecha = ? AND cumplio = -1", (h["id"], hoy)
        ).fetchone()
        ventana = "Sin hueco" if e["estado"] == "sin_hueco" else texto_intervalo(e["inicio"], e["fin"])
        if hecho:
            # Un hábito ya hecho conserva la predicción con la que se evaluó, aunque luego cambie el horario.
            guardada = conexion.execute(
                "SELECT hora_predicha_inicio, hora_predicha_final, acierto FROM predicciones "
                "WHERE habito_id = ? AND fecha = ? ORDER BY id DESC LIMIT 1", (h["id"], hoy),
            ).fetchone()
            if guardada:
                ventana = texto_intervalo(guardada[0], guardada[1])
            estado = "Hecho" if not guardada or guardada[2] is None else ("Dentro" if guardada[2] else "Fuera")
        else:
            estado = "No hecho" if no_hecho else "Pendiente"
        nota = " | ".join(t for t in (nota_recomendacion(conexion, h["id"]), e["nota"]) if t)
        filas.append((h["nombre"], h["peso"], ventana, texto_hora(hecho[0]) if hecho else "-",
                      estado, nota or "-"))
    imprimir_tabla(("Hábito", "Imp.", "Ventana recomendada", "Hora real", "Estado", "Nota"), filas)
    print()

# --- 9.3 Gráfica de emociones --------------------------------------------------


def etiqueta_horas(horas, maximo=3):
    """
    Texto con las horas (minutos desde medianoche) a las que se hizo un hábito, de la más temprana a la más
    tardía, una por línea ('18:24'). Muestra hasta 'maximo' horas distintas y resume el resto con '+N'.
    """
    unicas = sorted(set(horas))
    lineas = [a_texto(h) for h in unicas[:maximo]]
    if len(unicas) > maximo:
        lineas.append(f"+{len(unicas) - maximo}")
    return "\n".join(lineas)


def datos_emocion_por_habito(conexion, usuario_id, dia):
    """
    Cumplimientos de un día de la semana (0 = Lunes ... 6 = Domingo), sumando todas las fechas de ese día,
    agrupados por hábito del usuario: {nombre del hábito: [(emoción 1-5, hora en minutos)]}.
    """
    filas = conexion.execute(
        "SELECT r.fecha, r.hora, r.emocion, h.habito FROM registros r JOIN habitos h ON h.id = r.habito_id "
        "WHERE h.usuario_id = ? AND r.cumplio = 1 AND r.hora IS NOT NULL AND r.emocion IS NOT NULL",
        (usuario_id,),
    ).fetchall()
    datos = {}
    for fecha, hora, emocion, nombre in filas:
        if date.fromisoformat(fecha[:10]).weekday() == dia:
            datos.setdefault(nombre, []).append((emocion, hora))
    return datos


def graficar_emociones(conexion, usuario_id):
    """
    Pregunta qué día de la semana quiere ver el usuario y dibuja con Matplotlib una barra por hábito: su
    altura es la emoción promedio de ese día y sobre cada barra aparece la hora (o las horas) a la que se hizo.
    Los hábitos se ordenan de la emoción más alta a la más baja. Si no hay pantalla disponible, guarda la
    gráfica como imagen PNG.
    """
    try:
        import matplotlib
        import matplotlib.pyplot as plt
    except ImportError:
        print("Para ver la gráfica necesitas Matplotlib. En Fedora: sudo dnf install python3-matplotlib "
              "(o: pip install matplotlib).")
        return

    print("\n¿Qué día quieres ver?")
    for i, nombre in enumerate(DIAS_SEMANA, 1):
        print(f"{i}.-{nombre}")
    print("0.-Cancelar")
    opcion = pedir_entero("> ", 0, 7)
    if opcion == 0:
        return
    dia = opcion - 1
    datos = datos_emocion_por_habito(conexion, usuario_id, dia)
    if not datos:
        print(f"Todavía no hay hábitos cumplidos registrados en {DIAS_SEMANA[dia]}.")
        return

    promedios = {n: sum(e for e, _ in regs) / len(regs) for n, regs in datos.items()}
    habitos = sorted(promedios, key=lambda n: (-promedios[n], n))
    valores = [promedios[n] for n in habitos]
    colores = [plt.get_cmap("RdYlGn")((v - 1) / 4) for v in valores]

    fig, ax = plt.subplots(figsize=(max(8, 1.6 * len(habitos) + 3), 5))
    posiciones = range(len(habitos))
    ax.bar(posiciones, valores, color=colores, width=ANCHO_BARRA, edgecolor="#444444")
    for x, nombre, valor in zip(posiciones, habitos, valores):
        ax.text(x, valor + 0.08, etiqueta_horas([h for _, h in datos[nombre]]),
                ha="center", va="bottom", fontsize=9)
    ax.set_xticks(list(posiciones))
    ax.set_xticklabels(habitos, rotation=20, ha="right")
    ax.set_yticks([valor for _, valor in EMOCIONES])
    ax.set_yticklabels([f"{nombre} ({valor})" for nombre, valor in EMOCIONES])
    ax.set_ylim(0, 6.4)  # espacio sobre las barras altas para las horas
    # Siempre hay espacio para al menos 4 barras, centradas: con pocos hábitos las barras no se ven gordas.
    centro, mitad = (len(habitos) - 1) / 2, max(len(habitos), 4) / 2
    ax.set_xlim(centro - mitad, centro + mitad)
    ax.set_xlabel("Hábito")
    plural = DIAS_SEMANA[dia].lower() + ("" if DIAS_SEMANA[dia].endswith("s") else "s")
    ax.set_title(f"Cómo te sientes los {plural} con cada hábito")
    ax.grid(axis="y", alpha=0.3)
    fig.text(0.99, 0.01, "Hora sobre cada barra = cuándo hiciste el hábito (formato 24 h)",
             ha="right", fontsize=8, color="#555555")
    fig.tight_layout()

    if matplotlib.get_backend().lower() == "agg":  # sin pantalla: no se puede abrir una ventana
        archivo = Path(f"emociones_{DIAS_SEMANA[dia].lower()}.png").resolve()
        fig.savefig(archivo, dpi=150)
        print(f"No hay pantalla disponible; la gráfica se guardó en: {archivo}")
    else:
        print("Cierra la ventana de la gráfica para volver al menú.")
        plt.show()
    plt.close(fig)


# --- 9.4 Submenú «Ver mis datos» -----------------------------------------------


def menu_ver(conexion, usuario_id):
    """Submenú para ver cada tabla por separado (sueño, rutinas, hábitos, gráfica y plan de hoy)."""
    while True:
        opcion = pedir_entero(
            "\n¿Qué quieres ver?\n1.-MI SUEÑO\n2.-MIS RUTINAS\n3.-MIS HÁBITOS\n"
            "4.-GRÁFICA DE EMOCIONES\n5.-PLAN DE HOY\n0.-VOLVER\n> ", 0, 5
        )
        if opcion == 0:
            return
        titulos = {1: "TU SUEÑO", 2: "TUS RUTINAS", 3: "TUS HÁBITOS", 4: "CÓMO TE SIENTES", 5: "PLAN DE HOY"}
        print("\n" + "=" * 50)
        print(titulos[opcion])
        print("=" * 50)
        if opcion == 1:
            mostrar_tabla_sueno(cargar_rutinas(conexion, usuario_id))
        elif opcion == 2:
            mostrar_tabla_rutinas(cargar_rutinas(conexion, usuario_id))
        elif opcion == 3:
            mostrar_tabla_habitos(obtener_habitos(conexion, usuario_id))
        elif opcion == 4:
            graficar_emociones(conexion, usuario_id)
        else:
            mostrar_plan_hoy(conexion, usuario_id)


# =============================================================================
# 10. GESTIÓN: agregar y activar / desactivar (flujo.md, 6.4)
# =============================================================================


def agregar_habito(conexion, usuario_id):
    """Pide los datos de un hábito nuevo (con las mismas validaciones del registro) y lo guarda."""
    activos = conexion.execute(
        "SELECT COUNT(*) FROM habitos WHERE usuario_id = ? AND activo = 1", (usuario_id,)
    ).fetchone()[0]
    if activos >= MAX_HABITOS:
        print(f"Ya tienes el máximo de {MAX_HABITOS} hábitos activos. Quita uno primero.")
        return

    # Se comparan también los hábitos quitados: sus registros siguen en la BD con ese nombre.
    usados = {n.lower() for (n,) in conexion.execute(
        "SELECT habito FROM habitos WHERE usuario_id = ?", (usuario_id,)
    )}
    nombre = pedir_nombre_unico("\nEscribe el nombre del nuevo hábito\n> ", usados)
    peso = pedir_entero(f"Del 1 al 10, ¿qué tan importante es el hábito '{nombre}'?\n> ", 1, 10)
    dias = pedir_dias(nombre)
    validador = crear_validador_habitos(cargar_rutinas(conexion, usuario_id))
    inicio, fin = pedir_intervalo(nombre, dias, validador, fin_opcional=True)
    habito = construir_habito(nombre, peso, dias, inicio, fin)
    with conexion:
        insertar_habito(conexion, usuario_id, habito, date.today().isoformat())
    print(f"Hábito '{nombre}' agregado.")

def agregar_rutina(conexion, usuario_id):
    """Pide los datos de una rutina fija nueva (días y horario, puede cruzar medianoche) y la guarda."""
    activas = cargar_rutinas(conexion, usuario_id)
    if len(activas) >= MAX_RUTINAS:
        print(f"Ya tienes el máximo de {MAX_RUTINAS} rutinas activas. Quita una primero.")
        return

    usadas = {n.lower() for (n,) in conexion.execute(
        "SELECT rutina FROM rutinas_fijas WHERE usuario_id = ?", (usuario_id,)
    )}
    nombre = pedir_nombre_unico("\nEscribe el nombre de la nueva rutina\n> ", usadas)
    dias = pedir_dias(nombre)
    horarios = pedir_horarios(nombre, dias)
    rutina = {"nombre": nombre, "bloques": expandir_bloques(horarios)}
    with conexion:
        insertar_rutina(conexion, usuario_id, rutina)
    print(f"Rutina '{nombre}' agregada.")

def activar_desactivar(conexion, usuario_id):
    """
    Lista las rutinas fijas o los hábitos (activos e inactivos) y alterna el estado del elegido. Al desactivar
    un hábito se archivan sus recordatorios pendientes; el historial y los horarios aprendidos se conservan.
    """
    while True:
        opcion = pedir_entero("\n¿Qué quieres activar o desactivar?\n1.-HÁBITOS\n2.-RUTINAS\n0.-VOLVER\n> ", 0, 2)
        if opcion == 0:
            return
        tabla, columna, limite, tipo = (
            ("habitos", "habito", MAX_HABITOS, "hábitos") if opcion == 1
            else ("rutinas_fijas", "rutina", MAX_RUTINAS, "rutinas")
        )
        filas = conexion.execute(
            f"SELECT id, {columna}, activo FROM {tabla} WHERE usuario_id = ? ORDER BY activo DESC, {columna}",
            (usuario_id,),
        ).fetchall()
        if not filas:
            print(f"No tienes {tipo} registrados.")
            continue
        print()
        for i, (_, nombre, activo) in enumerate(filas, 1):
            print(f"{i}.-{nombre} [{'ACTIVO' if activo else 'INACTIVO'}]")
        print("0.-Cancelar")
        elegido = pedir_entero("> ", 0, len(filas))
        if elegido == 0:
            continue
        id_, nombre, activo = filas[elegido - 1]

        if not activo:
            activos = sum(1 for f in filas if f[2])
            if activos >= limite:
                print(f"Ya tienes el máximo de {limite} {tipo} activos. Desactiva uno primero.")
                continue
            with conexion:
                conexion.execute(f"UPDATE {tabla} SET activo = 1 WHERE id = ?", (id_,))
            print(f"'{nombre}' reactivado.")
        else:
            with conexion:
                conexion.execute(f"UPDATE {tabla} SET activo = 0 WHERE id = ?", (id_,))
                if opcion == 1:
                    conexion.execute(
                        "UPDATE recordatorios SET estado = 'archivado', fecha_resuelto = ? "
                        "WHERE habito_id = ? AND estado IN ('pendiente', 'pospuesto')",
                        (ahora_iso(), id_),
                    )
            print(f"'{nombre}' desactivado. Tu historial se conserva.")


# =============================================================================
# 11. MENÚ PRINCIPAL Y PUNTO DE ENTRADA
# =============================================================================


def menu_principal(conexion, usuario_id):
    """Menú principal: registrar hábitos, muro, consultas, altas y activar/desactivar. Termina con SALIR."""
    while True:
        opcion = pedir_entero(
            "\n1.-REGISTRAR HÁBITO\n2.-MURO DE RECORDATORIOS\n3.-VER MIS DATOS\n4.-AGREGAR HÁBITO\n"
            "5.-AGREGAR RUTINA\n6.-ACTIVAR / DESACTIVAR\n7.-SALIR\n> ", 1, 7
        )
        if opcion == 7:
            break
        try:
            if opcion == 1:
                registrar_habito(conexion, usuario_id, obtener_habitos(conexion, usuario_id))
            elif opcion == 2:
                muro_de_recordatorios(conexion, usuario_id)
            elif opcion == 3:
                menu_ver(conexion, usuario_id)
            elif opcion == 4:
                agregar_habito(conexion, usuario_id)
            elif opcion == 5:
                agregar_rutina(conexion, usuario_id)
            else:
                activar_desactivar(conexion, usuario_id)
        except sqlite3.Error as error:
            print(f"Error de base de datos, no se guardó nada: {error}")

def main():
    """
    Punto de entrada: conecta a la BD y muestra el menú de inicio (iniciar sesión,
    registrarse o salir). Al autenticarse el usuario, abre el menú principal
    (tabla de hábitos, gráfica y gestión de hábitos y rutinas).
    """
    conexion = conectar()
    try:
        while True:
            opcion = pedir_entero("1.-INICIAR SESIÓN\n2.-REGISTRARSE\n3.-SALIR\n> ", 1, 3)
            if opcion == 1:
                usuario_id, ultimo_login = inicio_sesion(conexion)
            elif opcion == 2:
                usuario_id, ultimo_login = registrarse(conexion), None
            else:
                break

            if usuario_id is not None:
                try:
                    if ultimo_login:
                        generar_recordatorios(conexion, usuario_id, ultimo_login, date.today())
                    muro_de_recordatorios(conexion, usuario_id)
                except sqlite3.Error as error:
                    print(f"No se pudo generar el muro de recordatorios: {error}")
                menu_principal(conexion, usuario_id)
                break
    except (KeyboardInterrupt, EOFError):
        print("\nSesión cancelada. No se guardó nada pendiente.")
    finally:
        conexion.close()


if __name__ == "__main__":
    main()