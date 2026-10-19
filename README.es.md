# Progress Ring

[English](README.md) · **Español**

![Un plan avanzando por sus pasos en la banda sobre el prompt](docs/media/demo.gif)

Ve por dónde va Claude Code en un plan largo sin leerte la conversación. Cuando Claude arranca un plan de tres o más pasos, abre una ficha de progreso y cierra cada paso al terminarlo. Lo ves en:

- **Banda** sobre el prompt (terminal y escritorio): anillo con el porcentaje, el paso actual, el plan y si Claude está trabajando, esperando tu respuesta o parado.
- **Panel `/progress`**: todos los planes de este chat y de otros, con sus pasos, los subagentes pendientes de revisión y un botón para ir al chat de cada plan.
- **Hitos** en la conversación: el comando que cierra un paso se dibuja como «Ahora: X · ✓ lo cerrado · N/M».

No hace falta decirle a Claude que lo use: el plugin le enseña cuándo abrir una ficha, cerrar pasos, aceptar el trabajo de los subagentes y terminar.

**En la app de escritorio de Claude**, Claude ya lleva una lista de tareas bajo su último mensaje. Progress Ring sigue esa lista en lugar de pedirle a Claude que lleve otra: una lista de tres pasos o más abre una ficha, cada paso que Claude completa se cierra en ella y la ficha termina con el turno. Una ficha es un objetivo: cuando Claude pasa a otra cosa, esa ficha se cierra y se abre otra, con el título del nuevo objetivo. Claude solo usa el comando `progress` para lo que la lista no lleva: iteraciones, aceptar el trabajo de los subagentes, cancelar o pausar, y un título más claro. En la terminal no cambia nada.

![La banda desplegada: pasos hechos, el actual con su agente y lo que viene](docs/media/band-desktop-open.png)

![El panel /progress: planes de este chat, de otros chats y terminados hoy](docs/media/panel-desktop.png)

*Las capturas usan datos de demo (`scripts/demo.py`) y la interfaz en inglés.*

## Instalar

En el prompt de una sesión de Claude Code:

```
/plugin install progress-ring --marketplace alvaro-hi/progress-ring
```

Responde `y` para añadir el marketplace y elige el ámbito de usuario. O desde un terminal:

```bash
claude plugin marketplace add alvaro-hi/progress-ring
```

```bash
claude plugin install progress-ring@progress-ring --scope user
```

Después, abre una sesión nueva. Para quitarlo: `claude plugin uninstall progress-ring@progress-ring`.

**Requisitos:** una versión de Claude Code con mods (módulos de hooks), probado en la 2.1.287; macOS o Linux; `python3` 3.9 o posterior en el `PATH`.

**Idioma:** la interfaz sigue el idioma del sistema (español o inglés). Para fijar uno, pon la opción `language` del plugin en `es` o `en` desde `/plugin` → progress → configurar.

## Cómo funciona

| Pieza | Dónde | Qué hace |
|---|---|---|
| CLI | `bin/progress`, `bin/progresslib.py` | Crea y actualiza las fichas en `~/.claude/progress/<id>.json`, con un lock por ficha |
| Hook | `bin/progress_hook.py` | Inyecta el estado de la ficha activa en cada mensaje; registra los subagentes como filas; apunta inicio y fin de cada turno, el chat dueño y los tokens por paso; cierra el plan con todos los pasos hechos al acabar el turno |
| Mod | `hooks/register.tsx` y el resto de `hooks/` | Dibuja la banda, el panel y los hitos; pasa los eventos de hooks de Claude Code al hook de Python; añade la regla de uso al system prompt |

El mod solo lee fichas: toda escritura pasa por el CLI. El hook nunca bloquea una sesión: cualquier error va a `~/.claude/progress/_hook-errors.log` y la sesión sigue. Nada sale de tu máquina.

### Qué ejecuta, qué lee y qué envía

- **Ejecuta** dos scripts locales de Python desde la carpeta del propio plugin, siempre como `python3 bin/progress_hook.py` y `python3 bin/progress`: el primero en los eventos de hooks de Claude Code (mensaje enviado, fin de turno, uso de herramientas, inicio y fin de subagentes, una llamada a Agent a punto de correr), y el segundo cuando pulsas Ocultar, Vaciar, Modo discreto o Ir al chat. Para abrir un chat, `progress chat` ejecuta `open` (macOS) o `xdg-open` (Linux) con un enlace `claude://`.
- **Lee** las fichas de `~/.claude/progress`, el id y la carpeta de la sesión, los datos de los eventos de hooks (incluida la ruta de la transcripción: el hook la lee para sumar los tokens gastados por paso) No lee credenciales ni datos de la app de Claude.
- **No envía nada** por la red. Todo se queda en archivos JSON locales en `~/.claude/progress`.
- **Cambios en Claude Code:** añade una sección al system prompt (cuándo y cómo seguir un plan; en la app de escritorio, cómo sigue la ficha a la lista de tareas), añade a tus mensajes una línea de contexto con el estado de la ficha activa y, cuando la lista de tareas abre una ficha, una línea que le dice a Claude su id, registra `/progress`, dibuja la banda, el panel y las filas de hitos, y oculta la fila de resultado de los comandos `progress`, que ya enseña el hito. Nunca concede, deniega ni pide un permiso.

### Reglas que siguen las fichas

- El porcentaje solo cuenta pasos cerrados. Un subagente que entrega aparece como «entregado, por revisar» y solo suma cuando Claude lo acepta.
- La actividad mueve el estado: un paso nuevo reabre una ficha pausada, cerrada o cancelada; un plan con todos los pasos cerrados se cierra solo al acabar el turno.
- Una ficha está **parada** tras 30 minutos sin avance (15 si faltan uno o dos pasos), nunca con un turno o un subagente en marcha.
- La sesión que toca una ficha es su dueña. Los otros chats la ven en «Otros planes en curso» y no la tocan salvo que se lo pidas.
- Las terminadas se ven hasta medianoche. «Vaciar» mueve las terminadas y canceladas a `~/.claude/progress/_done/`; no se borra nada.

## Usar el CLI tú mismo

Claude llama al CLI con su ruta completa. Para usarlo desde tu terminal:

```bash
alias progress="$(ls -dt ~/.claude/plugins/cache/progress-ring/progress-ring/*/ | head -1)bin/progress"
```

```bash
progress start lanzamiento "Lanzamiento 2.0" --total 5            # ficha nueva
progress plan lanzamiento build="Build" qa="QA" ship="Publicar"    # pasos previstos (el total sigue al plan)
progress step lanzamiento "Build en verde" --key build             # cerrar un paso
progress plan lanzamiento --add docs="Docs" --after qa             # replanificar
progress child accept lanzamiento <cid>                            # aceptar la entrega de un subagente
progress done lanzamiento                                          # terminar (o: cancel, pause)
progress hide lanzamiento                                          # fuera de la lista hasta que se mueva
progress clear                                                     # terminadas y canceladas → _done/
progress chat lanzamiento                                          # abre su último chat, o imprime `claude --resume <id>`
progress discreet toggle                                           # ocultar títulos al compartir pantalla
```

`CLAUDE_PROGRESS_DIR` lleva las fichas a otra carpeta en lugar de `~/.claude/progress`.

## Desarrollo

```bash
python3 -m unittest discover tests
```

```bash
claude plugin test .
```

```bash
claude plugin validate .claude-plugin/plugin.json
```

`scripts/demo.py` lanza un plan de demo que avanza solo (para capturas); `--clean` lo quita.

Para probar cambios locales, añade la carpeta del repo como marketplace (`claude plugin marketplace add .`) e instala desde ella: Claude Code lee entonces el plugin directamente de la carpeta y `/reload-plugins` recoge los cambios.

## Créditos

Diseñado por [Álvaro del Pozo](https://www.linkedin.com/in/alvaro-hiexperience/). Licencia MIT. Sin relación con Anthropic; «Claude» y «Claude Code» son marcas de Anthropic.
