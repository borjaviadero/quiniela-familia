# Quiniela familiar

Bot de Telegram + web para la quiniela semanal de la familia (Luis Angel, Rosana,
Joseba, Lara y Borja). Todo en servicios gratuitos:

| Pieza | Servicio | Qué hace |
|---|---|---|
| Base de datos | Supabase (Postgres) | Jornadas, partidos, columnas, partes enviados y pagos |
| Web de apuestas | GitHub Pages (`web/`) | Cada uno elige su nombre, pone su PIN y marca su columna |
| Bot | GitHub Actions (`bot/`) | Lee la Quiniela oficial, calcula aciertos y escribe en el grupo |
| Mensajes | Bot de Telegram | Fotos del boleto, avisos y quién paga |

## Cómo funciona una jornada

1. **Se publica la quiniela oficial** → el bot crea la jornada, elige quién rellena el
   pleno al 15 y manda al grupo la foto de los partidos con el botón *Hacer mi quiniela*.
2. **Cada uno marca su columna** en la web con su PIN. Puede cambiarla hasta
   **24 h antes del primer partido**.
3. **48 h antes del primer partido** → aviso en el grupo, mencionando a quien falte.
4. **Cuando están las 5** (o al cerrar el plazo) → foto con las 5 columnas para sellar
   en la administración y quién debe abonarla.
5. **Al acabar el último partido de cada día** con resultado oficial → foto con
   aciertos (verde) y fallos (rojo) y quién va en cabeza.
6. **Al acabar la jornada** → foto final, aciertos de cada uno y quién paga la siguiente.

### Reglas

- Paga el boleto siguiente (3,75 €) quien menos aciertos saca. Si hay empate, se
  reparte; los céntimos que no cuadran se ajustan en el primero (1,87 € + 1,88 €).
- El pleno al 15 lo rellena quien más acertó la jornada anterior. Si hay empate, o es
  la primera jornada, se sortea. Solo sirve si se aciertan los 14; no suma aciertos.
- Quien no registra columna antes del cierre cuenta con 0 aciertos.

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
4. *Project Settings → API*: apunta la **URL**, la **anon key** y la **service_role key**.
   La service_role key es secreta: solo va en GitHub Secrets, nunca en la web.

### 2. Bot de Telegram

1. Habla con **@BotFather** → `/newbot` → apunta el **token**.
2. En @BotFather, `/setprivacy` → elige el bot → **Disable** (para que vea quién escribe
   en el grupo y poder mencionar a cada uno).
3. Crea el grupo de la quiniela, añade al bot y a la familia, y que cada uno escriba
   cualquier cosa en el grupo.

### 3. GitHub

1. Sube esta carpeta a un repositorio nuevo. Si es **público**, los minutos de Actions
   son ilimitados (aquí no hay ningún secreto en el código).
2. *Settings → Secrets and variables → Actions*:
   - Secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`
   - Variable: `WEB_URL` (la dirección de la web del paso 4)
3. Para sacar el `TELEGRAM_CHAT_ID` y los usuarios: *Actions → Quiniela → Run workflow →
   telegram-ids*. En el log verás `chat_id=-100…` (el grupo) y el `user_id` de cada uno.
   Guarda los de cada persona:
   ```sql
   update jugadores set telegram_user_id = 123456789 where nombre = 'Rosana';
   ```

### 4. Web

1. Rellena `web/config.js` con la URL y la **anon key** de Supabase.
2. *Settings → Pages → Source: GitHub Actions*. Al subir el cambio se publica en
   `https://<usuario>.github.io/<repo>/`. Esa es la `WEB_URL`.
3. Para ver la web sin base de datos: añade `?demo` a la dirección.
   Para un enlace que ya lleve elegido el nombre: `?j=5` (Borja).

### 5. Comprobar Loterías

Loterías no publica una API documentada; el bot usa los servicios JSON de su web.
Antes de la primera jornada: *Actions → Quiniela → Run workflow → diagnostico-lae*.
En el log debe salir la próxima jornada con sus 14 partidos + pleno y las horas.
Si sale vacío o raro, el formato ha cambiado y hay que ajustar `bot/lae.py`.

### Opcional: quién paga la primera jornada

Si quieres que el primer anuncio diga quién paga, crea la última jornada del sistema
antiguo como finalizada y apunta al perdedor:
```sql
insert into jornadas (temporada, numero, primer_partido, finalizada)
values ('2026-2027', 13, '2026-10-17 16:00+02', true);
insert into pagos (jornada_id, jugador_id, importe)
values ((select id from jornadas where numero = 13), 3, 3.75);
```

## Desarrollo

```bash
pip install -r requirements.txt
python -m tests.test_simulacion salida/   # simula una jornada entera sin red
python -m bot.main tick --dry-run salida/ # usa Supabase y Loterías reales, sin enviar a Telegram
```
