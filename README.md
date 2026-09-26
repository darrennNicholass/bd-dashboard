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
