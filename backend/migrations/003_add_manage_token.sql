-- Existing Supabase DBs: run once in the SQL Editor.
-- Prevents strangers from overwriting a subscription by email alone.

alter table public.users
  add column if not exists manage_token uuid;

alter table public.users
  add column if not exists recover_token uuid;

alter table public.users
  add column if not exists recover_token_expires_at timestamptz;

update public.users
set manage_token = gen_random_uuid()
where manage_token is null;

alter table public.users
  alter column manage_token set default gen_random_uuid();

alter table public.users
  alter column manage_token set not null;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'users_manage_token_unique'
  ) then
    alter table public.users
      add constraint users_manage_token_unique unique (manage_token);
  end if;
end $$;

comment on column public.users.manage_token is
  'Opaque ownership token; required on POST /api/subscribe updates. Stored in the extension.';

comment on column public.users.recover_token is
  'One-time email recovery token to rotate manage_token if the extension lost it.';
