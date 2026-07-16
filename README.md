# Job Mailer

An AI-powered tool that generates and sends personalized job-application emails
with your CV attached. It uses an OpenAI-compatible API (Groq by default) to
write the email body, Gmail SMTP to send it, and Supabase to store config, the
CV, and a history of sent emails.

## Features

- Generate a tailored application email from your CV for any role
- Send the email via Gmail SMTP with your CV attached
- Prevent duplicate sends (tracks recipients in Supabase)
- Web UI (`frontend/index.html`) to configure, generate, preview, and send
- Local (FastAPI + `config.json`) and serverless (Vercel + Supabase) modes

## Prerequisites

- Python 3.10+
- A Gmail account with an **App Password** (not your normal password)
- A Groq API key (or any OpenAI-compatible endpoint)
- A Supabase project (URL + service key) — required for the send/history features
  and for Vercel deployment

## Project layout

```
main.py          # Local FastAPI app (config stored in config.json)
vercel_app.py    # Vercel FastAPI app (config stored in Supabase)
config.json       # Local non-secret config (name, role, model)
frontend/         # Static web UI served at /
.env              # Local secrets (git-ignored)
vercel.json       # Vercel build/routes config
requirements.txt  # Python dependencies
```

## 1. Install dependencies

```bash
python -m venv env
env\Scripts\activate      # Windows
# source env/bin/activate  # macOS/Linux

pip install -r requirements.txt
```

## 2. Configure local environment

Create a `.env` file in the project root (copy the keys you need):

```env
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx   # Gmail App Password
GROQ_API_KEY=gsk_xxx                      # OpenAI-compatible API key
SUPABASE_URL=https://YOURPROJECT.supabase.co
SUPABASE_KEY=eyJ...                       # service_role key
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_MODEL=llama-3.3-70b-versatile
```

Edit `config.json` to set your name and target role:

```json
{
  "your_name": "Your Name",
  "job_role": "Software Engineer",
  "auto_send": false,
  "openai_base_url": "https://api.groq.com/openai/v1",
  "openai_model": "llama-3.3-70b-versatile"
}
```

## 3. Set up Supabase

In the Supabase SQL editor, create the tables used by the app:

```sql
create table app_config (
  id              int primary key,
  your_name       text,
  job_role        text,
  auto_send       boolean,
  openai_base_url text,
  openai_model    text,
  cv_filename     text,
  cv_storage_path text
);
insert into app_config (id) values (1);

create table sent_emails (
  id        int generated always as identity primary key,
  email     text unique,
  subject   text,
  body      text,
  created_at timestamptz default now()
);
```

Create a Storage bucket named `cvs` (public or private — the app downloads via
the service key). The `app_config` access is also via the service role key, so
keep `SUPABASE_KEY` secret.

## 4. Run locally

```bash
uvicorn main:app --reload --port 8000
```

Open <http://localhost:8000> and:

1. Open the config panel and save your name/role.
2. Upload your CV (`uploaded_cv.pdf` is used if already present in the root).
3. Enter a recipient email, click **Generate** to draft, then **Send**.

## 5. Deploy to Vercel

The serverless entry point is `vercel_app.py` (config + CV live in Supabase, so
no local `config.json` or filesystem is used).

1. Set the same environment variables from step 2 in the Vercel project
   settings (Environment Variables).
2. Deploy:

```bash
vercel --prod
```

`vercel.json` routes all requests to `vercel_app.py` via the `@vercel/python`
builder.

## Notes

- Secrets are never returned by `/api/config`; only masked previews are.
- `config.json` and `.env` are git-ignored — never commit real credentials.
- Gmail requires an App Password: Google Account → Security → 2-Step Verification
  → App Passwords.
