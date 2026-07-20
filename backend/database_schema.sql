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

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint users_email_unique unique (email),
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
  'Local send times HH:MM; multiple values = multi-send per day.';
