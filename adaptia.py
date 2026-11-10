"""
AdaptIA - Etapa 1: registro, inicio de sesión y menú principal conectados a SQLite.

Organización del archivo:
    1. CONFIGURACIÓN         constantes y parámetros del programa
    2. BASE DE DATOS         conexión y verificación del esquema
    3. UTILIDADES            contraseñas, formato de horas y entrada de datos validada
    4. REGISTRO              captura de datos, validaciones y guardado del usuario nuevo
    5. INICIO DE SESIÓN      verificación de credenciales
    6. MENÚ PRINCIPAL        tabla de hábitos, gráfica y agregar/quitar hábitos y rutinas
    7. PROGRAMA PRINCIPAL    menú de inicio y punto de entrada

Convenciones del esquema:
    - Todas las horas (rutinas y hábitos) se guardan como minutos desde medianoche (0 a 1439).
    - Dormir se registra como una rutina fija más (opcional). Una rutina que cruza la medianoche
      se guarda partida en dos bloques: ej. 23:00-07:00 del lunes = lunes 23:00-23:59 + martes 00:00-07:00.
    - Un hábito tiene UN solo horario, igual para todos sus días (algoritmo.md, sección 9).
    - Los días de la semana se guardan como entero: 0 = Lunes ... 6 = Domingo.
    - La contraseña se guarda como hash PBKDF2 ("sal$hash"), nunca en texto plano.
"""

import hashlib
import hmac
import os
import re
import sqlite3
import sys
from datetime import datetime
from getpass import getpass
from pathlib import Path


# =============================================================================
# 1. CONFIGURACIÓN
# =============================================================================

# Base de datos: por defecto "adaptia-db" junto a este script. Se puede indicar otro archivo
# (por ejemplo una copia de respaldo para pruebas) como argumento:
#     python3 adaptia.py adaptia-db.respaldo
RUTA_BD = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "adaptia-db"

# Nombres de los días; la posición en la lista es el índice que se guarda en la BD.
DIAS_SEMANA = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]

# Límites de captura.
MAX_RUTINAS = 20
MAX_HABITOS = 20
MIN_LONGITUD_CONTRASENA = 8

# Menú principal.
DIAS_CORTOS = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"]
ANCHO_GRAFICA = 30  # largo máximo de la barra más larga, en caracteres

# Seguridad.
MAX_INTENTOS_LOGIN = 3
ITERACIONES_HASH = 200_000

# Algoritmo (algoritmo.md, secciones 3 y 9): margen inicial de un horario, en minutos.
MARGEN_INICIAL = 30

# Columnas que este código escribe en cada tabla. Si el esquema real usa otros
# nombres, hay que corregirlos aquí y en las consultas SQL correspondientes.
COLUMNAS_USADAS = {
    "usuarios": ["usuario", "contrasena_hash", "ultimo_login"],
    "rutinas_fijas": ["usuario_id", "rutina", "activo"],
    "rutina_dias": ["rutina_id", "dia", "hora_inicio", "hora_fin"],
    "habitos": ["usuario_id", "habito", "peso", "duracion", "hora_esperada", "activo"],
    "habito_dias": ["habito_id", "dia"],
    "horarios_habito": ["habito_id", "hora_central", "margen", "estado"],
    "predicciones": ["habito_id", "horario_id", "fecha", "hora_predicha_inicio", "hora_predicha_final"],
}


# =============================================================================
# 2. BASE DE DATOS
# =============================================================================

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


# =============================================================================
# 3. UTILIDADES COMPARTIDAS
# =============================================================================

# --- 3.1 Contraseñas --------------------------------------------------------

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


# --- 3.2 Formato de horas ---------------------------------------------------

def _a_minutos(texto):
    """Convierte 'HH:MM' (24 h) a minutos desde medianoche. Devuelve None si el formato es inválido."""
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", texto)
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def a_texto(minutos):
    """Convierte minutos desde medianoche a texto 'HH:MM' (ejemplo: 450 -> '07:30')."""
    return f"{minutos // 60:02d}:{minutos % 60:02d}"


# --- 3.3 Entrada de datos con validación ------------------------------------

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


# =============================================================================
# 4. REGISTRO
# =============================================================================

# --- 4.1 Cuenta: usuario y contraseña ---------------------------------------

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


# --- 4.2 Configuración inicial: herramientas para días y horarios -----------

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


def pedir_intervalo(etiqueta, dias, validador=None, permitir_cruce=False):
    """
    Pide hora de inicio y de fin (ambas obligatorias).
    Si se recibe un 'validador', lo ejecuta y vuelve a pedir el intervalo mientras devuelva un error.
    Con permitir_cruce=True el fin puede ser anterior al inicio (cruza la medianoche).
    Devuelve (inicio, fin) en minutos.
    """
    while True:
        inicio = pedir_hora(f"[{etiqueta}] Hora de inicio (HH:MM)\n> ")
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
            bloques.append((dia, inicio, 1439))
            if fin > 0:
                bloques.append(((dia + 1) % 7, 0, fin))
    return bloques


def crear_validador_habitos(rutinas):
    """
    Construye la función que valida el horario de un hábito (flujo.md 4.4):
    no debe solaparse con ninguna rutina fija (incluida Dormir) en los días indicados.
    La función devuelta recibe (dias, inicio, fin) y regresa None si es válido o un mensaje de error.
    """
    def validador(dias, inicio, fin):
        for d in dias:
            for rutina in rutinas:
                for dia_bloque, r_inicio, r_fin in rutina["bloques"]:
                    if dia_bloque == d and inicio < r_fin and r_inicio < fin:
                        return (f"Coincide con tu rutina '{rutina['nombre']}' el {DIAS_SEMANA[d]} "
                                f"({a_texto(r_inicio)}-{a_texto(r_fin)}). Elige otro horario.")
        return None

    return validador


# --- 4.3 Configuración inicial: rutinas y hábitos ---------------------------

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
        if dormir is not None and dormir < 1439:
            bloques.append((dia, dormir, 1439))
    return bloques


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
        inicio, fin = pedir_intervalo(nombre, dias, validador)

        habitos.append({
            "nombre": nombre,
            "peso": peso,
            "dias": dias,
            "hora_esperada": inicio,
            "duracion": fin - inicio,
            "hora_central": (inicio + fin) // 2,
        })
    return habitos


# --- 4.4 Guardado en la base de datos ---------------------------------------

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
        (habito_id, cur.lastrowid, hoy,
         max(0, central - MARGEN_INICIAL), min(1439, central + MARGEN_INICIAL)),
    )


def guardar_usuario_completo(conexion, usuario, contrasena, rutinas, habitos):
    """
    Guarda al usuario y toda su configuración en UNA sola transacción
    usuarios, rutinas_fijas, rutina_dias, habitos, habito_dias, horarios_habito y predicciones.
    Si algo falla, se deshace todo (rollback) y no queda nada a medias.
    Devuelve el id del usuario creado.
    """
    ahora = datetime.now().isoformat(timespec="seconds")
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


# --- 4.5 Flujo completo de registro -----------------------------------------

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
    except sqlite3.IntegrityError:
        print("No se pudo registrar: ese usuario ya existe. No se guardó nada.")
        return None
    except sqlite3.Error as error:
        print(f"Error de base de datos, no se guardó nada: {error}")
        return None

    print(f"\nPerfil guardado: {len(rutinas)} rutina(s) y {len(habitos)} hábito(s).")
    print("\nBienvenid@ a AdaptIA, la aplicación que te ayuda a tener una rutina "
          "que se adapte a ti, no tú a ella\n")
    return usuario_id


# =============================================================================
# 5. INICIO DE SESIÓN
# =============================================================================

def inicio_sesion(conexion):
    """
    Flujo de inicio de sesión (flujo.md, sección 3), con un máximo de MAX_INTENTOS_LOGIN intentos.
    Si las credenciales son correctas, actualiza ultimo_login y devuelve el id del usuario.
    Si no, devuelve None. El mensaje de error es genérico para no revelar si falló el usuario
    o la contraseña.
    """
    for intento in range(1, MAX_INTENTOS_LOGIN + 1):
        usuario = pedir_texto("Ingresa tu nombre de usuario\n> ", maximo=30)
        contrasena = getpass("Ingresa tu contraseña\n> ")

        fila = conexion.execute(
            "SELECT id, contrasena_hash FROM usuarios WHERE LOWER(usuario) = LOWER(?)", (usuario,)
        ).fetchone()

        if fila and verificar_contrasena(contrasena, fila[1]):
            with conexion:
                conexion.execute(
                    "UPDATE usuarios SET ultimo_login = ? WHERE id = ?",
                    (datetime.now().isoformat(timespec="seconds"), fila[0]),
                )
            print(f"\n\n***** Bienvenid@ {usuario}, puedes continuar en tu perfil *****\n")
            return fila[0]

        print(f"Usuario o contraseña incorrectos (intento {intento}/{MAX_INTENTOS_LOGIN}).")

    print("Demasiados intentos fallidos.")
    return None


# =============================================================================
# 6. MENÚ PRINCIPAL (después de iniciar sesión)
# =============================================================================

# --- 6.1 Lectura de datos ---------------------------------------------------

def formatear_dias(dias):
    """Convierte [0, 2, 4] en 'Lun,Mie,Vie' (o 'Todos' si son los 7 días)."""
    if len(dias) == 7:
        return "Todos"
    return ",".join(DIAS_CORTOS[d] for d in dias)


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


# --- 6.2 Tabla y gráfica ----------------------------------------------------

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


def mostrar_tabla_habitos(habitos):
    """Muestra la tabla de hábitos activos: nombre, importancia, días, horario y veces cumplido."""
    if not habitos:
        print("Aún no tienes hábitos activos. Agrega uno desde el menú.\n")
        return
    filas = [
        (h["nombre"], h["peso"], formatear_dias(h["dias"]),
         f"{a_texto(h['hora_esperada'])}-{a_texto(h['hora_esperada'] + h['duracion'])}", h["veces"])
        for h in habitos
    ]
    imprimir_tabla(("Hábito", "Imp.", "Días", "Horario", "Veces"), filas)
    print()


def mostrar_grafica_habitos(habitos):
    """Gráfica de barras (texto) de las veces que se cumplió cada hábito, resaltando el más constante."""
    con_datos = sorted((h for h in habitos if h["veces"] > 0), key=lambda h: h["veces"], reverse=True)
    if not con_datos:
        print("Aún no hay hábitos cumplidos registrados; la gráfica aparecerá cuando los haya.\n")
        return

    maximo = con_datos[0]["veces"]
    ancho = max(len(h["nombre"]) for h in con_datos)
    print("Veces que has cumplido cada hábito:")
    for h in con_datos:
        barra = "█" * max(1, round(h["veces"] / maximo * ANCHO_GRAFICA))
        print(f"{h['nombre']:<{ancho}} | {barra} {h['veces']}")
    print(f"\nHábito que más haces: {con_datos[0]['nombre']} ({maximo} veces)\n")


# --- 6.3 Agregar y quitar hábitos -------------------------------------------

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
    inicio, fin = pedir_intervalo(nombre, dias, validador)

    habito = {
        "nombre": nombre, "peso": peso, "dias": dias, "hora_esperada": inicio,
        "duracion": fin - inicio, "hora_central": (inicio + fin) // 2,
    }
    with conexion:
        insertar_habito(conexion, usuario_id, habito, datetime.now().date().isoformat())
    print(f"Hábito '{nombre}' agregado.")


def quitar_habito(conexion, habitos):
    """
    Desactiva un hábito (activo = 0) en lugar de borrarlo: así su historial de registros
    y predicciones se conserva para el algoritmo.
    """
    if not habitos:
        print("No tienes hábitos activos para quitar.")
        return
    print("\n¿Qué hábito quieres quitar?")
    for i, h in enumerate(habitos, 1):
        print(f"{i}.-{h['nombre']}")
    print("0.-Cancelar")
    opcion = pedir_entero("> ", 0, len(habitos))
    if opcion == 0:
        return

    habito = habitos[opcion - 1]
    if pedir_si_no(f"¿Seguro que quieres quitar '{habito['nombre']}'? (tu historial se conserva)"):
        with conexion:
            conexion.execute("UPDATE habitos SET activo = 0 WHERE id = ?", (habito["id"],))
        print(f"Hábito '{habito['nombre']}' quitado.")


# --- 6.4 Agregar y quitar rutinas -------------------------------------------

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


def quitar_rutina(conexion, usuario_id):
    """Desactiva una rutina fija (activo = 0); no se borra, por si se quiere recuperar después."""
    rutinas = cargar_rutinas(conexion, usuario_id)
    if not rutinas:
        print("No tienes rutinas activas para quitar.")
        return
    print("\n¿Qué rutina quieres quitar?")
    for i, r in enumerate(rutinas, 1):
        print(f"{i}.-{r['nombre']}")
    print("0.-Cancelar")
    opcion = pedir_entero("> ", 0, len(rutinas))
    if opcion == 0:
        return

    rutina = rutinas[opcion - 1]
    if pedir_si_no(f"¿Seguro que quieres quitar '{rutina['nombre']}'?"):
        with conexion:
            conexion.execute("UPDATE rutinas_fijas SET activo = 0 WHERE id = ?", (rutina["id"],))
        print(f"Rutina '{rutina['nombre']}' quitada.")


# --- 6.5 Menú ---------------------------------------------------------------

def menu_principal(conexion, usuario_id):
    """
    Pantalla de inicio tras autenticarse: muestra la tabla de hábitos y la gráfica, y ofrece
    agregar o quitar hábitos y rutinas. Se vuelve a dibujar después de cada cambio.
    """
    while True:
        habitos = obtener_habitos(conexion, usuario_id)
        print("\n" + "=" * 50)
        print("TUS HÁBITOS")
        print("=" * 50)
        mostrar_tabla_habitos(habitos)
        mostrar_grafica_habitos(habitos)

        opcion = pedir_entero(
            "1.-AGREGAR HÁBITO\n2.-QUITAR HÁBITO\n3.-AGREGAR RUTINA\n4.-QUITAR RUTINA\n5.-SALIR\n> ", 1, 5
        )
        if opcion == 5:
            break
        try:
            if opcion == 1:
                agregar_habito(conexion, usuario_id)
            elif opcion == 2:
                quitar_habito(conexion, habitos)
            elif opcion == 3:
                agregar_rutina(conexion, usuario_id)
            else:
                quitar_rutina(conexion, usuario_id)
        except sqlite3.Error as error:
            print(f"Error de base de datos, no se guardó nada: {error}")


# =============================================================================
# 7. PROGRAMA PRINCIPAL
# =============================================================================

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
                usuario_id = inicio_sesion(conexion)
            elif opcion == 2:
                usuario_id = registrarse(conexion)
            else:
                break

            if usuario_id is not None:
                # Pendiente de la siguiente etapa: muro de recordatorios antes del menú.
                menu_principal(conexion, usuario_id)
                break
    except (KeyboardInterrupt, EOFError):
        print("\nSesión cancelada. No se guardó nada pendiente.")
    finally:
        conexion.close()


if __name__ == "__main__":
    main()