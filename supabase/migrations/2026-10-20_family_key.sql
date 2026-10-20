-- =====================================================================
--  Migration · 2026-10-20 · Family key
--
--  The web app now needs a family key (a long random secret carried in the link
--  posted to the Telegram group) on top of each player's PIN, so nobody outside
--  the family can use the website even if they know a PIN.
--  It also closes a hole in the original schema: PostgreSQL grants EXECUTE on every
--  function to PUBLIC by default, so internal functions such as set_pin could be
--  called with the public key. Now only the three website functions are callable.
--  Fresh installs do NOT need it: supabase/schema.sql already includes it.
--
--  After running it, generate the key with:   select set_family_key();
--  and save the returned value as the FAMILY_KEY secret in GitHub.
-- =====================================================================

begin;

create table if not exists family_access (
  id        smallint primary key default 1 check (id = 1),
  key_hash  text not null
);
alter table family_access enable row level security;

-- The public functions get a new first parameter (p_key)
drop function if exists public_state();
drop function if exists my_pick(smallint, text);
drop function if exists submit_pick(smallint, text, text, text);

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

-- Creates or updates the player's own pick. Allowed until 24 h before the first kickoff.
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

commit;
