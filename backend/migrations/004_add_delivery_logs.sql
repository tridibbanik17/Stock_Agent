-- Existing Supabase DBs: run once in the SQL Editor.
-- Stores cron send success/fail + Resend id for debugging.

create table if not exists public.delivery_logs (
  id uuid primary key default gen_random_uuid(),

  user_id uuid references public.users (id) on delete set null,

  email text not null,

  status text not null,

  resend_id text,

  subject text,
  ticker_count integer,

  error text,

  created_at timestamptz not null default now(),

  constraint delivery_logs_status_check check (
    status in ('success', 'failure', 'dry_run')
  ),
  constraint delivery_logs_email_format check (position('@' in email) > 1)
);

create index if not exists delivery_logs_created_at_idx
  on public.delivery_logs (created_at desc);

create index if not exists delivery_logs_email_idx
  on public.delivery_logs (email);

create index if not exists delivery_logs_user_id_idx
  on public.delivery_logs (user_id);

create index if not exists delivery_logs_status_idx
  on public.delivery_logs (status);

comment on table public.delivery_logs is
  'Cron email send audit: success/fail + Resend id. No holdings or message bodies.';
