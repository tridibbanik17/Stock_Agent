-- Existing Supabase DBs: run once in the SQL Editor.
-- New installs already get this column from database_schema.sql.

alter table public.users
  add column if not exists unsubscribe_token uuid;

update public.users
set unsubscribe_token = gen_random_uuid()
where unsubscribe_token is null;

alter table public.users
  alter column unsubscribe_token set default gen_random_uuid();

alter table public.users
  alter column unsubscribe_token set not null;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'users_unsubscribe_token_unique'
  ) then
    alter table public.users
      add constraint users_unsubscribe_token_unique unique (unsubscribe_token);
  end if;
end $$;

comment on column public.users.unsubscribe_token is
  'Opaque token for one-click email unsubscribe (GET/POST/DELETE /api/unsubscribe).';
