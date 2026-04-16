# Vigil — Deployment Guide

Guida operativa per pubblicare `vigil-backend` in produzione su **Ubuntu 22.04+ / Debian 12+**.

---

## Architettura di deploy

- **Backend**: FastAPI (`main.py`) eseguito da `systemd`
- **Frontend**: build statica Vite servita da **Nginx**
- **Proxy**: Nginx espone:
  - `/` → frontend statico
  - `/api/` → backend FastAPI su `127.0.0.1:8000`
  - WebSocket gestito via `Upgrade`/`Connection`

---

## File coinvolti

- `deploy.sh` → bootstrap automatico server
- `vigil.service` → unit systemd del backend
- `nginx.conf` → configurazione Nginx production
- `vigil-db-backup.service` + `vigil-db-backup.timer` → backup automatico SQLite
- `tools/backup_vigil_db.sh` → script backup (snapshot consistente + gzip)
- `vigil.logrotate` → rotazione log applicativi
- `.env.example` → base per `.env` di produzione

---

## Deploy rapido

Sul server:

```bash
export VIGIL_REPO_URL="https://github.com/<owner>/<repo>.git"
bash deploy.sh
```

Lo script:

1. installa dipendenze di sistema
2. installa **Node.js 20** se manca
3. clona o aggiorna il repository
4. crea `venv` e installa `requirements.txt`
5. esegue `npm ci && npm run build`
6. pubblica il frontend in `/var/www/vigil`
7. installa `vigil.service`
8. installa `nginx.conf`
9. configura HTTPS automatico con Certbot (se `VIGIL_DOMAIN` e DNS pronti)
10. abilita backup DB ogni 6 ore (`systemd timer`)
11. abilita log rotation per `/var/log/vigil/app.log`
12. riavvia `vigil` e ricarica Nginx
13. verifica `/health`

---

## Percorsi usati in produzione

- app: `/home/ubuntu/vigil-backend`
- frontend statico: `/var/www/vigil`
- backend locale: `127.0.0.1:8000`
- servizio: `vigil.service`

---

## Variabili ambiente minime

> **Database**: `VIGIL_DB_URL` usa SQLite di default — adatto per sviluppo locale e MVP con carico limitato. Per un deployment produttivo continuativo (alta frequenza di scrittura, multi-worker, backup incrementali) sostituire con PostgreSQL; la sezione **Postgres migration** in coda a questo documento descrive la procedura.

Esempio `.env`:

```env
VIGIL_DB_URL=sqlite:///./vigil.db
VIGIL_ALLOWED_ORIGINS=https://tuodominio.it
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
```

Variabili opzionali per deploy hardening:

```env
VIGIL_DOMAIN=vigil.example.com
LETSENCRYPT_EMAIL=ops@example.com
BACKUP_DIR=/var/backups/vigil
BACKUP_KEEP_DAYS=14
```

---

## Verifiche post-deploy

```bash
curl http://127.0.0.1:8000/health
curl http://YOUR_SERVER/api/health
sudo systemctl status vigil
sudo journalctl -u vigil -f
sudo nginx -t
sudo systemctl status vigil-db-backup.timer
sudo systemctl list-timers | grep vigil-db-backup
sudo ls -lh /var/backups/vigil
sudo tail -n 50 /var/log/vigil/app.log
```

Se HTTPS e attivo:

```bash
curl -I https://YOUR_DOMAIN/
sudo certbot certificates
```

---

## Operazioni utili

### Restart backend
```bash
sudo systemctl restart vigil
```

### Reload nginx
```bash
sudo nginx -t && sudo systemctl reload nginx
```

### Rebuild frontend manuale
```bash
cd /home/ubuntu/vigil-backend/vigil-frontend
npm ci
npm run build
sudo rsync -a --delete dist/ /var/www/vigil/
```

### Eseguire backup DB manuale
```bash
sudo systemctl start vigil-db-backup.service
sudo journalctl -u vigil-db-backup.service -n 50 --no-pager
```

### Verifica logrotate
```bash
sudo logrotate -d /etc/logrotate.d/vigil
```

---

## Prossimi step consigliati

- monitoraggio uptime esterno (healthcheck remoto)
- alerting su errori applicativi (mail/Slack/Telegram)
- eventuale migrazione da SQLite a Postgres se cresce il carico

---

## Postgres migration

Quando il volume di eventi e media supera le capacita di SQLite (scritture concorrenti, multi-worker uvicorn, backup incrementali), migrare a PostgreSQL:

1. **Installare il driver**: `pip install psycopg2-binary`.
2. **Impostare `VIGIL_DB_URL`** nel `.env`:
  ```
  VIGIL_DB_URL=postgresql://vigil:password@localhost:5432/vigil
  ```
3. **Creare database e utente** su Postgres:
  ```sql
  CREATE USER vigil WITH PASSWORD 'password';
  CREATE DATABASE vigil OWNER vigil;
  ```
4. **Prima avviata**: `init_db()` eseguira `Base.metadata.create_all()` su Postgres — lo schema viene creato automaticamente.
5. Riavviare il servizio: `sudo systemctl restart vigil`.

Le migrazioni incrementali (ALTER TABLE) in `vigil/core/database.py` sono idempotenti e funzionano su entrambi i backend.
