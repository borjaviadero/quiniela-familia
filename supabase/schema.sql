-- =====================================================================
--  Family Quiniela · Database schema (Supabase / PostgreSQL)
--  Fresh install: run this whole file in Supabase > SQL Editor > Run.
-- =====================================================================

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------
--  Tables
-- ---------------------------------------------------------------------

create table if not exists players (
  id                smallint primary key,
  name              text not null unique,
  pin_hash          text not null,                -- crypt(pin, gen_salt('bf'))
  telegram_user_id  bigint,                       -- to mention them in the group
  failed_attempts   smallint not null default 0,
  locked_until      timestamptz
);

create table if not exists matchdays (
  id                bigserial primary key,
  season            text not null,                -- e.g. '2026-2027'
  number            smallint not null,            -- official Quiniela matchday (jornada)
  draw_date         date,
  lae_draw_id       text,                         -- draw id at Loterías y Apuestas del Estado
  first_kickoff     timestamptz not null,         -- betting closes 24 h before · reminder 48 h before
  pleno_player_id   smallint references players(id),
  pleno_by_draw     boolean not null default false,
  -- Flags so that every message is sent exactly once
  announced         boolean not null default false,
  reminder_sent     boolean not null default false,
  picks_published   boolean not null default false,
  finished          boolean not null default false,
  prizes_published  boolean not null default false,
  -- Early close: once everyone is in, whoever pays can close betting from Telegram
  closed_early_at            timestamptz,
  close_question_message_id  bigint,          -- the Telegram message with the Yes/No buttons
  close_question_asked_for   timestamptz,     -- latest pick change when it was asked
  created_at        timestamptz not null default now(),
  unique (season, number)
);

create table if not exists matches (
  matchday_id  bigint not null references matchdays(id) on delete cascade,
  position     smallint not null check (position between 1 and 15),  -- 15 = Pleno al 15
  home         text not null,
  away         text not null,
  kickoff      timestamptz,
  home_goals   smallint,
  away_goals   smallint,
  sign         text check (sign in ('1','X','2')),
  primary key (matchday_id, position)
);

create table if not exists picks (
  matchday_id  bigint   not null references matchdays(id) on delete cascade,
  player_id    smallint not null references players(id),
  signs        text     not null check (signs ~ '^[12X]{14}$'),
  pleno        text              check (pleno is null or pleno ~ '^[012M]-[012M]$'),
  updated_at   timestamptz not null default now(),
  primary key (matchday_id, player_id)
);

-- Daily progress reports already sent (one per matchday and day)
create table if not exists daily_reports (
  matchday_id  bigint not null references matchdays(id) on delete cascade,
  day          date   not null,
  sent_at      timestamptz not null default now(),
  primary key (matchday_id, day)
);

-- Losers of each matchday: they pay the ticket of the NEXT matchday
create table if not exists payments (
  matchday_id  bigint   not null references matchdays(id) on delete cascade,
  player_id    smallint not null references players(id),
  amount       numeric(6,2) not null,
  primary key (matchday_id, player_id)
);

-- The family key: a long random secret that travels inside the link the bot posts
-- to the Telegram group. Without it the website can do nothing, even with a PIN.
-- Only its hash is stored. Generate it with:  select set_family_key();
create table if not exists family_access (
  id        smallint primary key default 1 check (id = 1),
  key_hash  text not null
);

-- Telegram users seen in the group (to find each player's telegram_user_id)
create table if not exists telegram_users (
  user_id     bigint primary key,
  first_name  text,
  username    text,
  last_seen   timestamptz not null default now()
);

-- Small key/value store for the bot (e.g. the last Telegram update processed)
create table if not exists bot_state (
  key    text primary key,
  value  text not null
);

-- ---------------------------------------------------------------------
--  Security: the website (anon role) cannot touch tables, only the
--  functions below. The bot uses the service_role key, which bypasses RLS.
-- ---------------------------------------------------------------------
alter table players       enable row level security;
alter table matchdays     enable row level security;
alter table matches       enable row level security;
alter table picks         enable row level security;
alter table daily_reports enable row level security;
alter table payments      enable row level security;
alter table family_access enable row level security;
alter table telegram_users enable row level security;
alter table bot_state     enable row level security;

-- =====================================================================
--  Functions
-- =====================================================================

-- When betting closes: 24 h before the first kickoff, or earlier if whoever pays
-- closed it from Telegram once everyone was in.
create or replace function betting_close(md matchdays) returns timestamptz
language sql stable set search_path = public as $$
  select least(md.first_kickoff - interval '24 hours', coalesce(md.closed_early_at, 'infinity'::timestamptz));
$$;

-- Matchday shown on the website: the one open for betting (the nearest), otherwise
-- the one being played, otherwise the last finished one.
create or replace function current_matchday() returns matchdays
language sql stable security definer set search_path = public, extensions as $$
  select * from matchdays m
  order by
    case when not m.finished and now() < betting_close(m) then 0
         when not m.finished then 1 else 2 end,
    case when not m.finished and now() < betting_close(m)
         then extract(epoch from m.first_kickoff) else -extract(epoch from m.first_kickoff) end
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

-- True when p_key is the current family key
create or replace function check_family_key(p_key text) returns boolean
language sql stable security definer set search_path = public, extensions as $$
  select exists (select 1 from family_access
                  where p_key is not null and key_hash = crypt(p_key, key_hash));
$$;

-- ---------------------------------------------------------------------
--  Public API used by the website (errors are returned as {"error": code}).
--  Every call needs the family key; picks also need the player's PIN.
-- ---------------------------------------------------------------------

-- Everything the website needs to draw the ticket. Other players' columns are only
-- visible once all of them are in or betting has closed.
create or replace function public_state(p_key text) returns json
language plpgsql stable security definer set search_path = public, extensions as $$
declare
  md matchdays;
  submitted int;
  visible boolean;
begin
  if not check_family_key(p_key) then return json_build_object('error', 'family_key_required'); end if;
  md := current_matchday();
  if md.id is null then return json_build_object('matchday', null); end if;

  select count(*) into submitted from picks where matchday_id = md.id;
  visible := submitted >= (select count(*) from players) or now() >= betting_close(md);

  return json_build_object(
    'matchday', json_build_object(
      'id', md.id, 'number', md.number, 'season', md.season,
      'first_kickoff', md.first_kickoff, 'close', betting_close(md),
      'pleno_player_id', md.pleno_player_id, 'finished', md.finished,
      'open', now() < betting_close(md)),
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
create or replace function my_pick(p_key text, p_player smallint, p_pin text) returns json
language plpgsql security definer set search_path = public, extensions as $$
declare md matchdays; r json; err text;
begin
  if not check_family_key(p_key) then return json_build_object('error', 'family_key_required'); end if;
  err := check_pin(p_player, p_pin);
  if err is not null then return json_build_object('error', err); end if;
  md := current_matchday();
  select json_build_object('signs', signs, 'pleno', pleno, 'updated_at', updated_at)
    into r from picks where matchday_id = md.id and player_id = p_player;
  return coalesce(r, json_build_object('signs', null));
end $$;

-- Creates or updates the player's own pick. Allowed until betting closes.
create or replace function submit_pick(
  p_key text, p_player smallint, p_pin text, p_signs text, p_pleno text default null
) returns json
language plpgsql security definer set search_path = public, extensions as $$
declare md matchdays; err text;
begin
  if not check_family_key(p_key) then return json_build_object('error', 'family_key_required'); end if;
  err := check_pin(p_player, p_pin);
  if err is not null then return json_build_object('error', err); end if;

  md := current_matchday();
  if md.id is null or md.finished then
    return json_build_object('error', 'no_open_matchday');
  end if;
  if now() >= betting_close(md) then
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

-- Family key management (SQL Editor only). Generates a new random key, stores its
-- hash and returns the key once: save it as the FAMILY_KEY secret in GitHub.
-- Running it again revokes the previous key.
create or replace function set_family_key() returns text
language plpgsql security definer set search_path = public, extensions as $$
declare k text := translate(encode(gen_random_bytes(18), 'base64'), '+/=', '-_');
begin
  insert into family_access (id, key_hash) values (1, crypt(k, gen_salt('bf')))
  on conflict (id) do update set key_hash = excluded.key_hash;
  return k;
end $$;

-- Permissions: the website may only call these three functions. PostgreSQL lets
-- PUBLIC execute every function by default, so that is revoked too (otherwise anyone
-- with the public key could call set_pin or set_family_key).
revoke all on all functions in schema public from public, anon, authenticated;
grant execute on function public_state(text)                              to anon;
grant execute on function my_pick(text, smallint, text)                   to anon;
grant execute on function submit_pick(text, smallint, text, text, text)   to anon;

-- =====================================================================
--  Players. They are created with a random, unknown PIN: set each real PIN
--  afterwards, privately, in the SQL Editor (never commit real PINs):
--    select set_pin(1::smallint, '....');
-- =====================================================================
insert into players (id, name, pin_hash)
select id, name, crypt(gen_random_uuid()::text, gen_salt('bf'))
  from (values (1, 'Luis Angel'), (2, 'Rosana'), (3, 'Joseba'), (4, 'Lara'), (5, 'Borja')) as p(id, name)
on conflict (id) do nothing;
