-- Migración 1 (20/10/2026): la web enseña la jornada abierta para apostar aunque
-- otra se esté jugando todavía. Ejecutar una vez en Supabase > SQL Editor.
create or replace function jornada_actual() returns jornadas
language sql stable security definer set search_path = public, extensions as $$
  select * from jornadas
  order by
    case when not finalizada and now() < primer_partido - interval '24 hours' then 0
         when not finalizada then 1 else 2 end,
    case when not finalizada and now() < primer_partido - interval '24 hours'
         then extract(epoch from primer_partido) else -extract(epoch from primer_partido) end
  limit 1;
$$;
revoke all on function jornada_actual() from anon, authenticated;
