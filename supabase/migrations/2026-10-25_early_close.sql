-- =====================================================================
--  Migration · 2026-10-25 · Early close from Telegram
--
--  Once everyone has submitted their column, the bot asks whoever pays the ticket
--  whether to close betting now (Yes/No buttons in Telegram). If they say yes,
--  betting closes at that moment instead of 24 h before the first kickoff.
--  Also adds telegram_users (members seen in the group, to find each player's id)
--  and bot_state (the last Telegram update processed).
--  Fresh installs do NOT need it: supabase/schema.sql already includes it.
-- =====================================================================

begin;

alter table matchdays
  add column if not exists closed_early_at            timestamptz,
  add column if not exists close_question_message_id  bigint,
  add column if not exists close_question_asked_for   timestamptz;

create table if not exists telegram_users (
  user_id     bigint primary key,
  first_name  text,
  username    text,
  last_seen   timestamptz not null default now()
);
create table if not exists bot_state (
  key    text primary key,
  value  text not null
);
alter table telegram_users enable row level security;
alter table bot_state      enable row level security;

create or replace function betting_close(md matchdays) returns timestamptz
language sql stable set search_path = public as $$
  select least(md.first_kickoff - interval '24 hours', coalesce(md.closed_early_at, 'infinity'::timestamptz));
$$;

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

-- Only the three website functions stay callable with the public key
revoke all on all functions in schema public from public, anon, authenticated;
grant execute on function public_state(text)                              to anon;
grant execute on function my_pick(text, smallint, text)                   to anon;
grant execute on function submit_pick(text, smallint, text, text, text)   to anon;

commit;
