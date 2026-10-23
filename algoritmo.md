# Adaptia — Especificación del algoritmo

**Versión:** V0
**Alcance:** lógica de predicción y aprendizaje.
**Naturaleza:** especificación funcional, independiente de la implementación.

---

## 1. Objetivo

Para cada hábito de un usuario, el algoritmo:

1. **Predice** la hora a la que se realizará (hora central y margen).
2. **Evalúa** cada registro nuevo: cumplimiento, hora real y emoción.
3. **Ajusta** la predicción o **propone un horario alternativo**.

**Entradas** (en cada registro):

- Hábito, cumplimiento, hora real y emoción (1-5).
- Historial del hábito, rutinas fijas, hora de despertar y hora de dormir.

**Salidas:**

- Horario recomendado (hora central ± margen).
- Cambios de estado del horario, o mensaje de "probar otro horario".
- En caso de incumplimiento, una señal a la aplicación para generar un recordatorio.

---

## 2. Definiciones

| Concepto                  | Definición                                                                                         |
+---------------------------+----------------------------------------------------------------------------------------------------+
| **Rutina fija**           | Bloque no negociable (escuela, trabajo). El algoritmo nunca lo modifica.                           |
| **Hábito**                | Actividad negociable con días, duración e importancia (1-10).                                      |
| **Horario**               | Hora central con un margen (± minutos). Un hábito puede tener varios.                              |
| **Estado de un horario**  | `declarado` (indicado por el usuario), `bueno`, `malo` o `en prueba` (propuesto por el algoritmo). |
| **Margen**                | Tamaño de la ventana. Inicia en 30 min y desciende hasta un mínimo de 10 min.                      |
| **Acierto**               | El hábito se realizó dentro de la ventana recomendada.                                             |
| **Cumplimiento**          | El hábito se realizó a cualquier hora. Es independiente del acierto.                               |
| **Emoción**               | felicidad = 5, satisfacción = 4, nada = 3, frustración = 2, enojo = 1.                             |

Clasificación de emociones:

- **Positiva:** 4 o 5.
- **Neutral:** 3 (no modifica ningún valor).
- **Negativa:** 1 o 2.

---

## 3. Primera predicción

El primer horario de cada hábito es el declarado por el usuario: estado `declarado`, hora central en el punto medio del intervalo y margen de 30 min.

---

## 4. Reglas de evaluación de un registro

| # |¿Cumplió?|¿Dentro de la ventana?| Emoción |                                                       Acción del algoritmo                                                                   |
|---+----+----+----------+--------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| A | No | —  | —        | Registra "no hecho" y señala a la aplicación que genere un recordatorio. No modifica márgenes ni horarios.                                                         |
| B | Sí | Sí | Positiva | Acierto (+1). El margen disminuye 5 min (mínimo 10). Se mantiene la recomendación.                                                                                 |
| C | Sí | Sí | Neutral  | Acierto (+1). El margen no cambia. Se mantiene la recomendación.                                                                                                   |
| D | Sí | Sí | Negativa | Acierto (+1). El margen no disminuye y el horario queda "en duda". Con 2 registros negativos consecutivos, el horario pasa a `malo` y se propone otro (sección 5). |
| E | Sí | No | Positiva | Fallo de predicción (-1). La hora real se guarda como horario `bueno` con margen de 30 min y se recomienda.                                                        |
| F | Sí | No | Neutral  | Fallo de predicción (-1). La hora se guarda como dato, sin clasificarla. Se mantiene la recomendación actual.                                                      |
| G | Sí | No | Negativa | Fallo de predicción (-1). La hora real se marca `mala`. Se muestra el mensaje "probar otro horario" y se propone uno (sección 5).                                  |

Consideraciones:

- Cumplimiento y acierto se almacenan por separado.
- Si el hábito tiene varios horarios `buenos`, "dentro de la ventana" equivale a estar dentro de cualquiera de ellos.
- Cada horario mantiene su propio margen.
- Un horario `bueno` con margen de 10 min se sigue recomendando mientras se mantengan los aciertos.

---

## 5. Selección de un horario alternativo

Se evalúan los pasos en orden y se aplica el primero que produzca resultado:

1. **Horarios buenos almacenados.** Se recomienda el de mayor emoción promedio; en caso de empate, el más reciente.
2. **Horas probadas con emoción ≥ 3.** Si no existen horarios `buenos`, se toman las horas reales registradas para el hábito con emoción neutral o positiva y se recomienda la más frecuente.
3. **Exploración por huecos libres.** Si no hay datos útiles, se busca un hueco que cumpla todas las condiciones:
   - Se encuentra dentro del rango despertar-dormir.
   - No coincide con rutinas fijas ni con hábitos de mayor importancia.
   - Admite la duración del hábito más la ventana.
   - No está marcado como `malo`.
   - Se evalúan primero los huecos más cercanos al horario original, alternando hacia antes y hacia después (±1 h, ±2 h, …).
4. **Sin huecos disponibles.** Se notifica al usuario y se sugiere revisar importancias o rutinas.

El horario propuesto ingresa como `en prueba` con margen de 30 min. Si el siguiente registro tiene emoción positiva pasa a `bueno`; si es negativa, a `malo`.

---

## 6. Competencia entre hábitos

Cuando dos hábitos requieren el mismo hueco, se asigna al de **mayor importancia**; el otro busca un hueco alternativo. En caso de igualdad, prevalece el hábito con más aciertos.

---

## 7. Vigencia de los datos

Para promedios y frecuencias se consideran únicamente los **últimos 14 registros** de cada hábito. Los registros anteriores se conservan, pero no participan en el cálculo.

---

## 8. Restricciones

1. No se genera ninguna recomendación entre la hora de dormir (o medianoche) y la hora de despertar.
2. No se genera ninguna recomendación que coincida con una rutina fija.
3. Las ventanas se recortan cuando invaden un bloque restringido.
4. Un hábito se recomienda únicamente en los días configurados para él.
5. Los hábitos inactivos no generan recomendaciones.
6. Los registros nunca se eliminan; solo se excluyen del cálculo según la sección 7.

---

## 9. Parámetros de diseño

| Parámetro                                                           | Valor                                                                                                                 |
|---------------------------------------------------------------------+-----------------------------------------------------------------------------------------------------------------------|
| Emoción positiva / neutral / negativa                               | 4-5 / 3 / 1-2                                                                                                         |
| Margen inicial / mínimo / reducción por acierto                     | 30 min / 10 min / 5 min                                                                                               |
| Registros considerados en el cálculo                                | Últimos 14 por hábito                                                                                                 |
| Registros negativos consecutivos para marcar un horario como `malo` | 2                                                                                                                     |
| Propuesta de horarios nuevos (sección 5, pasos 2 y 3)               | Se habilita tras 7 registros del hábito; antes, solo se usa el horario declarado y los horarios `buenos` ya guardados |
| Granularidad del aprendizaje                                        | Por hábito (el mismo horario aplica a todos los días configurados)                                                    |
| Escala de importancia                                               | 1 a 10                                                                                                                |

---

## 10. Plan de versiones

| Versión| Alcance                                                                  |
|--------+--------------------------------------------------------------------------|
| **V0** | Un solo hábito: lectura de registros y cálculo de hora central y margen. |
| **V1** | Emociones y los 7 casos de la matriz de evaluación.                      |
| **V2** | Múltiples horarios buenos y selección por huecos libres.                 |
| **V3** | Importancia y competencia entre hábitos considerando rutinas fijas.      |