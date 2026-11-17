# Account database

Apply `migrations/202611160001_accounts.sql` with the Supabase CLI or SQL editor to a dedicated project. Clients use the project's public/publishable key plus the user's access token; no service-role key belongs in the portal or runner.

The migration creates three tenant-isolated tables and a private `resumes` bucket. `save_account_profile(expected_revision, data)` returns the saved profile row. Pass `0` for first creation and the previously read `revision` for an update. SQLSTATE `40001` means a concurrent edit; preserve the user's draft, reload, and ask them to reconcile. Database-owned revisions increment on every profile update, including direct PostgREST writes. Portal writes should always use the RPC to obtain optimistic concurrency.

An optional `data.resume_object` must point into the user's own folder, in the format `user-uuid/object-uuid.pdf`. Upload a new immutable object before saving the pointer; never use Storage upsert. Bucket restrictions are 10 MiB and MIME type `application/pdf`; application code must also inspect uploaded PDF bytes. Signed-in owners can read, insert and delete their resume objects. No object UPDATE policy is created.

Run the actual PostgreSQL isolation checks against a **disposable local Supabase database** after applying migrations:

```sh
psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f supabase/tests/accounts_rls.sql
```

The test uses real `authenticated` and `anon` roles, two distinct user IDs, profile conflicts, foreign ownership attempts, and Storage metadata policies. It creates fixtures inside a rolled-back transaction. It requires the privileged local database connection to insert auth fixtures and switch roles. Run additional Storage HTTP integration checks for PDF MIME/size enforcement and upload/download/delete behavior: SQL metadata checks do not exercise the Storage service or object bytes.

No database integration result is implied by these files. The development environment used to write them had no `psql` or Supabase CLI and no running Docker daemon, so the integration script was not executed there.

Use a dedicated project or audit existing permissive policies before applying this migration: PostgreSQL permissive policies combine with OR, so an unrelated broad policy on `storage.objects` can weaken this bucket's restrictions. Supabase documents [Storage access control](https://supabase.com/docs/guides/storage/security/access-control) and [bucket restrictions](https://supabase.com/docs/guides/storage/buckets/fundamentals).
