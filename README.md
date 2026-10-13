# 📊 Business Development Dashboard | AIESEC in BINUS

An interactive **Business Development Dashboard** developed to transform partnership data into accessible, structured, and actionable insights.

The dashboard was built as an internal monitoring tool for **AIESEC in BINUS**, helping Business Development members monitor partnership performance, document completion, partnership periods, and other key metrics from a centralized interface.

🔗 **Live Dashboard:** https://bit.ly/BD2627_Dashboard

---

## 📌 Overview

Partnership data is often distributed across multiple spreadsheets, making it difficult to quickly monitor performance, document completion, and partnership progress.

This project transforms existing Business Development data into an interactive dashboard that allows users to explore key metrics and monitor partnership activities more efficiently.

The dashboard integrates with **Google Sheets** as its data source and provides interactive visualizations using **Streamlit** and **Plotly**.

---

## ✨ Key Features

### 📈 Market Research Performance

Tracks the number of market research activities conducted by each member.

Users can filter the data by specific periods to analyze individual and overall performance.

### 📊 Partnership Conversion Rate

Displays partnership conversion metrics based on the selected reporting period, allowing members to quickly monitor Business Development performance.

### 📁 Document Tracker

Provides centralized monitoring for partnership-related documents and their completion status.

The tracker helps identify documents that have been completed and those that still require follow-up.

### 🤝 Partnership Completion Monitoring

Tracks partnerships that have reached the end of their collaboration period and monitors post-partnership requirements such as:

- Partnership Reports
- Partnership Surveys
- Other required partnership documentation

### ⏳ Partnership Period Monitoring

Highlights partnerships approaching their end date, helping members identify collaborations that may require follow-up or renewal actions.

### 🔎 Interactive Filtering

Users can dynamically filter dashboard information based on available reporting periods and other relevant parameters.

---

## 🔐 Data Privacy & Access

This dashboard contains two access modes:

### Member Mode

Authorized members can access the dashboard using authentication and view actual organizational partnership data.

### Public Mode

Public visitors can explore the dashboard using **dummy data** designed to demonstrate the dashboard's functionality without exposing confidential organizational information.

> **Note:** Actual partnership data, credentials, Google Sheets URLs, and other sensitive organizational information are not included in this repository.

---

## 🛠️ Tech Stack

- **Python** — Core programming language
- **Streamlit** — Dashboard application framework
- **Plotly** — Interactive data visualization
- **Pandas** — Data processing and transformation
- **Google Sheets API** — Dynamic data source integration
- **Streamlit Secrets** — Secure credential management
- **Git & GitHub** — Version control
- **Streamlit Community Cloud** — Application deployment

---

## 🏗️ Project Structure

```text
bd-dashboard/
│
├── app.py
├── requirements.txt
├── README.md
│
├── src/
│   ├── charts.py
│   ├── metrics.py
│   ├── periods.py
│   ├── preparation.py
│   └── sheets.py
│
├── data/
│   └── sample/
│
├── notebooks/
│   └── exploration.ipynb
│
└── .streamlit/
    └── config.toml
```

### Main Components

**`app.py`**  
Main Streamlit application responsible for the dashboard interface, authentication flow, filters, and page rendering.

**`src/sheets.py`**  
Handles data extraction and Google Sheets integration.

**`src/preparation.py`**  
Processes and prepares raw data before analysis.

**`src/metrics.py`**  
Contains calculations for dashboard metrics and KPIs.

**`src/charts.py`**  
Handles Plotly chart generation and visualization logic.

**`src/periods.py`**  
Manages reporting period logic and filtering.

---

## 🚀 Running the Project Locally

### 1. Clone the repository

```bash
git clone YOUR_GITHUB_REPOSITORY_URL
cd bd-dashboard
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate the environment on Windows:

```bash
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure secrets

Create:

```text
.streamlit/secrets.toml
```

Add the required credentials and environment variables.

Example:

```toml
MEMBER_PASSWORD = "your-password"
```

Google Service Account credentials should also be configured securely through Streamlit Secrets.

S&D manual entries require a **separate writable Google Sheet** on Streamlit
Community Cloud. Three things are required, and all three are easy to miss:

1. **Enable the Google Sheets API** in the service account's Google Cloud project
   (<https://console.cloud.google.com/apis/library/sheets.googleapis.com>).
   Drive API access alone is not enough: every Sheets call returns
   `403 SERVICE_DISABLED` while it is off, no matter how the Sheet is shared.
2. Share that Sheet with the service account as **Editor**.
3. Configure these top-level secrets:

```toml
SND_STORAGE_BACKEND = "google_sheets"
SND_CONFIG_SHEET_ID = "your-dedicated-spreadsheet-id"
SND_CUTOVER_ID = "verified-id-returned-by-the-cutover-command"
```

Verify all of it at any time with a strictly read-only check that writes nothing
and prints no credentials:

```bash
python -m src.snd_backup preflight --sheet-id "<spreadsheet ID or URL>"
```

It exits non-zero and names the single blocking action until storage is ready.

Drive proof access alone does not persist manual entries. Missing S&D storage
configuration or a verified migration receipt disables saving instead of creating
a temporary or empty production store. The one-time `python -m src.snd_backup cutover
... --apply` initializes a completely empty spreadsheet with an `SND_Config` worksheet
and the headers `partner_key`, `payload_json`, and `updated_at` (other worksheets are
not changed), then returns the `SND_CUTOVER_ID`. `SND_CONFIG_SHEET_ID` accepts the
spreadsheet ID or its full URL (including `?gid=`/`#gid=` and `/u/0/` links); a
published `/d/e/.../pubhtml` link is rejected because that token is not a
spreadsheet ID.
For local development only, explicitly select `sqlite` and set an absolute
`SND_DB_PATH` on a persistent disk, with `SND_CONFIG_SHEET_ID` unset.

See [the S&D incident report and recovery instructions](docs/snd-data-loss-incident.md)
before restarting an existing deployment. The member-only **All saved requirements**
panel still lists inactive and renamed partners. The operational S&D Tracker page
deliberately exposes no Legacy Tracker or Backup button; snapshots, backups, and the
legacy rescue reader stay available through `python -m src.snd_backup`.

> Never commit `secrets.toml`, API credentials, passwords, or private Google Sheets URLs to GitHub.

### 5. Run the dashboard

```bash
streamlit run app.py
```

The application will then be available through the local Streamlit server.

---

## 🔄 Data Pipeline

The simplified data flow of the application is:

```text
Google Sheets
      ↓
Data Extraction
      ↓
Data Preparation
      ↓
Metric Calculation
      ↓
Interactive Visualization
      ↓
Streamlit Dashboard
```

This structure separates data extraction, transformation, analytics, and visualization to keep the application modular and maintainable.

---

## 🎯 Project Objectives

This project was developed to:

- Centralize Business Development partnership monitoring
- Reduce manual spreadsheet monitoring
- Provide clearer visibility into partnership performance
- Improve document tracking
- Support data-driven decision-making
- Apply data analytics and visualization to a real organizational use case

---

## 🌐 Live Demo

The deployed dashboard can be explored here:

**https://bit.ly/BD2627_Dashboard**

The public version uses dummy data for demonstration purposes.

---

## ⚠️ Disclaimer

This repository is intended for portfolio and educational purposes.

All confidential organizational data, private documents, credentials, and sensitive partnership information have been excluded from the public repository. Any publicly accessible demonstration data is dummy or sanitized data and does not represent confidential partnership information.
