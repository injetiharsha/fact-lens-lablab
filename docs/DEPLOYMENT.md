# Deployment Instructions

Target: Ubuntu VM on Vultr, Azure, or any similar Linux server.

## Server Setup

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv nginx
git clone <your-repo-url> factlens-crew
cd factlens-crew
python3.11 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```text
GEMINI_API_KEY=...
FEATHERLESS_API_KEY=...
TAVILY_API_KEY=
FACTLENS_ALLOW_OFFLINE_FALLBACK=0
```

## Run Backend

```bash
./.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000
```

For a persistent service, create `/etc/systemd/system/factlens.service`:

```ini
[Unit]
Description=FactLens Crew API
After=network.target

[Service]
WorkingDirectory=/home/azureuser/factlens-crew
EnvironmentFile=/home/azureuser/factlens-crew/.env
ExecStart=/home/azureuser/factlens-crew/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000
Restart=always
User=azureuser

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now factlens
sudo systemctl status factlens
```

## Health Check

```bash
curl http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"healthy"}
```
