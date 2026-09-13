from datetime import date, datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))


def get_wib_day_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """
    Mengonversi tanggal kalender WIB (from & to) menjadi boundary datetime UTC.
    Contoh: 13 Sept 2026 s/d 13 Sept 2026 (WIB) ->
    start_utc = 2026-09-12 17:00:00 UTC
    end_utc   = 2026-09-13 17:00:00 UTC
    """
    start_wib = datetime.combine(date_from, datetime.min.time(), tzinfo=WIB)
    end_wib = datetime.combine(date_to, datetime.min.time(), tzinfo=WIB) + timedelta(days=1)
    return start_wib.astimezone(timezone.utc), end_wib.astimezone(timezone.utc)


def get_wib_today_bounds() -> tuple[datetime, datetime, date]:
    """
    Mendapatkan boundary UTC untuk hari ini di zona waktu WIB.
    Returns: (day_start_utc, day_end_utc, today_wib_date)
    """
    now_wib = datetime.now(timezone.utc).astimezone(WIB)
    today_wib = now_wib.date()
    day_start_utc, day_end_utc = get_wib_day_bounds(today_wib, today_wib)
    return day_start_utc, day_end_utc, today_wib


def get_wib_month_start_bounds() -> tuple[datetime, datetime, date]:
    """
    Mendapatkan boundary UTC untuk awal bulan sampai akhir hari ini di WIB.
    Returns: (month_start_utc, day_end_utc, today_wib_date)
    """
    now_wib = datetime.now(timezone.utc).astimezone(WIB)
    today_wib = now_wib.date()
    month_start_wib = today_wib.replace(day=1)
    month_start_utc, day_end_utc = get_wib_day_bounds(month_start_wib, today_wib)
    return month_start_utc, day_end_utc, today_wib
