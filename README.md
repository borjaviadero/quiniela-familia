# Quiniela familiar

Bot de Telegram + web para la quiniela semanal de la familia (Luis Angel, Rosana,
Joseba, Lara y Borja). Todo en servicios gratuitos.

📄 **Guía completa para cualquier persona (técnica y no técnica):**
[`docs/Quiniela-familiar-guia.pdf`](docs/Quiniela-familiar-guia.pdf)

| Pieza | Servicio | Qué hace |
|---|---|---|
| Bot | GitHub Actions (`bot/`) | Lee la Quiniela oficial, calcula aciertos y escribe en el grupo |
| Web de apuestas | GitHub Pages (`web/`) | Cada uno elige su nombre, pone su PIN y marca su columna |
| Base de datos | Supabase (Postgres) | Jornadas, partidos, columnas, partes enviados y pagos |
| Mensajes | Bot de Telegram | Fotos del boleto, avisos, quién paga y premios |
| Datos | Loterías y Apuestas del Estado | Partidos, horarios, resultados y escrutinio oficiales |
| Equipos de Primera | ESPN | Lista de la temporada, para el filtro de Liga |
| Copia de seguridad | GitHub Actions (`backups/`) | Copia semanal cifrada de la base de datos |

## Cómo funciona una jornada

1. **Loterías publica una quiniela de Liga** → el bot crea la jornada, elige quién
   rellena el pleno al 15 y manda al grupo la foto de los partidos con el botón
   *Hacer mi quiniela*.
2. **Cada uno marca su columna** en la web con su PIN. Puede cambiarla hasta
   **24 h antes del primer partido**.
3. **48 h antes del primer partido** → aviso en el grupo, mencionando a quien falte.
4. **Cuando están las 5** (o al cerrar el plazo) → foto con las 5 columnas para sellar
   en la administración y quién debe abonarla.
5. **Al acabar el último partido de cada día** → foto con aciertos (verde), fallos
   (rojo) y quién va en cabeza.
6. **Al acabar la jornada** → foto final, aciertos de cada uno y quién paga la siguiente.
7. **Cuando Loterías publica el escrutinio** → si alguna columna tiene premio, cuánto
   le corresponde.

El bot se ejecuta solo: de martes a jueves cada hora y de viernes a lunes cada
20 minutos. Nunca repite un mensaje: cada envío queda marcado en la base de datos.

## Reglas

**Quién paga**
- Paga el boleto siguiente (3,75 €) quien menos aciertos saca. Si hay empate, se
  reparte; los céntimos que no cuadran se ajustan en el primero (1,87 € + 1,88 €).
- Quien no registra columna antes del cierre cuenta con 0 aciertos.
- Se empezó a contar desde la jornada 16 de la temporada 2026-27.

**Pleno al 15**
- Lo rellena quien más acertó la jornada anterior. Si hay empate, o es la primera
  jornada, se sortea. Si la anterior todavía se está jugando (jornadas solapadas), se
  usa la última terminada.
- No suma aciertos. Es uno por boleto: vale para cualquier columna con 14 aciertos.

**Qué quinielas se juegan**
- Solo las **de Liga**: al menos 5 partidos entre equipos de Primera. Las de Champions,
  Copa, etc. se ignoran. La lista de Primera se descarga sola de ESPN, así que ascensos
  y descensos no hay que tocarlos; si ESPN fallara, se usa la lista de respaldo de
  `bot/logica.py`.
- Los equipos femeninos salen con **(F)** y nunca cuentan como de Primera.

**Aplazamientos y cambios de horario**
- Hasta que empieza la jornada, el bot vuelve a leer los partidos en cada pasada: si
  Loterías cambia nombres u horarios, la web y las fotos se actualizan.
- Si cambia la hora del **primer partido** mientras se puede apostar, el plazo se mueve
  con ella y el bot avisa en el grupo (*«Cambio de horario en la jornada X…»*).
- Si un partido no tiene resultado 8 horas después del último partido de su día
  (aplazado o suspendido), el parte de ese día se manda igual, indicando qué partido
  falta. El resultado final espera al signo oficial de Loterías.

**Premios**
- Con el escrutinio oficial, el bot manda una línea por columna premiada:
  *«Borja ha tenido 11 aciertos por lo que corresponde 2,22 € según el sorteo.»*
  Si nadie cobra, no manda nada.

## Puesta en marcha (unos 30 minutos)

### 1. Supabase

1. Crea un proyecto gratuito en <https://supabase.com>.
2. *SQL Editor* → pega `supabase/schema.sql` → **Run**.
3. Cambia los PIN (los del script son 1111, 2222…):
   ```sql
   select fijar_pin(1::smallint, '4821');  -- Luis Angel
   select fijar_pin(2::smallint, '....');  -- Rosana
   -- 3 Joseba · 4 Lara · 5 Borja
   ```
4. *Project Settings → API Keys → Legacy anon, service_role API keys*: apunta la
   **URL**, la **anon key** y la **service_role key**. La service_role key es secreta:
   solo va en GitHub Secrets, nunca en la web.

### 2. Bot de Telegram

1. Habla con **@BotFather** → `/newbot` → apunta el **token**.
2. En @BotFather, `/setprivacy` → elige el bot → **Disable** (antes de meterlo en el
   grupo), para que vea quién escribe y pueda mencionar a cada uno.
3. Crea el grupo de la quiniela, añade al bot y a la familia, y que cada uno escriba
   cualquier cosa en el grupo.

### 3. GitHub

1. Sube esta carpeta a un repositorio nuevo. Si es **público**, los minutos de Actions
   son ilimitados (aquí no hay ningún secreto en el código).
2. *Settings → Secrets and variables → Actions*:
   - Secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `TELEGRAM_TOKEN`,
     `TELEGRAM_CHAT_ID` y `BACKUP_KEY` (una contraseña larga inventada para cifrar las
     copias; **guárdala también en un sitio seguro**, sin ella no se pueden recuperar)
   - Variable: `WEB_URL` (la dirección de la web del paso 4)
3. Para sacar el `TELEGRAM_CHAT_ID` y los usuarios, abre en el navegador
   `https://api.telegram.org/bot<TOKEN>/getUpdates` después de escribir en el grupo
   (o *Actions → Quiniela → Run workflow → telegram-ids*). El del grupo es negativo.
   Guarda el de cada persona:
   ```sql
   update jugadores set telegram_user_id = 123456789 where nombre = 'Rosana';
   ```

### 4. Web

1. Rellena `web/config.js` con la URL y la **anon key** de Supabase.
2. *Settings → Pages → Source: GitHub Actions*. Al subir el cambio se publica en
   `https://<usuario>.github.io/<repo>/`. Esa es la `WEB_URL`.
3. Para ver la web sin base de datos: añade `?demo` a la dirección.
   Para un enlace que ya lleve elegido el nombre: `?j=5` (Borja).

### 5. Comprobar Loterías y ESPN

*Actions → Quiniela → Run workflow → diagnostico-lae*. En el registro deben salir los
equipos de Primera según ESPN, las próximas jornadas con sus 15 partidos y horas (y si
son de Liga o no) y el escrutinio de las últimas jornadas. Si algo sale vacío, Loterías
ha cambiado su web y hay que ajustar `bot/lae.py`.

## Copias de seguridad

El plan gratuito de Supabase no hace copias. Cada **martes** el workflow
*Copia de seguridad* descarga todas las tablas, las **cifra** con el secreto
`BACKUP_KEY` (el repositorio es público) y las guarda en `backups/quiniela.json.enc`.
El historial de git conserva todas las versiones anteriores. También se puede lanzar a
mano: *Actions → Copia de seguridad → Run workflow*.

Para recuperar una copia (con Python y openssl instalados):
```bash
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in backups/quiniela.json.enc -out copia.json
python -m bot.backup restaurar copia.json      # con SUPABASE_URL y SUPABASE_SERVICE_KEY
```
Y después, en Supabase > SQL Editor:
```sql
select setval('jornadas_id_seq', (select coalesce(max(id), 1) from jornadas));
```

> ⚠️ Como el bot sube una copia cada semana, **antes de subir cambios propios haz
> `git pull`**. Si no, `git push` te dirá que tu copia local está desactualizada.

## Migraciones de la base de datos

Si la base de datos se creó con una versión anterior de `schema.sql`, ejecutar en
Supabase > SQL Editor, en orden, los `supabase/migracion_*.sql` que falten:

| Migración | Qué hace |
|---|---|
| `migracion_01_jornadas_solapadas.sql` | La web enseña la jornada abierta para apostar aunque otra se esté jugando |
| `migracion_02_premios.sql` | Marca si ya se han publicado los premios de cada jornada |

## Mantenimiento

- **Si llega un correo de GitHub con un fallo**: *Actions* → la ejecución en rojo →
  *bot* → *Ejecutar*, y leer el error.
- **Si el bot deja de escribir en el grupo**: si el grupo pasó a «supergrupo», su ID
  cambia (empieza por `-100`). Actualiza el secreto `TELEGRAM_CHAT_ID`.
- **Volver a enviar el anuncio de una jornada**:
  `update jornadas set anunciada = false where numero = 16;` y *Run workflow → tick*.
- **Acciones de GitHub**: los workflows usan `actions/checkout@v5`,
  `actions/setup-python@v6`, `actions/upload-pages-artifact@v4` y fuerzan Node.js 24
  (`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24`). Si GitHub avisa de nuevas versiones, basta
  con subir el número de versión.

## Estructura

```
.github/workflows/quiniela.yml   cuándo se ejecuta el bot
.github/workflows/web.yml        publica la web en GitHub Pages
.github/workflows/backup.yml     copia de seguridad semanal cifrada
bot/main.py                      ciclo de cada pasada y mensajes
bot/lae.py                       lectura de Loterías (partidos, resultados, escrutinio)
bot/equipos.py                   equipos de Primera (ESPN) y comparación de nombres
bot/logica.py                    reglas: aciertos, pagos, pleno, partes, filtro de Liga, premios
bot/imagen.py                    dibuja las fotos del boleto
bot/db.py · bot/telegram.py      conexión con Supabase y Telegram
bot/backup.py                    exportar y restaurar la base de datos
web/index.html · web/config.js   página de apuestas
supabase/schema.sql              crea la base de datos desde cero
supabase/migracion_*.sql         cambios para bases de datos ya creadas
tests/test_simulacion.py         simula una jornada entera sin conexión
docs/Quiniela-familiar-guia.pdf  guía completa
```

## Desarrollo

```bash
pip install -r requirements.txt
python -m tests.test_simulacion salida/   # simula una jornada entera sin red
python -m bot.main tick --dry-run salida/ # usa Supabase y Loterías reales, sin enviar a Telegram
```
