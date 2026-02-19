# Tempiro Energianalys - Vercel

Dashboard för Tempiro smarta säkringar, hostad på Vercel med Supabase som databas.

## Miljövariabler (sätt i Vercel Dashboard)

| Variabel | Beskrivning |
|----------|-------------|
| `SUPABASE_URL` | `https://vkecqtpxygfhwqesievk.supabase.co` |
| `SUPABASE_PUBLISHABLE` | Publishable key från Supabase |
| `SUPABASE_SECRET` | Secret key från Supabase |
| `TEMPIRO_USERNAME` | Ditt Tempiro-användarnamn |
| `TEMPIRO_PASSWORD` | Ditt Tempiro-lösenord |

## Arkitektur

- `public/index.html` - Dashboard (HTML/JS)
- `api/devices.py` - Realtidsdata från Tempiro API
- `api/energy.py` - Historisk energidata från Supabase
- `api/prices.py` - Spotpriser från Supabase
- `api/switch.py` - Styra säkringar via Tempiro API
- `api/sync.py` - Cron job (var 15:e minut) som synkar data

## Lokal migrering

```bash
pip install supabase
export SUPABASE_SECRET=din_secret_key
python migrate_to_supabase.py
```
