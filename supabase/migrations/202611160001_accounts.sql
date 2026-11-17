-- Apply with Supabase migrations. No service-role key is required by clients.
create table public.account_profiles (
    user_id uuid primary key default auth.uid() references auth.users(id) on delete cascade,
    data jsonb not null default '{}'::jsonb check (jsonb_typeof(data) = 'object'),
    revision bigint not null default 1 check (revision > 0),
    constraint account_resume_owned check (
        not (data ? 'resume_object') or data->>'resume_object' is null
        or data->>'resume_object' = ''
        or (
            split_part(data->>'resume_object', '/', 1) = user_id::text
            and data->>'resume_object' ~ '^[0-9a-f-]{36}/[0-9a-f-]{36}\.pdf$'
        )
    )
);

create table public.account_answers (
    user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
    key text not null check (length(key) between 1 and 200),
    value text not null check (length(value) <= 20000),
    always_use boolean not null default false,
    label text not null default '' check (length(label) <= 2000),
    primary key (user_id, key)
);

create table public.account_runs (
    user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
    id uuid not null,
    url text not null check (length(url) <= 8192 and url ~ '^https?://'),
    status text not null check (status in ('started', 'review', 'blocked', 'stopped', 'error')),
    unresolved jsonb not null default '[]'::jsonb check (jsonb_typeof(unresolved) = 'array'),
    created_at timestamptz not null default now(),
    primary key (user_id, id)
);
create index account_runs_history on public.account_runs (user_id, created_at desc);

alter table public.account_profiles enable row level security;
alter table public.account_answers enable row level security;
alter table public.account_runs enable row level security;

create policy account_profiles_owner on public.account_profiles
    for all to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);
create policy account_answers_owner on public.account_answers
    for all to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);
create policy account_runs_owner on public.account_runs
    for all to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

revoke all on public.account_profiles, public.account_answers, public.account_runs from public, anon, authenticated;
grant select, delete on public.account_profiles to authenticated;
-- Revision is DB-owned; even direct writes cannot choose or reset it.
grant insert (user_id, data), update (data) on public.account_profiles to authenticated;
grant select, insert, update, delete on public.account_answers, public.account_runs to authenticated;

create function public.bump_account_profile_revision()
returns trigger language plpgsql set search_path = '' as $$
begin
    new.revision := old.revision + 1;
    return new;
end;
$$;
revoke all on function public.bump_account_profile_revision() from public, anon, authenticated;
create trigger account_profile_revision before update on public.account_profiles
    for each row execute function public.bump_account_profile_revision();

-- Expected revision 0 creates the initial profile. Stale writes raise a conflict;
-- the caller must retain its edits and fetch the new version before retrying.
create function public.save_account_profile(expected_revision bigint, data jsonb)
returns public.account_profiles
language plpgsql security invoker set search_path = '' as $$
declare
    saved public.account_profiles;
    subject uuid := auth.uid();
begin
    if subject is null then
        raise exception 'Authentication required' using errcode = '42501';
    end if;
    if expected_revision is null or expected_revision < 0 then
        raise exception 'Invalid expected revision' using errcode = '22023';
    end if;
    if data is null or jsonb_typeof(data) <> 'object' then
        raise exception 'Profile must be an object' using errcode = '22023';
    end if;
    if expected_revision = 0 then
        insert into public.account_profiles(user_id, data)
        values (subject, data)
        on conflict (user_id) do nothing
        returning * into saved;
    else
        update public.account_profiles p
        set data = save_account_profile.data
        where p.user_id = subject and p.revision = expected_revision
        returning p.* into saved;
    end if;
    if saved.user_id is null then
        raise exception 'Profile changed; reload before saving' using errcode = 'PT409';
    end if;
    return saved;
end;
$$;
revoke all on function public.save_account_profile(bigint, jsonb) from public, anon;
grant execute on function public.save_account_profile(bigint, jsonb) to authenticated;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('resumes', 'resumes', false, 10485760, array['application/pdf'])
on conflict (id) do update set
    public = false, file_size_limit = 10485760, allowed_mime_types = array['application/pdf'];

-- Immutable object names avoid update races. Replacing a resume uploads a fresh
-- UUID object, then changes the versioned profile pointer. No UPDATE policy.
create policy account_resume_read on storage.objects for select to authenticated
using (bucket_id = 'resumes' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy account_resume_insert on storage.objects for insert to authenticated
with check (
    bucket_id = 'resumes'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and name ~ '^[0-9a-f-]{36}/[0-9a-f-]{36}\.pdf$'
);
create policy account_resume_delete on storage.objects for delete to authenticated
using (bucket_id = 'resumes' and (storage.foldername(name))[1] = (select auth.uid())::text);
