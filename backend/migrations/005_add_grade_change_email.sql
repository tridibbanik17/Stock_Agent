-- Existing Supabase DBs: run once in the SQL Editor.
-- HTML cron reports + optional "email when a grade flips" mode.

alter table public.users
  add column if not exists email_on_grade_change_only boolean not null default false;

alter table public.users
  add column if not exists last_grades jsonb not null default '{}'::jsonb;

comment on column public.users.email_on_grade_change_only is
  'When true, cron still emails on schedule: full report if any grade flipped, otherwise a short no-change digest.';

comment on column public.users.last_grades is
  'Last emailed grade per ticker, e.g. {"NVDA":"STRONG_BUY"}. Used for grade-change digests.';
