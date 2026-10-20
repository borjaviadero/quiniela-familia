-- Migración 2 (20/10/2026): marcar si ya se han publicado los premios de cada jornada.
-- Ejecutar una vez en Supabase > SQL Editor.
alter table jornadas add column if not exists premios_publicados boolean not null default false;
