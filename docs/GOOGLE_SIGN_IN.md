# Google sign-in setup (Supabase Free plan)

The local app (`pocket-agent ui`) and `pocket-agent login` sign in with Google OAuth and PKCE. Both store the same session in the macOS Keychain, so you sign in once per computer. You need no SMTP server, confirmation email, app password, service-role key, or paid integration. Supabase's Free-plan limits on Auth, the database, and Storage still apply.

## 1. Create a Google OAuth client

In [Google Cloud Console](https://console.cloud.google.com/), select or create your project. Open **Google Auth Platform** (or **APIs & Services → OAuth consent screen**).

- Set the app name (Pocket Agent), support email, and developer contact email.
- Choose **External** audience if people outside a Google Workspace organization will use it.
- While the app is in **Testing**, add your Google email under **Test users**. Until you publish the app, every other user must also be added as a tester.
- Create an **OAuth client ID**, application type **Web application**.
- Under **Authorized redirect URIs**, add exactly:

```text
https://itwafyswshzdcrsyieay.supabase.co/auth/v1/callback
```

If you use another Supabase project, put that project's callback here instead. Google redirects to Supabase, not to the app. This is the redirect-based OAuth flow, so you can leave Google's Authorized JavaScript origins empty. The app requests only the OpenID, email, and profile scopes, and no Drive, Gmail, or Calendar access.

Copy the Google **Client ID** and **Client secret** into the Supabase provider settings in the next step. Do not put the client secret in `.env`, the app, or the public project key field.

## 2. Enable Google in Supabase

Open [this project's Google provider settings](https://supabase.com/dashboard/project/itwafyswshzdcrsyieay/auth/providers).

Under **Authentication → Sign In / Providers → Google**:

1. Enable Google.
2. Enter the Google Client ID and Client secret.
3. Save.

Keep new-user sign-ups enabled. Google sign-in does not use SMTP or email templates. Keep **Confirm email** on. Google already verifies the email address. With confirmation on, nobody can register an unverified password account under someone else's email that Supabase would later link to that person's Google sign-in. Pocket Agent offers no password or email-link sign-in.

## 3. Allow the two return URLs

Under **Authentication → URL Configuration**, set:

**Site URL**

```text
http://127.0.0.1:8000
```

**Redirect URLs** (add both)

```text
http://127.0.0.1:8000/auth/callback**
http://127.0.0.1:8766/auth/callback**
```

The first URL is the local app and the second is `pocket-agent login` in the terminal. The trailing wildcard allows the random `pa_state` query parameter the app adds. Open the app at `127.0.0.1`, not `localhost`, unless you also allow the matching `localhost` URL. Keep the app on port 8000, or add the callback for the port you choose.

## 4. Start the app and sign in

```sh
uv run --env-file .env pocket-agent ui
```

A tab opens at `http://127.0.0.1:8000`. Click **Continue with Google** and stay in the same tab until sign-in finishes. After you choose your Google account, the app returns to your workspace. Reloading the tab keeps you signed in. If an account with the same verified email already exists, Supabase may link the Google sign-in to it. If linking fails, check the Auth logs. Do not delete the old account or create a second one to work around it.

The terminal shares this sign-in, so these commands need no second Google prompt:

```sh
uv run --env-file .env pocket-agent whoami
uv run --env-file .env pocket-agent fill 'https://YOUR_JOB_URL' --account --model none
```

`pocket-agent login` signs in from the terminal instead. It waits up to three minutes for its callback on port 8766, which must be free. **Sign out** in the app and `pocket-agent logout` both end the shared session.

## Checks and limits

The Python tests cover provider setup errors, the authorization URL parameters, code exchange, the standard PKCE test vector, a real local callback that rejects a wrong state, and Keychain storage. The app tests cover storing the refresh token in Keychain without returning it to the browser, restoring the session after a reload, and sign-out clearing Keychain. The JavaScript tests cover per-tab PKCE generation, callback state and expiry, removing the code from the address bar, and loading the workspace after sign-in. None of them prove your Google Console and Supabase settings are correct. Complete one real Google sign-in after setup.

Sources: [Supabase Google sign-in](https://supabase.com/docs/guides/auth/social-login/auth-google), [PKCE](https://supabase.com/docs/guides/auth/sessions/pkce-flow), [redirect allowlists](https://supabase.com/docs/guides/auth/redirect-urls).
