-- ============================================================================
-- ALPHA BRAIN — Changelog + Analysis Tables
-- Run in Supabase SQL Editor (idempotent)
-- ============================================================================

-- 1. CHANGELOG — every GPFC, bugfix, param tweak
create table if not exists public.changelog (
    id              bigint generated always as identity primary key,
    created_at      timestamptz not null default now(),
    deployed_at     timestamptz,
    change_type     text not null default 'gpfc'
                    check (change_type in ('gpfc','param_change','bugfix','feature','revert','strategy')),
    title           text not null,
    description     text,
    version         text,
    parameters_before  jsonb,
    parameters_after   jsonb,
    status          text not null default 'deployed'
                    check (status in ('pending', 'deployed', 'reverted')),
    git_commit_hash text,
    tags            text[]
);

create index if not exists idx_changelog_deployed_at
    on public.changelog (deployed_at desc) where deployed_at is not null;
create index if not exists idx_changelog_created_at
    on public.changelog (created_at desc);

-- 2. ALPHA_ANALYSIS — Claude analysis results
create table if not exists public.alpha_analysis (
    id              bigint generated always as identity primary key,
    created_at      timestamptz not null default now(),
    changelog_entry_id  bigint references public.changelog(id),
    analysis_type   text not null default 'general'
                    check (analysis_type in ('general','changelog_impact','pair_review','strategy_review')),
    prompt_context  jsonb,
    model_used      text not null,
    analysis_text   text not null,
    summary         text,
    recommendations jsonb,
    input_tokens    integer,
    output_tokens   integer,
    triggered_by    text not null default 'manual'
);

create index if not exists idx_alpha_analysis_created_at
    on public.alpha_analysis (created_at desc);
create index if not exists idx_alpha_analysis_changelog
    on public.alpha_analysis (changelog_entry_id) where changelog_entry_id is not null;

-- RLS
alter table public.changelog      enable row level security;
alter table public.alpha_analysis enable row level security;

do $$ begin
    create policy "Allow read changelog" on public.changelog
        for select to anon, authenticated using (true);
exception when duplicate_object then null; end $$;

do $$ begin
    create policy "Allow insert changelog" on public.changelog
        for insert to anon, authenticated with check (true);
exception when duplicate_object then null; end $$;

do $$ begin
    create policy "Allow update changelog" on public.changelog
        for update to anon, authenticated using (true);
exception when duplicate_object then null; end $$;

do $$ begin
    create policy "Allow read alpha_analysis" on public.alpha_analysis
        for select to anon, authenticated using (true);
exception when duplicate_object then null; end $$;

do $$ begin
    create policy "Allow insert alpha_analysis" on public.alpha_analysis
        for insert to anon, authenticated with check (true);
exception when duplicate_object then null; end $$;

-- Realtime
alter publication supabase_realtime add table public.changelog;
alter publication supabase_realtime add table public.alpha_analysis;
