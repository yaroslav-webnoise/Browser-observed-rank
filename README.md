# Browser-Observed Rank Checker

Checks the exact Google organic position of any website by scraping Google through a real Chrome browser — so results match exactly what you see when searching manually.

---

## Requirements

- Windows 10 or 11
- [Python 3.10+](https://www.python.org/downloads/) — during installation, check **"Add Python to PATH"**
- [Google Chrome](https://www.google.com/chrome/) — must be installed (the app uses your Chrome)

---

## Installation

Open PowerShell in the project folder (right-click the folder → "Open in Terminal") and run these commands one at a time:

```powershell
pip install -r requirements.txt
```

```powershell
python -m playwright install chromium
```

---

## Running the App

```powershell
streamlit run app.py
```

The app will open automatically in your browser at `http://localhost:8501`.

---

## How to Use

1. **Select your target market** in the left sidebar:
   - Google Country (e.g. `il` for Israel)
   - Google Language (e.g. `he` for Hebrew)
   - Google Domain (e.g. `google.co.il`)
   - Location (optional city for local results)

2. **Enter the keyword** you want to check rankings for.

3. **Enter the target domain** (e.g. `sportag.co.il`).

4. **Choose match mode:**
   - *Domain* — finds any page from that domain
   - *Exact URL* — matches only the specific page/homepage you entered

5. Click **Check Ranking**.

A Chrome window will briefly open, search Google across up to 10 pages (100 results), then close. The rank is shown in the app.

---

## First Run Note

On the very first search, Google may show a CAPTCHA in the Chrome window. Complete it manually — after that, the session is saved and CAPTCHAs won't appear again (unless you restart your computer or clear the profile).

The browser profile is saved at:
```
C:\Users\<YourName>\.browser_rank_profile
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `pip` not recognized | Re-install Python and check "Add Python to PATH" |
| Chrome window doesn't open | Make sure Google Chrome is installed |
| CAPTCHA appears every time | Don't delete the `.browser_rank_profile` folder |
| `streamlit` not recognized | Run `pip install streamlit` |
| App shows 0 results | Complete the CAPTCHA in the Chrome window that opens |
