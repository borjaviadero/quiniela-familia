-- =====================================================================
--  Quiniela familiar · Esquema de base de datos (Supabase / Postgres)
--  Ejecutar entero en: Supabase > SQL Editor > New query > Run
-- =====================================================================

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------
--  Tablas
-- ---------------------------------------------------------------------

create table if not exists jugadores (
  id                 smallint primary key,
  nombre             text not null unique,
  pin_hash           text not null,                -- crypt(pin, gen_salt('bf'))
  telegram_user_id   bigint,                       -- para mencionarle en el grupo
  intentos_fallidos  smallint not null default 0,
  bloqueado_hasta    timestamptz
);

create table if not exists jornadas (
  id                   bigserial primary key,
  temporada            text not null,              -- p. ej. '2026-2027'
  numero               smallint not null,          -- jornada de la Quiniela
  fecha_sorteo         date,
  id_sorteo_lae        text,                       -- id del sorteo en Loterías
  primer_partido       timestamptz not null,       -- cierre = 24 h antes · aviso = 48 h antes
  pleno_jugador_id     smallint references jugadores(id),
  pleno_por_sorteo     boolean not null default false,
  -- Banderas para que cada mensaje se envíe una sola vez
  anunciada            boolean not null default false,
  recordatorio_enviado boolean not null default false,
  apuestas_publicadas  boolean not null default false,
  finalizada           boolean not null default false,
  premios_publicados   boolean not null default false,
  created_at           timestamptz not null default now(),
  unique (temporada, numero)
);

create table if not exists partidos (
  jornada_id      bigint not null references jornadas(id) on delete cascade,
  posicion        smallint not null check (posicion between 1 and 15),  -- 15 = pleno
  local           text not null,
  visitante       text not null,
  inicio          timestamptz,
  goles_local     smallint,
  goles_visitante smallint,
  signo           text check (signo in ('1','X','2')),
  primary key (jornada_id, posicion)
);

create table if not exists apuestas (
  jornada_id  bigint   not null references jornadas(id) on delete cascade,
  jugador_id  smallint not null references jugadores(id),
  signos      text     not null check (signos ~ '^[12X]{14}$'),
  pleno       text              check (pleno is null or pleno ~ '^[012M]-[012M]$'),
  updated_at  timestamptz not null default now(),
  primary key (jornada_id, jugador_id)
);

-- Partes diarios ya enviados (uno por jornada y día)
create table if not exists partes_diarios (
  jornada_id  bigint not null references jornadas(id) on delete cascade,
  dia         date   not null,
  enviado_at  timestamptz not null default now(),
  primary key (jornada_id, dia)
);

-- Perdedores de cada jornada: pagan el boleto de la jornada SIGUIENTE.
-- Para arrancar, puedes insertar a mano los perdedores de la última jornada
-- jugada con el sistema antiguo (crea esa jornada con finalizada = true).
create table if not exists pagos (
  jornada_id  bigint   not null references jornadas(id) on delete cascade,
  jugador_id  smallint not null references jugadores(id),
  importe     numeric(6,2) not null,
  primary key (jornada_id, jugador_id)
);

-- ---------------------------------------------------------------------
--  Seguridad: la web (rol anon) NO toca tablas, solo las funciones de abajo.
--  El bot usa la service_role key, que se salta RLS.
-- ---------------------------------------------------------------------
alter table jugadores      enable row level security;
alter table jornadas       enable row level security;
alter table partidos       enable row level security;
alter table apuestas       enable row level security;
alter table partes_diarios enable row level security;
alter table pagos          enable row level security;

-- ---------------------------------------------------------------------
--  Funciones internas
-- ---------------------------------------------------------------------

-- Jornada que enseña la web: primero la que está abierta para apostar (la más próxima);
-- si no hay, la que se está jugando; si no, la última terminada.
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

-- Comprueba el PIN con bloqueo de 15 min tras 5 fallos seguidos.
-- Devuelve NULL si es correcto o el texto del error. No lanza excepción
-- para que el contador de fallos quede guardado.
create or replace function comprobar_pin(p_jugador smallint, p_pin text) returns text
language plpgsql security definer set search_path = public, extensions as $$
declare j jugadores;
begin
  select * into j from jugadores where id = p_jugador for update;
  if not found then return 'Jugador desconocido'; end if;
  if j.bloqueado_hasta is not null and j.bloqueado_hasta > now() then
    return 'Demasiados intentos. Prueba otra vez en unos minutos.';
  end if;
  if j.pin_hash <> crypt(coalesce(p_pin, ''), j.pin_hash) then
    update jugadores
       set intentos_fallidos = intentos_fallidos + 1,
           bloqueado_hasta = case when intentos_fallidos + 1 >= 5
                                  then now() + interval '15 minutes' end
     where id = p_jugador;
    return 'PIN incorrecto';
  end if;
  update jugadores set intentos_fallidos = 0, bloqueado_hasta = null where id = p_jugador;
  return null;
end $$;

-- ---------------------------------------------------------------------
--  API pública para la web
-- ---------------------------------------------------------------------

-- Todo lo que la web necesita para pintar el boleto.
-- Las columnas de los demás solo se ven cuando están las 5 o ya ha cerrado el plazo.
create or replace function estado_publico() returns json
language plpgsql stable security definer set search_path = public, extensions as $$
declare
  jo jornadas;
  hechas int;
  visibles boolean;
begin
  jo := jornada_actual();
  if jo.id is null then return json_build_object('jornada', null); end if;

  select count(*) into hechas from apuestas where jornada_id = jo.id;
  visibles := hechas >= (select count(*) from jugadores) or now() >= jo.primer_partido - interval '24 hours';

  return json_build_object(
    'jornada', json_build_object(
      'id', jo.id, 'numero', jo.numero, 'temporada', jo.temporada,
      'primer_partido', jo.primer_partido, 'cierre', jo.primer_partido - interval '24 hours',
      'pleno_jugador_id', jo.pleno_jugador_id, 'finalizada', jo.finalizada,
      'abierta', now() < jo.primer_partido - interval '24 hours'),
    'partidos', (select coalesce(json_agg(p order by p.posicion), '[]')
                   from (select posicion, local, visitante, inicio,
                                goles_local, goles_visitante, signo
                           from partidos where jornada_id = jo.id) p),
    'jugadores', (select json_agg(json_build_object(
                     'id', j.id, 'nombre', j.nombre,
                     'hecha', exists (select 1 from apuestas a
                                       where a.jornada_id = jo.id and a.jugador_id = j.id))
                   order by j.id) from jugadores j),
    'apuestas', case when visibles then
                  (select coalesce(json_agg(json_build_object(
                     'jugador_id', a.jugador_id, 'signos', a.signos, 'pleno', a.pleno)), '[]')
                     from apuestas a where a.jornada_id = jo.id)
                else null end
  );
end $$;

-- Devuelve la apuesta propia (para precargar el formulario)
create or replace function mi_apuesta(p_jugador smallint, p_pin text) returns json
language plpgsql security definer set search_path = public, extensions as $$
declare jo jornadas; r json; err text;
begin
  err := comprobar_pin(p_jugador, p_pin);
  if err is not null then return json_build_object('error', err); end if;
  jo := jornada_actual();
  select json_build_object('signos', signos, 'pleno', pleno, 'updated_at', updated_at)
    into r from apuestas where jornada_id = jo.id and jugador_id = p_jugador;
  return coalesce(r, json_build_object('signos', null));
end $$;

-- Registra o modifica la apuesta propia. Solo hasta 24 h antes del primer partido.
create or replace function registrar_apuesta(
  p_jugador smallint, p_pin text, p_signos text, p_pleno text default null
) returns json
language plpgsql security definer set search_path = public, extensions as $$
declare jo jornadas; err text;
begin
  err := comprobar_pin(p_jugador, p_pin);
  if err is not null then return json_build_object('error', err); end if;

  jo := jornada_actual();
  if jo.id is null or jo.finalizada then
    return json_build_object('error', 'No hay jornada abierta');
  end if;
  if now() >= jo.primer_partido - interval '24 hours' then
    return json_build_object('error', 'El plazo de esta jornada ya está cerrado');
  end if;

  p_signos := upper(coalesce(p_signos, ''));
  if p_signos !~ '^[12X]{14}$' then
    return json_build_object('error', 'Faltan partidos por marcar');
  end if;

  if p_jugador = jo.pleno_jugador_id then
    p_pleno := upper(coalesce(p_pleno, ''));
    if p_pleno !~ '^[012M]-[012M]$' then
      return json_build_object('error', 'Te toca el pleno al 15: marca los goles de los dos equipos');
    end if;
  else
    p_pleno := null;   -- el pleno solo lo rellena a quien le toca
  end if;

  insert into apuestas (jornada_id, jugador_id, signos, pleno, updated_at)
  values (jo.id, p_jugador, p_signos, p_pleno, now())
  on conflict (jornada_id, jugador_id)
  do update set signos = excluded.signos, pleno = excluded.pleno, updated_at = now();

  return json_build_object('ok', true, 'jornada', jo.numero);
end $$;

-- Gestión de PIN (solo desde el SQL Editor o el bot)
create or replace function fijar_pin(p_jugador smallint, p_pin text) returns void
language sql security definer set search_path = public, extensions as $$
  update jugadores set pin_hash = crypt(p_pin, gen_salt('bf')),
                       intentos_fallidos = 0, bloqueado_hasta = null
   where id = p_jugador;
$$;

-- Permisos: la web solo puede llamar a estas tres funciones
revoke all on all functions in schema public from anon, authenticated;
grant execute on function estado_publico()                                to anon;
grant execute on function mi_apuesta(smallint, text)                      to anon;
grant execute on function registrar_apuesta(smallint, text, text, text)   to anon;

-- ---------------------------------------------------------------------
--  Jugadores (cambia los PIN antes de ejecutar, o luego con fijar_pin)
-- ---------------------------------------------------------------------
insert into jugadores (id, nombre, pin_hash) values
  (1, 'Luis Angel', crypt('1111', gen_salt('bf'))),
  (2, 'Rosana',     crypt('2222', gen_salt('bf'))),
  (3, 'Joseba',     crypt('3333', gen_salt('bf'))),
  (4, 'Lara',       crypt('4444', gen_salt('bf'))),
  (5, 'Borja',      crypt('5555', gen_salt('bf')))
on conflict (id) do nothing;
