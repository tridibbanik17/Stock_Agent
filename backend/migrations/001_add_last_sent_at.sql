-- Existing Supabase DBs: run once in the SQL Editor.
-- New installs already get this column from database_schema.sql.

alter table public.users
  add column if not exists last_sent_at timestamptz;

comment on column public.users.last_sent_at is
  'UTC timestamp of last successful cron email; used to skip duplicate sends in the same preferred-hour window.';
