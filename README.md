# Fahrzeugübergabe – Schadensprotokoll (Demo)

Mobile Web-App für die Pilot-Demo: Schäden am Van markieren, Fotos + Beschreibung, PDF exportieren.

## Stufe 1 (Klick-Dummy)

- SVG Van (Draufsicht), Tipp/Klick setzt rote Marker
- Popup: Beschreibung + Foto (Handy-Kamera)
- **PDF Vorschau (Demo)**: festes Beispiel-PDF vom Backend
- **Drucken**: Browser-Druckdialog (ohne Backend)

## Stufe 2 (Minimal-MVP)

- `POST /api/generate-pdf`: echtes PDF mit Fahrzeugdaten, Skizze, Schaden-Tabelle + Fotos
- Backend: FastAPI + fpdf2

## Starten

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x run.sh
./run.sh
```

Alternativ ohne Auto-Reload (empfohlen für Demo-Termin, kein Neustart-Loop):

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

**Hinweis:** `uvicorn ... --reload` im `server/`-Ordner kann durch Änderungen in `.venv/` ständig neu starten. Nutze `./run.sh` (schließt `.venv` aus) oder starte ohne `--reload`.

Dann im Browser: **http://127.0.0.1:8000**

(Ein Server liefert Frontend + API.)

## Nur Frontend (ohne PDF-Download)

```bash
cd public
python3 -m http.server 8080
```

PDF-Download braucht dann Backend auf Port 8000.

## Demo-Ablauf (Termin)

1. Kennzeichen, Kunde, Fahrzeug ausfüllen
2. Auf Van tippen → Schaden beschreiben + Foto
3. **PDF Vorschau (Demo)** zeigen
4. Live: eigenen Schaden markieren → **PDF herunterladen**

## Vercel Deploy (öffentliche Demo)

Statisches Frontend aus `public/`, FastAPI als Serverless-Function. PDF-Export funktioniert. Mit **Vercel Blob** (Store „damage“) sind Verträge und Schadenfotos auch auf Vercel persistent.

### Vercel Blob (Verträge + Fotos)

1. Vercel Dashboard → Projekt → **Storage** → Blob Store „damage“ mit dem Projekt verbinden
2. Env-Variablen werden automatisch gesetzt (`BLOB_READ_WRITE_TOKEN`, `BLOB_STORE_ID`)
3. Lokal: `npx vercel env pull` (Token für Tests)
4. Deploy → Status zeigt „Blob Storage aktiv“, Übergabe speichern/laden funktioniert

Gespeichert unter `damage-demo/contracts/*.json` und `damage-demo/photos/{vertrag}/{nr}.jpg`.

### Voraussetzungen

- Vercel-Account
- Vercel CLI: `npm i -g vercel`

### Erstes Deploy

```bash
vercel login
vercel          # Preview
vercel --prod   # Production-URL
```

Bei GitHub-Import: **Root Directory** leer lassen, Framework = Auto (FastAPI).

### Lokal wie Vercel testen

```bash
npx vercel dev
# → http://localhost:3000 (UI + API; statische Dateien aus public/)
```

Contract-Buttons testweise ausblenden (wie auf Vercel):

```bash
VERCEL=1 uvicorn main:app --host 127.0.0.1 --port 8000
```

### Lokal mit vollem Funktionsumfang

```bash
cd server
./run.sh
# → http://127.0.0.1:8000 (Frontend aus public/, Verträge aktiv)
```
