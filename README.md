# Exodus Underwriting — Web Application

A web app that ports the v3 Excel underwriting tool to a browser-based interface
your whole team can access. Built with Streamlit. Free to host.

## What it does

Given a property's details, comps (uploaded from your realtor's MLS export),
rehab estimate, and seller-call signals, it recommends the right acquisition
strategy — Wholesale Assignment, Double Close, Rehab, Novation, Short Sale,
MLS Referral, or Pass — with offer terms, target fee/commission, action items,
and rationale. Team can save deals to a searchable history and export Word/PDF memos.

The decision logic is a pure-Python port of the v3 Excel tool. Same inputs
produce the same recommendation across both.

## Project Structure

```
exodus_web/
├── app.py                          # Home page (Streamlit entry point)
├── pages/
│   ├── 1_New_Deal.py              # Main analysis page
│   └── 2_Past_Deals.py            # Searchable deal history
├── modules/
│   ├── strategy.py                # Decision tree (v3 Excel ported to Python)
│   ├── comp_import.py             # MLS export parser
│   ├── memo.py                    # Word + PDF generation
│   ├── db.py                      # SQLite deal history
│   └── auth.py                    # Google OAuth gate
├── .streamlit/
│   ├── config.toml                # Theme + server settings
│   └── secrets.toml.example       # Auth credentials template
├── data/                          # SQLite DB (auto-created, gitignored)
├── requirements.txt
├── .gitignore
├── README.md
└── test_strategy_parity.py        # Validates against 6 known scenarios
```

## Run locally

```bash
# 1. Clone or copy this folder
cd exodus_web

# 2. Install dependencies (Python 3.10+)
pip install -r requirements.txt

# 3. Run
streamlit run app.py
```

The app opens at <http://localhost:8501>. Without `.streamlit/secrets.toml`,
the auth gate is bypassed (dev mode banner shown).

## Deploy to Streamlit Cloud (free)

### Step 1 — Push to GitHub

1. Create a new **private** GitHub repository (any name, e.g. `exodus-underwriting`).
2. From this folder:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/exodus-underwriting.git
   git push -u origin main
   ```

### Step 2 — Deploy on Streamlit Cloud

1. Go to <https://streamlit.io/cloud> and sign in with GitHub.
2. Click **New app** → connect the repo.
3. Set the main file to `app.py` and deploy.
4. You get a URL like `https://exodus-underwriting-XXXXX.streamlit.app`.

### Step 3 — Configure Google OAuth (allow-list auth)

1. In Google Cloud Console (<https://console.cloud.google.com/apis/credentials>),
   create an OAuth 2.0 Client (type: Web application).
2. Set **Authorized redirect URI** to:
   `https://YOUR-APP-URL.streamlit.app/oauth2callback`
3. Save the **Client ID** and **Client Secret**.
4. In Streamlit Cloud, open your app's settings → **Secrets**, and paste:

   ```toml
   [auth]
   redirect_uri = "https://YOUR-APP-URL.streamlit.app/oauth2callback"
   cookie_secret = "<run: python -c \"import secrets; print(secrets.token_hex(32))\">"
   client_id = "YOUR_CLIENT_ID.apps.googleusercontent.com"
   client_secret = "YOUR_CLIENT_SECRET"
   server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"

   [allowed_emails]
   emails = [
       "jo@exoduspropertysolutions.com",
       "teammate@exoduspropertysolutions.com"
   ]
   ```

5. Save. The app redeploys with auth enabled.

### Step 4 — Share with your team

Send team members the app URL. They sign in with Google. If their email is
on the allow-list (in `[allowed_emails]`), they get in. Otherwise they're rejected.

To add or remove a teammate later: edit `[allowed_emails]` in Streamlit Cloud
secrets and save. Takes effect immediately on next sign-in.

## Updating the app

Any time you want to change formulas, thresholds, UI, etc.:

1. Edit the relevant Python file.
2. Push to GitHub.
3. Streamlit Cloud auto-deploys within ~60 seconds.

No team-side action needed — they just see the new version next time they refresh.

## Validation

Run the parity test to confirm the Python logic matches the Excel tool:

```bash
python test_strategy_parity.py
```

This runs the 6 standard scenarios (Wholesale Assignment, Wholesale DC, Rehab,
MLS Referral, Short Sale, Novation) and confirms each produces the expected
strategy with matching numbers.

## Data persistence note

The default setup uses SQLite for deal history. Streamlit Cloud's free tier
does **not** persist disk between deployments — when you push new code, the
local SQLite resets. For permanent history with a team:

- **Quick fix**: After saving important deals, download the Word/PDF memos.
- **Better fix**: Swap `modules/db.py` for a Supabase or Turso backend (15-minute
  setup; the API surface is small, ~6 functions to port).

This won't matter while the app is brand new — the typical pattern is to
deploy → use for a few weeks → migrate to hosted DB once you have meaningful
history worth preserving.

## License

Internal tool for Exodus Property Solutions. Not for public distribution.
