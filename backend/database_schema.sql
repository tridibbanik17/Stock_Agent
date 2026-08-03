-- Stock Agent — Supabase / PostgreSQL schema
-- Privacy contract: ONLY delivery-eligible fields.
-- NEVER store shares, buy prices, portfolio balances, or Gemini API keys.

create extension if not exists "pgcrypto";

create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),

  -- Contact + delivery identity
  email text not null,

  -- Plain ticker symbols only (e.g. {"NVDA","AAPL","SHOP.TO"})
  watchlist text[] not null default '{}'::text[],

  -- daily | weekdays | weekly | custom
  schedule_frequency text not null default 'weekly',

  -- Multi-send times from the extension, 24h "HH:MM" strings
  preferred_hours text[] not null default '{09:00}'::text[],

  -- JS getDay() ints: 0=Sun … 6=Sat (required for weekly/custom)
  preferred_days smallint[] not null default '{6}'::smallint[],

  -- IANA timezone captured from the browser (e.g. America/New_York)
  timezone text not null default 'UTC',

  -- Soft disable without deleting the row
  enabled boolean not null default true,

  -- One-click unsubscribe (opaque; never email-guessable)
  unsubscribe_token uuid not null default gen_random_uuid(),

  -- Ownership proof required to update watchlist/schedule (returned to extension)
  manage_token uuid not null default gen_random_uuid(),

  -- Short-lived email recovery (reclaim manage_token if extension storage is lost)
  recover_token uuid,
  recover_token_expires_at timestamptz,

  -- Cron send dedupe: set after a successful email for a preferred-hour slot
  last_sent_at timestamptz,

  -- Optional: prefer digests when grades flip (still sends a short no-change note)
  email_on_grade_change_only boolean not null default false,

  -- Last emailed grade per ticker {"NVDA":"STRONG_BUY", ...}
  last_grades jsonb not null default '{}'::jsonb,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint users_email_unique unique (email),
  constraint users_unsubscribe_token_unique unique (unsubscribe_token),
  constraint users_manage_token_unique unique (manage_token),
  constraint users_email_format check (position('@' in email) > 1),
  constraint users_watchlist_cap check (cardinality(watchlist) <= 25),
  constraint users_hours_cap check (cardinality(preferred_hours) <= 8),
  constraint users_frequency_check check (
    schedule_frequency in ('daily', 'weekdays', 'weekly', 'custom')
  )
);

create index if not exists users_enabled_idx
  on public.users (enabled)
  where enabled = true;

create index if not exists users_schedule_idx
  on public.users (schedule_frequency, enabled);

-- Keep updated_at fresh on every upsert/update
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists users_set_updated_at on public.users;
create trigger users_set_updated_at
  before update on public.users
  for each row
  execute function public.set_updated_at();

comment on table public.users is
  'Delivery profiles only. No holdings, buy prices, or Gemini keys.';

comment on column public.users.watchlist is
  'Ticker symbols synchronized from the extension (max 25).';

comment on column public.users.preferred_hours is
  'Local send times HH:MM; at most 2 per day (× days ≤ 14 emails/week).';

comment on column public.users.last_sent_at is
  'UTC timestamp of last successful cron email; used to skip duplicate sends in the same preferred-hour window.';

comment on column public.users.unsubscribe_token is
  'Opaque token for one-click email unsubscribe (GET/POST/DELETE /api/unsubscribe).';

comment on column public.users.manage_token is
  'Opaque ownership token; required on POST /api/subscribe updates. Stored in the extension.';

comment on column public.users.recover_token is
  'One-time email recovery token to rotate manage_token if the extension lost it.';

comment on column public.users.email_on_grade_change_only is
  'When true, cron emails a full report on grade flips, otherwise a short no-change digest.';

comment on column public.users.last_grades is
  'Last emailed grade per ticker, e.g. {"NVDA":"STRONG_BUY"}. Used for grade-change digests.';

-- Cron / Resend delivery audit (no holdings, no email body)
create table if not exists public.delivery_logs (
  id uuid primary key default gen_random_uuid(),

  user_id uuid references public.users (id) on delete set null,

  -- Denormalized for easy filtering if the user row is later removed
  email text not null,

  -- success | failure | dry_run
  status text not null,

  -- Resend message id when provider accepted the send
  resend_id text,

  subject text,
  ticker_count integer,

  -- Truncated provider / exception detail on failure
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
