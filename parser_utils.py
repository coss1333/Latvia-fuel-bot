from datetime import datetime, timezone, timedelta

def parse_eur(s: str) -> float:
    try:
        return round(float(s.replace(",", ".").strip()), 3)
    except Exception:
        return None

def format_price(x) -> str:
    if x is None:
        return "-"
    return f"{x:.3f} € / l"

def human_now_lv() -> str:
    # Europe/Riga is UTC+2 / +3 DST; we format in local naive
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

def lv_time_now() -> datetime:
    return datetime.utcnow()

def chunk_text(text: str, limit: int = 4000):
    buf = []
    cur = 0
    lines = text.splitlines()
    acc = ""
    for line in lines:
        if len(acc) + len(line) + 1 > limit:
            buf.append(acc)
            acc = ""
        acc += (line + "\n")
    if acc:
        buf.append(acc)
    return buf
