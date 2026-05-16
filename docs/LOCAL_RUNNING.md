# Local Running Instructions

## Prerequisites

- Python 3.11 recommended.
- Internet access for live search.
- Optional Tesseract binary if you want image OCR through `pytesseract`.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set real keys if available:

```text
GEMINI_API_KEY=...
FEATHERLESS_API_KEY=...
FACTLENS_ALLOW_OFFLINE_FALLBACK=0
```

## Run

```powershell
.\.venv\Scripts\python api\main.py
```

Open:

```text
http://127.0.0.1:8000
```

## Verify

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```

Real mode returns `needs_live_evidence` if the machine cannot reach live evidence providers. That is expected and safer than a fake verdict.
