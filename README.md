# Family Quiniela

A serverless bot that runs my family's weekly **Quiniela** (the Spanish football pools)
end to end: it detects each new LaLiga matchday from the official lottery data, collects
everyone's predictions through a small web app, posts progress and results to a Telegram
group, works out who pays the next ticket and reports any prize won. It runs entirely on
free tiers, with no server to maintain.

📄 **Full guide** (functional and technical, for any reader):
[English](docs/Family-Quiniela-Guide-EN.pdf) · [Español](docs/Family-Quiniela-Guide-ES.pdf)

> The code, documentation and database are in English. Everything the players see
> (Telegram messages, ticket images and the web app) is in Spanish, their language.

## Highlights

- **Fully automated pipeline on a schedule** with GitHub Actions: no servers, no manual steps.
- **Idempotent design**: every message is flagged in the database once sent, so runs can be
  repeated or overlap safely and nothing is ever duplicated.
- **Unofficial data source handled defensively**: the lottery has no public API, so the
  client uses its website's JSON endpoints with browser impersonation, tolerant parsing and
  a built-in `diagnose` command.
- **Self-maintaining rules**: the LaLiga team list is pulled from ESPN every season, kickoff
  changes move the betting deadline automatically, and postponed matches are handled.
- **Secure by default**: PINs hashed with bcrypt and rate-limited, Row Level Security with
  the public site restricted to three database functions, secrets kept out of the code,
  and encrypted backups in a public repository.
- **Tested**: an offline simulation exercises a full matchday (announcement → results →
  payments → prizes) including edge cases.

## Architecture

```
 Loterías y Apuestas ─┐                                 ┌─> Telegram group
 (fixtures, results,  ├─> Bot (Python, GitHub Actions) ─┤   (announcements, images,
  prize breakdown)    │          │                      │    reminders, results, prizes)
 ESPN (LaLiga teams) ─┘          ▼                      │
                          Supabase (PostgreSQL) <───────┴── Web app (GitHub Pages)
                                                             players submit their picks
```

| Component | Technology | Role |
|---|---|---|
| Bot | Python 3.12 on GitHub Actions (`bot/`) | Reads the official data, computes hits, posts to Telegram |
| Web app | Static HTML + JS on GitHub Pages (`web/`) | Each player picks their name, enters a PIN and fills in their column |
| Database | Supabase / PostgreSQL (`supabase/`) | Matchdays, fixtures, picks, reports sent, payments |
| Messaging | Telegram Bot API | Ticket images, reminders, who pays, prizes |
| Data | Loterías y Apuestas del Estado (LAE) | Official fixtures, kickoff times, results, prize breakdown |
| Teams | ESPN public API | Current LaLiga top-flight teams, for the league filter |
| Backup | GitHub Actions (`backups/`) | Weekly encrypted database backup |

## How a matchday works

1. **LAE publishes a LaLiga Quiniela** → the bot creates the matchday, chooses who fills in
   the *Pleno al 15* and posts the fixtures image with a *Hacer mi quiniela* button.
2. **Each player submits a column** on the web app with their PIN, and can change it until
   **24 h before the first kickoff**.
3. **48 h before the first kickoff** → reminder in the group, mentioning whoever is missing.
4. **When all five are in** (or betting closes) → image with every column, ready to be
   stamped at the lottery shop, and who pays for it.
5. **After the last match of each day** → image with hits (green), misses (red) and the leader.
6. **When the matchday ends** → final image, everyone's hits and who pays the next ticket.
7. **When LAE publishes the prize breakdown** → how much each winning column gets, if any.

The bot runs Tuesday–Thursday every hour and Friday–Monday every 20 minutes.

## Game rules

**Who pays**
- The player with the fewest hits pays the next ticket (€3.75). Ties split it, with leftover
  cents assigned to the first player (€1.87 + €1.88).
- A player who misses the deadline counts as 0 hits.
- Counting started at matchday 16 of the 2026-27 season.

**Pleno al 15** (the 15th match, where each team's goals are predicted: 0, 1, 2 or M = 3+)
- Filled in by the best player of the previous matchday; ties, or the first matchday, are
  drawn at random. If the previous matchday is still being played, the last finished one is used.
- It does not add hits. There is one per ticket, so it counts for any column with 14 hits.

**Which Quinielas are played**
- Only **LaLiga** ones: at least 5 matches between two top-flight teams. Champions League,
  Copa del Rey, etc. are skipped. Women's teams are shown with **(F)** and never count.

**Overlapping matchdays**
- If a matchday (e.g. a midweek LaLiga round) is published while another is still being
  played, both are handled independently: own announcement, deadline, columns, reports and
  ticket. The announcement says so explicitly, and the web app always shows the matchday
  open for betting.

**Postponements and rescheduling**
- Until kickoff, fixtures are re-read on every run. If the **first kickoff** moves while
  betting is open, the deadline moves with it and the group is told.
- If a match has no result 8 hours after the last kickoff of its day, that day's report is
  sent anyway, naming the missing match.

**Prizes**
- From the official breakdown, one line per winning column:
  *«Borja ha tenido 11 aciertos por lo que corresponde 2,22 € según el sorteo.»*

## Setup (about 30 minutes)

### 1. Supabase
1. Create a free project at <https://supabase.com>.
2. *SQL Editor* → paste `supabase/schema.sql` → **Run**.
3. Set real PINs: `select set_pin(1::smallint, '4821');` (ids 1–5 are the players).
4. *Project Settings → API Keys → Legacy anon, service_role API keys*: note the **URL**,
   the **anon key** and the **service_role key**. The service_role key is secret and only
   goes into GitHub Secrets.

### 2. Telegram bot
1. Talk to **@BotFather** → `/newbot` → note the **token**.
2. `/setprivacy` → your bot → **Disable** (before adding it to the group), so it can see
   who writes and mention people.
3. Create the group, add the bot and the players, and have everyone send a message.

### 3. GitHub
1. Push this folder to a **public** repository (unlimited Actions minutes; no secrets in code).
2. *Settings → Secrets and variables → Actions*:
   - Secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`,
     `BACKUP_KEY` (a long made-up password that encrypts backups; **store it elsewhere
     too**, it cannot be recovered from GitHub)
   - Variable: `WEB_URL` (the GitHub Pages address from step 4)
3. To find the chat and user ids, open `https://api.telegram.org/bot<TOKEN>/getUpdates`
   after writing in the group (or *Actions → Bot → Run workflow → telegram-ids*). The group
   id is negative. Store each player's id:
   `update players set telegram_user_id = 123456789 where name = 'Rosana';`

### 4. Web app
1. Fill in `web/config.js` with the Supabase URL and **anon** key (public by design).
2. *Settings → Pages → Source: GitHub Actions*. It is published at
   `https://<user>.github.io/<repo>/`.
3. Preview without a database: add `?demo` to the URL. Preselect a player: `?p=5`.

### 5. Check the data sources
*Actions → Bot → Run workflow → diagnose* prints the ESPN team list, the upcoming
matchdays with their 15 fixtures (and whether they qualify as LaLiga) and recent prize
breakdowns. If something comes back empty, LAE changed its website and `bot/lae.py`
needs adjusting.

## Backups

Supabase's free tier keeps no backups, so every **Tuesday** the *Backup* workflow exports
all tables, encrypts them with `BACKUP_KEY` and commits `backups/quiniela.json.enc`; git
history keeps every previous version. To restore:

```bash
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in backups/quiniela.json.enc -out backup.json
python -m bot.backup restore backup.json      # needs SUPABASE_URL and SUPABASE_SERVICE_KEY
```
Then, in the SQL Editor: `select setval('matchdays_id_seq', (select coalesce(max(id), 1) from matchdays));`

> Because the bot commits a backup every week, run `git pull` before pushing your own changes.

## Maintenance

- **A run fails** (GitHub emails you): *Actions* → the red run → *bot* → *Run*, and read the error.
- **The bot stops posting**: if the Telegram group became a *supergroup* its id changed
  (it starts with `-100`); update `TELEGRAM_CHAT_ID`.
- **Re-send a matchday announcement**:
  `update matchdays set announced = false where number = 16;` then *Run workflow → tick*.
- **GitHub Actions versions**: workflows use `actions/checkout@v5`, `actions/setup-python@v6`
  and `actions/upload-pages-artifact@v4`, and force Node.js 24.
- **`supabase/migrations/`** holds one-off scripts already applied to the production
  database. Fresh installs only need `schema.sql`.

## Project structure

```
.github/workflows/quiniela.yml   Bot: schedule and secrets
.github/workflows/web.yml        Deploys web/ to GitHub Pages
.github/workflows/backup.yml     Weekly encrypted database backup
bot/main.py                      Orchestration: one idempotent run (tick) and the CLI
bot/rules.py                     Game rules: hits, who pays, Pleno, daily reports, league filter, prizes
bot/lae.py                       Client for the official lottery data (fixtures, results, prizes)
bot/teams.py                     LaLiga team list from ESPN and fuzzy name matching
bot/messages.py                  Every user-facing text (Spanish)
bot/ticket.py                    Renders the ticket images (Pillow)
bot/db.py                        Supabase (PostgREST) access
bot/telegram.py                  Telegram Bot API client
bot/backup.py                    Database export and restore
web/index.html · web/config.js   Betting web app
supabase/schema.sql              Database schema, security and functions
supabase/migrations/             One-off scripts applied to the production database
tests/test_simulation.py         Offline simulation of whole matchdays
docs/                            Full guide (English and Spanish)
backups/                         Encrypted database backups
```

## Development

```bash
pip install -r requirements.txt
python -m tests.test_simulation output/   # full offline simulation; images and messages land in output/
python -m bot.main tick --dry-run output/ # real Supabase and LAE data, but nothing is sent to Telegram
```
