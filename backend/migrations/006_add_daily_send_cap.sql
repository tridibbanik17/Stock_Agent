-- Cap successful report emails per user per local calendar day (abuse: edit times after send).
-- Run once in Supabase SQL Editor after 004.

alter table public.users
  add column if not exists daily_send_count integer not null default 0;

alter table public.users
  add column if not exists daily_send_on date;

comment on column public.users.daily_send_count is
  'Successful report emails sent on daily_send_on (user local calendar day).';

comment on column public.users.daily_send_on is
  'Local calendar date (YYYY-MM-DD in user timezone) that daily_send_count applies to.';
