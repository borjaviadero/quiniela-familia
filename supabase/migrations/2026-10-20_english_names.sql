-- =====================================================================
--  Migration · 2026-10-20 · Spanish -> English names
--
--  One-off script for the production database created with the original
--  Spanish schema. It renames tables, columns, the sequence and the functions
--  to the English names used by the code. All data is kept.
--  Fresh installs do NOT need it: supabase/schema.sql already uses these names.
--
--  Run it once in Supabase > SQL Editor, right before deploying the matching code.
-- =====================================================================

begin;

-- ---- Old functions (they reference the old table names) ----------------
drop function if exists registrar_apuesta(smallint, text, text, text);
drop function if exists mi_apuesta(smallint, text);
drop function if exists estado_publico();
drop function if exists fijar_pin(smallint, text);
drop function if exists comprobar_pin(smallint, text);
drop function if exists jornada_actual();

-- ---- Tables -------------------------------------------------------------
alter table jugadores      rename to players;
alter table jornadas       rename to matchdays;
alter table partidos       rename to matches;
alter table apuestas       rename to picks;
alter table partes_diarios rename to daily_reports;
alter table pagos          rename to payments;
alter sequence jornadas_id_seq rename to matchdays_id_seq;

-- ---- Columns ------------------------------------------------------------
alter table players rename column nombre            to name;
alter table players rename column intentos_fallidos to failed_attempts;
alter table players rename column bloqueado_hasta   to locked_until;

alter table matchdays rename column temporada            to season;
alter table matchdays rename column numero               to number;
alter table matchdays rename column fecha_sorteo         to draw_date;
alter table matchdays rename column id_sorteo_lae        to lae_draw_id;
alter table matchdays rename column primer_partido       to first_kickoff;
alter table matchdays rename column pleno_jugador_id     to pleno_player_id;
alter table matchdays rename column pleno_por_sorteo     to pleno_by_draw;
alter table matchdays rename column anunciada            to announced;
alter table matchdays rename column recordatorio_enviado to reminder_sent;
alter table matchdays rename column apuestas_publicadas  to picks_published;
alter table matchdays rename column finalizada           to finished;
alter table matchdays rename column premios_publicados   to prizes_published;

alter table matches rename column jornada_id      to matchday_id;
alter table matches rename column posicion        to position;
alter table matches rename column local           to home;
alter table matches rename column visitante       to away;
alter table matches rename column inicio          to kickoff;
alter table matches rename column goles_local     to home_goals;
alter table matches rename column goles_visitante to away_goals;
alter table matches rename column signo           to sign;

alter table picks rename column jornada_id to matchday_id;
alter table picks rename column jugador_id to player_id;
alter table picks rename column signos     to signs;

alter table daily_reports rename column jornada_id to matchday_id;
alter table daily_reports rename column dia        to day;
alter table daily_reports rename column enviado_at to sent_at;

alter table payments rename column jornada_id to matchday_id;
alter table payments rename column jugador_id to player_id;
alter table payments rename column importe    to amount;

-- ---- Constraint and index names (cosmetic, so they match a fresh install) ---
do $$
declare r record; new_name text;
begin
  for r in
    select c.conrelid::regclass as tbl, c.conname
      from pg_constraint c join pg_namespace n on n.oid = c.connamespace
     where n.nspname = 'public'
       and c.conrelid::regclass::text in ('players','matchdays','matches','picks','daily_reports','payments')
  loop
    new_name := r.conname;
    new_name := replace(new_name, 'jugadores', 'players');
    new_name := replace(new_name, 'jornadas', 'matchdays');
    new_name := replace(new_name, 'partidos', 'matches');
    new_name := replace(new_name, 'apuestas', 'picks');
    new_name := replace(new_name, 'partes_diarios', 'daily_reports');
    new_name := replace(new_name, 'pagos', 'payments');
    new_name := replace(new_name, 'jornada_id', 'matchday_id');
    new_name := replace(new_name, 'jugador_id', 'player_id');
    new_name := replace(new_name, 'pleno_jugador_id', 'pleno_player_id');
    new_name := replace(new_name, 'temporada_numero', 'season_number');
    new_name := replace(new_name, 'nombre', 'name');
    new_name := replace(new_name, 'posicion', 'position');
    new_name := replace(new_name, 'signos', 'signs');
    new_name := replace(new_name, 'signo', 'sign');
    if new_name <> r.conname then
      execute format('alter table %s rename constraint %I to %I', r.tbl, r.conname, new_name);
    end if;
  end loop;
end $$;

-- ---- New functions (same as supabase/schema.sql) ------------------------
-- Matchday shown on the website: the one open for betting (the nearest), otherwise
-- the one being played, otherwise the last finished one.
create or replace function current_matchday() returns matchdays
language sql stable security definer set search_path = public, extensions as $$
  select * from matchdays
  order by
    case when not finished and now() < first_kickoff - interval '24 hours' then 0
         when not finished then 1 else 2 end,
    case when not finished and now() < first_kickoff - interval '24 hours'
         then extract(epoch from first_kickoff) else -extract(epoch from first_kickoff) end
  limit 1;
$$;

-- Checks a PIN, locking the player for 15 minutes after 5 failures in a row.
-- Returns NULL when correct, or an error code. It never raises, so the failure
-- counter is committed.
create or replace function check_pin(p_player smallint, p_pin text) returns text
language plpgsql security definer set search_path = public, extensions as $$
declare pl players;
begin
  select * into pl from players where id = p_player for update;
  if not found then return 'unknown_player'; end if;
  if pl.locked_until is not null and pl.locked_until > now() then
    return 'too_many_attempts';
  end if;
  if pl.pin_hash <> crypt(coalesce(p_pin, ''), pl.pin_hash) then
    update players
       set failed_attempts = failed_attempts + 1,
           locked_until = case when failed_attempts + 1 >= 5
                               then now() + interval '15 minutes' end
     where id = p_player;
    return 'wrong_pin';
  end if;
  update players set failed_attempts = 0, locked_until = null where id = p_player;
  return null;
end $$;

-- ---------------------------------------------------------------------
--  Public API used by the website (errors are returned as {"error": code})
-- ---------------------------------------------------------------------

-- Everything the website needs to draw the ticket. Other players' columns are only
-- visible once all of them are in or betting has closed.
create or replace function public_state() returns json
language plpgsql stable security definer set search_path = public, extensions as $$
declare
  md matchdays;
  submitted int;
  visible boolean;
begin
  md := current_matchday();
  if md.id is null then return json_build_object('matchday', null); end if;

  select count(*) into submitted from picks where matchday_id = md.id;
  visible := submitted >= (select count(*) from players) or now() >= md.first_kickoff - interval '24 hours';

  return json_build_object(
    'matchday', json_build_object(
      'id', md.id, 'number', md.number, 'season', md.season,
      'first_kickoff', md.first_kickoff, 'close', md.first_kickoff - interval '24 hours',
      'pleno_player_id', md.pleno_player_id, 'finished', md.finished,
      'open', now() < md.first_kickoff - interval '24 hours'),
    'matches', (select coalesce(json_agg(m order by m.position), '[]')
                  from (select position, home, away, kickoff, home_goals, away_goals, sign
                          from matches where matchday_id = md.id) m),
    'players', (select json_agg(json_build_object(
                   'id', p.id, 'name', p.name,
                   'submitted', exists (select 1 from picks k
                                         where k.matchday_id = md.id and k.player_id = p.id))
                 order by p.id) from players p),
    'picks', case when visible then
               (select coalesce(json_agg(json_build_object(
                  'player_id', k.player_id, 'signs', k.signs, 'pleno', k.pleno)), '[]')
                  from picks k where k.matchday_id = md.id)
             else null end
  );
end $$;

-- The player's own pick (to pre-fill the form)
create or replace function my_pick(p_player smallint, p_pin text) returns json
language plpgsql security definer set search_path = public, extensions as $$
declare md matchdays; r json; err text;
begin
  err := check_pin(p_player, p_pin);
  if err is not null then return json_build_object('error', err); end if;
  md := current_matchday();
  select json_build_object('signs', signs, 'pleno', pleno, 'updated_at', updated_at)
    into r from picks where matchday_id = md.id and player_id = p_player;
  return coalesce(r, json_build_object('signs', null));
end $$;

-- Creates or updates the player's own pick. Allowed until 24 h before the first kickoff.
create or replace function submit_pick(
  p_player smallint, p_pin text, p_signs text, p_pleno text default null
) returns json
language plpgsql security definer set search_path = public, extensions as $$
declare md matchdays; err text;
begin
  err := check_pin(p_player, p_pin);
  if err is not null then return json_build_object('error', err); end if;

  md := current_matchday();
  if md.id is null or md.finished then
    return json_build_object('error', 'no_open_matchday');
  end if;
  if now() >= md.first_kickoff - interval '24 hours' then
    return json_build_object('error', 'betting_closed');
  end if;

  p_signs := upper(coalesce(p_signs, ''));
  if p_signs !~ '^[12X]{14}$' then
    return json_build_object('error', 'incomplete_picks');
  end if;

  if p_player = md.pleno_player_id then
    p_pleno := upper(coalesce(p_pleno, ''));
    if p_pleno !~ '^[012M]-[012M]$' then
      return json_build_object('error', 'pleno_required');
    end if;
  else
    p_pleno := null;   -- only the designated player fills in the Pleno al 15
  end if;

  insert into picks (matchday_id, player_id, signs, pleno, updated_at)
  values (md.id, p_player, p_signs, p_pleno, now())
  on conflict (matchday_id, player_id)
  do update set signs = excluded.signs, pleno = excluded.pleno, updated_at = now();

  return json_build_object('ok', true, 'number', md.number);
end $$;

-- PIN management (SQL Editor only)
create or replace function set_pin(p_player smallint, p_pin text) returns void
language sql security definer set search_path = public, extensions as $$
  update players set pin_hash = crypt(p_pin, gen_salt('bf')),
                     failed_attempts = 0, locked_until = null
   where id = p_player;
$$;

-- Permissions: the website may only call these three functions
revoke all on all functions in schema public from anon, authenticated;
grant execute on function public_state()                            to anon;
grant execute on function my_pick(smallint, text)                   to anon;
grant execute on function submit_pick(smallint, text, text, text)   to anon;

commit;
