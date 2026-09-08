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
| `SWITCH_PIN` | PIN-kod som krävs för att slå på/av säkringarna från dashboarden |
| `CRON_SECRET` | Hemlig token för `/api/sync` och GitHub Actions cron |
| `TEMPIRO_SYNC_LOOKBACK_DAYS` | Valfri: maximalt antal dagar som en vanlig energisynk hämtar, default `2` |
| `WATER_HEATER_DEVICE_IDS` | Kommaseparerade Tempiro device-id för varmvattenberedarens säkringar. Om tomt används alla enheter |
| `WATER_HEATER_OFF_ALERT_MINUTES` | Antal minuter innan avstängd beredare blir kritiskt larm, default `180` |
| `RESEND_API_KEY` | Valfri: API-nyckel för e-postlarm via Resend |
| `ALERT_EMAIL_TO` | Valfri: mottagare för e-postlarm |
| `ALERT_EMAIL_FROM` | Valfri: avsändare för e-postlarm, t.ex. `Tempiro <alerts@din-domän.se>` |
| `ALERT_REPEAT_HOURS` | Valfri: minsta antal timmar mellan e-postlarm, default `6` |

Sätt samma `CRON_SECRET` både som Vercel Environment Variable och som GitHub Actions secret.

## Arkitektur

- `public/index.html` - Dashboard (HTML/JS)
- `api/devices.py` - Realtidsdata från Tempiro API
- `api/energy.py` - Historisk energidata från Supabase
- `api/prices.py` - Spotpriser från Supabase
- `api/switch.py` - Styra säkringar via Tempiro API
- `api/sync.py` - Cron job (var 15:e minut) som synkar data

GitHub Actions anropar energisynken separat för VVB1, VVB2 och VVB3. Det gör
att en långsam mätare inte blockerar de andra. Om Tempiros intervallhistorik
inte svarar sparas den aktuella effekten som en ny snapshot med `upsert`;
befintlig historik raderas eller skrivs aldrig om av synken.

Skyddade dellägen kan även köras manuellt med samma `CRON_SECRET`:

- `/api/sync?mode=energy&device=VVB1`
- `/api/sync?mode=prices`
- `/api/sync?mode=heater`

## Lokal migrering

```bash
pip install supabase
export SUPABASE_SECRET=din_secret_key
python migrate_to_supabase.py
```
