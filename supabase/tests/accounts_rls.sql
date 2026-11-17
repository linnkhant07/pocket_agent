-- Run against a disposable local Supabase DB, after applying migrations:
-- psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f supabase/tests/accounts_rls.sql
-- Real PostgreSQL roles and RLS, no mocks. All fixtures roll back.
begin;
insert into auth.users (id) values
    ('11111111-1111-4111-8111-111111111111'),
    ('22222222-2222-4222-8222-222222222222');

set local role authenticated;
select set_config('request.jwt.claim.sub', '11111111-1111-4111-8111-111111111111', true);
select public.save_account_profile(0, '{"name":"User A"}'::jsonb);
insert into public.account_answers (key,value,always_use) values ('gender','Decline',true);
insert into public.account_runs (id,url,status) values
    ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','https://example.test/job','review');
insert into storage.objects (bucket_id,name) values
    ('resumes','11111111-1111-4111-8111-111111111111/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.pdf');

do $$
declare changed integer;
begin
    if (select revision from public.account_profiles) <> 1 then
        raise exception 'Initial profile revision is not 1';
    end if;
    perform public.save_account_profile(1, '{"name":"User A edited"}'::jsonb);
    if (select revision from public.account_profiles) <> 2 then
        raise exception 'Profile revision was not incremented';
    end if;
    begin
        perform public.save_account_profile(1, '{"name":"Stale"}'::jsonb);
        raise exception 'Stale profile update succeeded';
    exception when serialization_failure then null;
    end;
    begin
        perform public.save_account_profile(0, '{}'::jsonb);
        raise exception 'Repeated create succeeded';
    exception when serialization_failure then null;
    end;
    begin
        update public.account_profiles set revision = 1;
        raise exception 'Client changed DB-owned revision';
    exception when insufficient_privilege then null;
    end;
    begin
        perform public.save_account_profile(2,
            '{"resume_object":"22222222-2222-4222-8222-222222222222/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.pdf"}'::jsonb);
        raise exception 'Foreign resume pointer accepted';
    exception when check_violation then null;
    end;
    update storage.objects set name = '11111111-1111-4111-8111-111111111111/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.pdf'
      where bucket_id = 'resumes';
    get diagnostics changed = row_count;
    if changed <> 0 then raise exception 'Immutable resume object updated'; end if;
end;
$$;

-- A different authenticated account must see no A-owned rows or objects.
select set_config('request.jwt.claim.sub', '22222222-2222-4222-8222-222222222222', true);
do $$
declare changed integer;
begin
    if exists(select from public.account_profiles) or exists(select from public.account_answers)
       or exists(select from public.account_runs)
       or exists(select from storage.objects where bucket_id = 'resumes') then
        raise exception 'User B can read user A data';
    end if;
    update public.account_profiles set data = '{}'::jsonb;
    get diagnostics changed = row_count;
    if changed <> 0 then raise exception 'User B changed user A profile'; end if;
    update public.account_answers set value = 'overwrite';
    get diagnostics changed = row_count;
    if changed <> 0 then raise exception 'User B changed user A answer'; end if;
    update public.account_runs set status = 'stopped';
    get diagnostics changed = row_count;
    if changed <> 0 then raise exception 'User B changed user A run'; end if;
    delete from public.account_profiles;
    get diagnostics changed = row_count;
    if changed <> 0 then raise exception 'User B deleted user A profile'; end if;
    delete from public.account_answers;
    get diagnostics changed = row_count;
    if changed <> 0 then raise exception 'User B deleted user A answer'; end if;
    delete from public.account_runs;
    get diagnostics changed = row_count;
    if changed <> 0 then raise exception 'User B deleted user A run'; end if;
    begin
        insert into public.account_profiles (user_id,data)
        values ('11111111-1111-4111-8111-111111111111','{}');
        raise exception 'Forged profile owner accepted';
    exception when insufficient_privilege then null;
    end;
    begin
        insert into public.account_answers (user_id,key,value)
        values ('11111111-1111-4111-8111-111111111111','forged','bad');
        raise exception 'Forged answer owner accepted';
    exception when insufficient_privilege then null;
    end;
    begin
        insert into public.account_runs (user_id,id,url,status)
        values ('11111111-1111-4111-8111-111111111111','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb','https://example.test','started');
        raise exception 'Forged run owner accepted';
    exception when insufficient_privilege then null;
    end;
    begin
        insert into storage.objects (bucket_id,name) values
        ('resumes','11111111-1111-4111-8111-111111111111/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.pdf');
        raise exception 'Foreign folder upload accepted';
    exception when insufficient_privilege then null;
    end;
end;
$$;
select public.save_account_profile(0, '{"name":"User B"}'::jsonb);
insert into public.account_answers (key,value) values ('phone','222');
insert into public.account_runs (id,url,status) values
    ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb','https://example.test/job-b','started');

-- Switching back confirms B's attempted updates/deletes had no side effects.
select set_config('request.jwt.claim.sub', '11111111-1111-4111-8111-111111111111', true);
do $$
begin
    if (select data->>'name' from public.account_profiles) <> 'User A edited'
       or (select value from public.account_answers where key='gender') <> 'Decline'
       or (select status from public.account_runs) <> 'review' then
        raise exception 'User A data changed across tenant boundary';
    end if;
end;
$$;

set local role anon;
select set_config('request.jwt.claim.sub', '', true);
do $$
begin
    begin
        perform * from public.account_profiles;
        raise exception 'Anonymous profile access accepted';
    exception when insufficient_privilege then null;
    end;
    begin
        perform * from public.account_answers;
        raise exception 'Anonymous answer access accepted';
    exception when insufficient_privilege then null;
    end;
    begin
        perform * from public.account_runs;
        raise exception 'Anonymous run access accepted';
    exception when insufficient_privilege then null;
    end;
    begin
        perform public.save_account_profile(0,'{}');
        raise exception 'Anonymous profile RPC accepted';
    exception when insufficient_privilege then null;
    end;
    if exists(select from storage.objects where bucket_id='resumes') then
        raise exception 'Anonymous resume read accepted';
    end if;
end;
$$;
rollback;
