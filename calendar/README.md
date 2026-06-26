# IDX Market Calendar

Holiday-aware trading calendar for IDX (Indonesia Stock Exchange). One JSON data file shared across Go (collector) and Python (research).

## Data file

`calendar/idx_holidays.json` — canonical source. Must be kept in sync with `collector/internal/calendar/idx_holidays.json` (the Go embed copy).

## Yearly refresh procedure

### 1. Get the official IDX image

Go to [idx.co.id](https://www.idx.co.id) → Tentang BEI → Jadwal Libur Bursa. Download the official KALENDER LIBUR BURSA image for the target year.

### 2. Transcribe into JSON

Update `calendar/idx_holidays.json`:
- Add each non-trading date under `"holidays"`.
- Set `"kind"` to one of: `public_holiday`, `collective_leave`, `exchange_closure`.
- Set `"provisional": true` while the schedule is still a draft. Change to `false` once the official image is published and reconciled.
- Update `"source"` with today's date and a note about the image used.

### 3. Sync the Go embed copy

```bash
cp calendar/idx_holidays.json collector/internal/calendar/idx_holidays.json
```

Both files must be identical. The Go package embeds its local copy via `//go:embed`.

### 4. Verify the trading-day count

IDX publishes "JUMLAH HARI BURSA" (total trading days) on the calendar image. Verify your JSON matches:

```bash
cd research
.venv/bin/python - <<'EOF'
from datetime import date, timedelta
from lib.market_calendar import MarketCalendar
from pathlib import Path

cal = MarketCalendar.load()
year = 2027  # change as needed
d = date(year, 1, 1)
count = 0
while d.year == year:
    if cal.is_trading_day(d):
        count += 1
    d += timedelta(days=1)
print(f"{year} trading days: {count}")
EOF
```

Compare the printed count against the IDX-published JUMLAH HARI BURSA. They must match before setting `provisional: false`.

### 5. Run tests

```bash
cd research && .venv/bin/python -m pytest lib/test_market_calendar.py -v
cd collector && go test ./internal/calendar/... -v
```

### 6. Check deadlines tracker

```bash
python calendar/check_deadlines.py
```

Update `calendar/deadlines.yaml` with the new `holiday-refresh-YYYY` entry for the following year.

## Deadlines

Operational deadlines are tracked in `calendar/deadlines.yaml`. Run the checker to see what's coming due:

```bash
python calendar/check_deadlines.py [--lead 30]
```

Exit code 1 means at least one deadline is DUE SOON or OVERDUE.
