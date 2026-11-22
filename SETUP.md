# Setup guide

Everything below is free. Plan for about 40 minutes. Do the steps in order: later steps need values from earlier ones.

| Service | What it does | You'll get |
|---|---|---|
| GitHub | Holds the code so Vercel can deploy it | a private repository |
| Supabase | Database (jobs, sources, settings) | `DATABASE_URL` |
| Gmail | Sends the alert emails | `SMTP_USER`, `SMTP_PASSWORD` |
| Google Sheets | Your job tracker spreadsheet | `SHEETS_WEBHOOK_URL` |
| Vercel | Hosts the dashboard and the checker | your app URL |
| cron-job.org | Runs the checker every minute | — |

Keep a text file open (not in the project folder) to collect the values as you go.

---

## Step 1 — Make three secrets

In PowerShell, run this three times and save each result:

```powershell
python -c "import secrets; print(secrets.token_hex(24))"
```

| Name | Used for |
|---|---|
| `ADMIN_PASSWORD` | Signing in to your dashboard (or choose your own long password) |
| `CRON_SECRET` | Lets cron-job.org trigger the checker |
| `SHEETS_SECRET` | Lets the app write to your Google Sheet |

---

## Step 2 — Put the code on GitHub

1. Sign in at [github.com](https://github.com) → **New repository** → name it `hidden-jobs` → choose **Private** → **Create repository** (don't add a README).
2. In PowerShell:

   ```powershell
   cd "C:\Users\KAUSTUBH\Desktop\17) Hidden Jobs AI Application Platform"
   git init
   git add .
   git commit -m "Hidden Jobs"
   git branch -M main
   git remote add origin https://github.com/YOUR-USERNAME/hidden-jobs.git
   git push -u origin main
   ```

   `.env`, `node_modules` and `dist` are excluded by `.gitignore`, so no secrets are uploaded.

---

## Step 3 — Supabase (database)

1. Sign up at [supabase.com](https://supabase.com) → **New project**.
   - Name: `hidden-jobs`
   - Database password: click **Generate a password** and save it
   - Region: an **East US** region (closest to Vercel's default)
2. Wait about 2 minutes for the project to start.
3. Click **Connect** (top of the project page) → **Connection String** → choose **Transaction pooler**.
4. Copy the URI. It looks like:
   `postgresql://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-us-east-1.pooler.supabase.com:6543/postgres`
5. Replace `[YOUR-PASSWORD]` (including the brackets) with your database password. This is your **`DATABASE_URL`**.

You don't need to create tables. The app creates them on first run.
Free Supabase projects pause after a week without activity; the every-minute checker keeps yours active.

---

## Step 4 — Gmail app password (sending email)

1. Go to [myaccount.google.com/security](https://myaccount.google.com/security) and turn on **2-Step Verification** if it's off.
2. Open [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) → name it `Hidden Jobs` → **Create**.
3. Copy the 16-character password.

You now have:
- **`SMTP_USER`** = your Gmail address
- **`SMTP_PASSWORD`** = the 16-character app password (spaces are fine)

Alerts go to the same address unless you set **`NOTIFY_EMAIL`** (or change it later in the dashboard).

---

## Step 5 — Google Sheet (job tracker)

1. Go to [sheets.new](https://sheets.new) → rename the spreadsheet to `Hidden Jobs`.
2. **Extensions → Apps Script**. A code editor opens.
3. Delete everything in `Code.gs`, then paste the whole content of [`google-sheets/Code.gs`](google-sheets/Code.gs) from this project.
4. On the line `const SECRET = 'PASTE_YOUR_SHEETS_SECRET_HERE';`, put your `SHEETS_SECRET` between the quotes.
5. Click **Save** (disk icon).
6. In the toolbar, pick the function **`setup`** in the dropdown → **Run**.
   - Google asks for permission → **Review permissions** → choose your account.
   - You'll see "Google hasn't verified this app". That's expected for your own script: **Advanced → Go to Hidden Jobs (unsafe) → Allow**.
   - A new **Jobs** tab with headers appears in your spreadsheet.
7. **Deploy → New deployment** → click the gear next to "Select type" → **Web app**:
   - Execute as: **Me**
   - Who has access: **Anyone**
   - **Deploy** → copy the **Web app URL** (ends in `/exec`). This is your **`SHEETS_WEBHOOK_URL`**.

"Anyone" only means the app can reach the script without logging in as you. The script rejects any request without your secret, and it can only add rows to the Jobs tab.

> If you ever edit the script, go to **Deploy → Manage deployments → ✏️ → Version: New version → Deploy**. Otherwise the old code keeps running.

---

## Step 6 — Vercel (hosting)

1. Sign up at [vercel.com](https://vercel.com) **with your GitHub account**.
2. **Add New… → Project** → find `hidden-jobs` → **Import**.
3. Leave the build settings as they are (Vercel detects Vite and the Python API from `vercel.json`).
4. Open **Environment Variables** and add:

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | from Step 3 |
   | `ADMIN_PASSWORD` | from Step 1 |
   | `CRON_SECRET` | from Step 1 |
   | `SMTP_USER` | your Gmail address |
   | `SMTP_PASSWORD` | Gmail app password |
   | `SHEETS_WEBHOOK_URL` | from Step 5 |
   | `SHEETS_SECRET` | from Step 1 (same as in Code.gs) |
   | `NOTIFY_EMAIL` | optional: where alerts go |

5. **Deploy**. After a minute you get a URL like `https://hidden-jobs-xyz.vercel.app`.
6. Optional: add **`APP_URL`** = that URL (Settings → Environment Variables) so emails link to your dashboard, then **Deployments → ⋯ → Redeploy**.

> Any time you add or change an environment variable, redeploy for it to take effect.

---

## Step 7 — First sign-in and tests

1. Open your Vercel URL and sign in with `ADMIN_PASSWORD`.
2. **Settings → Send test email.** It should arrive within a minute (check spam the first time and mark it "Not spam").
3. **Settings → Google Sheet → Test connection.** It should say "Connected". An **Open sheet ↗** button appears in the header.
4. Click **Check now** (top right). The first run sets up your default feeds and finds the first batch of companies. You may get a first email with jobs posted in the last 48 hours, and the same jobs appear in your sheet.

---

## Step 8 — cron-job.org (check every minute)

1. Sign up at [cron-job.org](https://cron-job.org) → **Cronjobs → Create cronjob**.
2. Fill in:
   - Title: `Hidden Jobs`
   - URL: `https://YOUR-APP.vercel.app/api/cron?key=YOUR_CRON_SECRET`
   - Execution schedule: **Every minute**
3. **Create**.

Within a minute, the dashboard's status pill turns green ("Watching · cron 20s ago") and the setup checklist disappears.
Vercel also calls the checker once a day by itself as a backup.

**You're done.** New matching jobs arrive by email and are added to your sheet.

---

## Troubleshooting

| What you see | Fix |
|---|---|
| Dashboard says "Can't reach the backend" | `DATABASE_URL` is wrong or missing. Use the **Transaction pooler** string (port 6543) with your real password, then redeploy. |
| "Wrong password" when signing in | `ADMIN_PASSWORD` in Vercel differs from what you typed, or you didn't redeploy after setting it. |
| Test email fails with "Username and Password not accepted" | Use the Gmail **app password**, not your normal password, and make sure 2-Step Verification is on. |
| Sheet test: "Wrong secret" | `SECRET` in Code.gs must exactly match `SHEETS_SECRET` in Vercel. After editing Code.gs, deploy a **New version**. |
| Sheet test: "Google didn't return JSON" | The deployment must be type **Web app**, Execute as **Me**, Who has access **Anyone**, and the URL must end in `/exec`. |
| Status pill says "Cron not set up yet" or "stalled" | Check the cron-job.org URL (domain and `key=`). Its history shows the response: 401 = wrong key. |
| A LinkedIn source shows "HTTP 429" | LinkedIn is limiting requests. It recovers on its own; company boards are unaffected. |
| An auto-discovered company shows errors | Boards that fail 5 times in a row are paused automatically. You can delete them. |

## Later

- **Change what you look for:** dashboard → Settings (roles, locations, visa mode, blocked companies).
- **Watch a specific company:** Sources → type its name or paste its careers URL.
- **Update the code:** commit and `git push`; Vercel redeploys automatically.
- **Refresh H-1B data yearly:** see "Refreshing H-1B data" in the README.
