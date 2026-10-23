# Adaptia — Flujo de la aplicación

**Versión:** V0
**Alcance:** interacción del usuario con la aplicación: sesión, configuración inicial, muro de recordatorios y menú principal.
**Naturaleza:** especificación funcional, independiente de la implementación.

---

## 1. Flujo general

```
Inicio
  │
  ▼
¿Tiene cuenta?
  ├─ No → Registro → Configuración inicial ─┐
  └─ Sí → Inicio de sesión ─────────────────┤
                                            ▼
                              Revisión de días pendientes
                                            ▼
                                 MURO DE RECORDATORIOS
                                            ▼
                                     Menú principal
              ┌──────────────┬──────────────┼──────────────┬──────────────┐
              ▼              ▼              ▼              ▼              ▼
      Registrar hábito    Ver hoy    Ver horarios    Activar /        Salir
                                       aprendidos    desactivar
```

---

## 2. Registro de usuario

1. Se solicita un nombre de usuario, que debe ser único. Si ya existe, se solicita otro.
2. Se solicita la contraseña y su confirmación.
3. La contraseña no se almacena en texto plano: se guarda únicamente su hash.
4. Se crea el usuario y se continúa con la configuración inicial.

## 3. Inicio de sesión

1. Se solicitan usuario y contraseña.
2. Se busca el usuario y se compara la contraseña ingresada contra el hash almacenado.
3. Si coinciden, se concede el acceso y se registra la fecha y hora del inicio de sesión.
4. Si no coinciden, se muestra un mensaje genérico ("usuario o contraseña incorrectos") y se permite reintentar.

---

## 4. Configuración inicial (usuario nuevo)

Los datos se guardan en la base de datos al finalizar cada paso:

1. **Hora de despertar** y **hora de dormir**.
2. **Rutinas fijas:** nombre, días, hora de inicio y hora de fin. Se repite hasta que no haya más por registrar.
3. **Hábitos deseados**, con:
   - Nombre.
   - Días de la semana.
   - Horario deseado (inicio y fin).
   - Importancia del 1 al 10.
4. **Validación:** si el horario de un hábito coincide con una rutina fija, se notifica y se solicita uno distinto.
5. Al finalizar, el algoritmo genera la primera predicción de cada hábito.

---

## 5. Muro de recordatorios

Es la primera pantalla que se muestra tras cada inicio de sesión.

### 5.1 Generación

No existe un proceso en segundo plano; el muro se genera **al iniciar sesión**:

1. Se revisan los días transcurridos desde el último inicio de sesión hasta la fecha actual.
2. Para cada día y cada hábito activo configurado para ese día, se busca un registro de cumplimiento.
3. Si no existe, se crea un recordatorio pendiente.
4. Si el usuario registró explícitamente que no realizó el hábito, este también ingresa al muro.

### 5.2 Contenido

- **Pendientes:** hábitos no realizados, ordenados por importancia (mayor primero) y luego por antigüedad.
- **Hoy:** hábitos programados para la fecha actual, con su ventana recomendada, ordenados por hora.

Si no hay pendientes, se muestra únicamente la sección "Hoy".

### 5.3 Acciones sobre un pendiente

|Acción |Resultado |
|---|---|
| **Ya lo hice** | Se solicitan hora real y emoción, se registra y se envía al algoritmo. El recordatorio sale del muro. |
| **Posponer**   | Permanece en el muro y reaparece en el siguiente inicio de sesión. |
| **Descartar**  | Sale del muro. Se conserva como "no hecho". |

### 5.4 Reglas

- Se genera un solo recordatorio por hábito y por día.
- Un recordatorio sin resolver tras **7 días** se archiva automáticamente como "no hecho".
- Los hábitos y rutinas inactivos no generan recordatorios.

---

## 6. Menú principal

### 6.1 Registrar hábito

1. El usuario selecciona el hábito.
2. Indica si lo realizó (sí / no).
3. Si lo realizó, elige entre "ahora" o una hora específica.
4. Selecciona la emoción: felicidad, satisfacción, nada, frustración o enojo.
5. La aplicación envía los datos al algoritmo y muestra el resultado: horario recomendado o mensaje de "probar otro horario".
6. Si no lo realizó, se crea el recordatorio en el muro.

### 6.2 Ver hoy

Lista de los hábitos programados para la fecha actual, con su ventana recomendada y su estado (pendiente / realizado).

### 6.3 Ver horarios aprendidos

Para cada hábito: horarios `buenos`, `malos` y `en prueba`, con el margen vigente de cada uno.

### 6.4 Activar / desactivar

Muestra la lista de rutinas fijas y hábitos, activos e inactivos. Al seleccionar uno se alterna su estado.

Al **desactivar**:

- Deja de aparecer en recomendaciones y en el muro.
- Sus recordatorios pendientes se archivan.
- El historial se conserva completo.

Al **reactivar**, el hábito conserva sus horarios aprendidos.

### 6.5 Salir

Cierra la sesión.

---

## 7. Parámetros de diseño

|Parámetro |Valor |
|---|
| Archivo automático de recordatorios | 7 días |
| Escala de importancia | 1 a 10 |

**Pendiente por definir:** límite de veces que un mismo recordatorio puede posponerse.