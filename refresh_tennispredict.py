# refresh_tennispredict.py
# requests-driven + retry + progress + MMDD input/argv
#
# Key behavior (WS):
# - Fetch ALL records from API
# - Group by date_short (e.g. "03/04") and write player_count_MMDD_*.csv for EVERY date in dataset
# - Publish ALL generated player_count_*.csv to docs/data and update manifest.json/latest.json
#
# Run:
#   winpty python -u refresh_tennispredict.py
#   START_MMDD=0304 winpty python -u refresh_tennispredict.py
#   MMDD=0304 winpty python -u refresh_tennispredict.py   (forces target_date_short for out_day only)

import csv
import math
import re
import sys
import time
from collections import Counter, defaultdict
from urllib.parse import unquote

import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except Exception:
    Retry = None


# ===== Fixed config (WS) =====
YEAR = 2026

# Page you view (used to obtain csrf/cookies)
PAGE_EVENT_ID = 30609
REFERER = f"https://www.live-tennis.cn/zh/survivor/event/{PAGE_EVENT_ID}/{YEAR}/WS/detail"

# Data API event id (from page source / DataTables ajax url)
API_EVENT_ID = 135
API = f"https://www.live-tennis.cn/zh/survivor/event/{API_EVENT_ID}/{YEAR}/detail"

UA = "Mozilla/5.0"
# =============================

PAGE_SIZE = 200
PAGE_SLEEP = 0.2

CONNECT_TIMEOUT = 15
READ_TIMEOUT = 45
MAX_PAGE_ATTEMPTS = 5


def ask_mmdd_or_argv() -> tuple[str, str]:
    """
    Returns:
      tag='0304', date_short='03/04'

    Priority:
      1) Env MMDD=0304
      2) argv: python script.py 0304
      3) auto today in Asia/Shanghai (Beijing time)

    Guard:
      If today < START_MMDD (env) then use START_MMDD.
      Default START_MMDD=0304.
    """
    import os
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(os.getenv("TZ_NAME", "Asia/Shanghai"))
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()

    start_mmdd = os.getenv("START_MMDD", "0304").strip()

    def norm_mmdd(s: str) -> str | None:
        s = (s or "").strip()
        if not re.fullmatch(r"\d{4}", s):
            return None
        mm, dd = s[:2], s[2:]
        m_int, d_int = int(mm), int(dd)
        if 1 <= m_int <= 12 and 1 <= d_int <= 31:
            return s
        return None

    # 1) env
    env_mmdd = norm_mmdd(os.getenv("MMDD", ""))
    if env_mmdd:
        mm, dd = env_mmdd[:2], env_mmdd[2:]
        return env_mmdd, f"{mm}/{dd}"

    # 2) argv
    if len(sys.argv) >= 2:
        arg_mmdd = norm_mmdd(sys.argv[1])
        if arg_mmdd:
            mm, dd = arg_mmdd[:2], arg_mmdd[2:]
            return arg_mmdd, f"{mm}/{dd}"

    # 3) auto today
    today_mmdd = now.strftime("%m%d")
    if norm_mmdd(start_mmdd) and today_mmdd < start_mmdd:
        today_mmdd = start_mmdd

    mm, dd = today_mmdd[:2], today_mmdd[2:]
    return today_mmdd, f"{mm}/{dd}"


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})

    if Retry is not None:
        retry = Retry(
            total=6,
            connect=6,
            read=6,
            status=6,
            backoff_factor=0.8,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(["GET", "POST"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
        s.mount("https://", adapter)
        s.mount("http://", adapter)

    return s


def extract_csrf_from_html(html: str) -> str:
    m = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
    return m.group(1) if m else ""


def get_xsrf_from_session_cookie(sess: requests.Session) -> str:
    val = sess.cookies.get("XSRF-TOKEN", "")
    return unquote(val) if val else ""


def get_cookie_and_tokens(sess: requests.Session) -> tuple[str, str]:
    print("[info] GET referer page to obtain cookies + csrf-token ...", flush=True)
    r = sess.get(REFERER, headers={"Referer": REFERER}, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    r.raise_for_status()
    csrf = extract_csrf_from_html(r.text)
    xsrf = get_xsrf_from_session_cookie(sess)
    if not csrf:
        raise RuntimeError("Failed to extract csrf-token from HTML.")
    if not xsrf:
        raise RuntimeError("Failed to get XSRF-TOKEN from cookies.")
    return csrf, xsrf


def post_detail_page(sess: requests.Session, csrf: str, xsrf: str, start: int, length: int) -> dict:
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "x-requested-with": "XMLHttpRequest",
        "Referer": REFERER,
        "Origin": "https://www.live-tennis.cn",
        "x-csrf-token": csrf,
        "X-XSRF-TOKEN": xsrf,
    }
    data = {
        "draw": "1",
        "start": str(start),
        "length": str(length),
        "device": "0",
        "is_yec": "0",
    }

    r = sess.post(API, headers=headers, data=data, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    text = (r.text or "").strip()
    if not text or text[0] not in "{[":
        preview = text[:300].replace("\n", "\\n").replace("\r", "\\r")
        raise RuntimeError(f"Non-JSON response start={start}, status={r.status_code}, preview={preview}")

    return r.json()


def clean_username(x: str) -> str:
    return re.sub(r"<[^>]*>", "", x or "").strip()


def write_csv(path: str, fieldnames: list[str], rows: list[dict]):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def write_counter(path: str, items: list[tuple[str, int]]):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["player", "count"])
        w.writerows(items)


def fetch_page_with_retry(sess: requests.Session, csrf: str, xsrf: str, start: int, length: int) -> dict:
    for attempt in range(1, MAX_PAGE_ATTEMPTS + 1):
        try:
            return post_detail_page(sess, csrf, xsrf, start, length)
        except Exception as e:
            print(f"[warn] page start={start} attempt={attempt}/{MAX_PAGE_ATTEMPTS} failed: {e}", flush=True)
            time.sleep(0.9 * attempt)
    raise RuntimeError(f"Failed to fetch page start={start} after retries.")


def ds_to_tag(ds: str) -> str:
    # "03/04" -> "0304"
    return (ds or "").replace("/", "")


def main():
    # This tag/date_short is only used to write picks_event{API_EVENT_ID}_{tag}.csv (day view),
    # counts are now generated for ALL date_short values.
    tag, target_date_short = ask_mmdd_or_argv()
    print(f"[info] target date_short={target_date_short} (tag={tag})", flush=True)

    sess = make_session()
    csrf, xsrf = get_cookie_and_tokens(sess)
    print("[info] csrf/xsrf ready, start fetching detail pages ...", flush=True)

    print(f"[info] fetching first page start=0 length={PAGE_SIZE} ...", flush=True)
    first = fetch_page_with_retry(sess, csrf, xsrf, 0, PAGE_SIZE)

    total = int(first.get("recordsTotal") or 0)
    data0 = first.get("data") or []
    if not isinstance(data0, list):
        raise RuntimeError(f"Unexpected response: recordsTotal={total}, data_type={type(data0)}")

    if total == 0:
        print("[info] recordsTotal=0 (no picks yet). Will output empty csv.", flush=True)

    pages = math.ceil(total / PAGE_SIZE) if total > 0 else 0
    print(f"[info] recordsTotal={total}, pages={pages}", flush=True)

    all_items = list(data0)
    for page_idx in range(1, pages):
        start = page_idx * PAGE_SIZE
        time.sleep(PAGE_SLEEP)
        print(f"[info] fetching page start={start} length={PAGE_SIZE} ...", flush=True)
        obj = fetch_page_with_retry(sess, csrf, xsrf, start, PAGE_SIZE)
        chunk = obj.get("data") or []
        if not isinstance(chunk, list):
            raise RuntimeError(f"Unexpected response at start={start}: data is not list")
        all_items.extend(chunk)

    fields = ["day", "date_short", "created_at", "user_id", "username", "fill", "player", "fill_alt", "player_alt"]

    full_rows: list[dict] = []
    for it in all_items:
        full_rows.append({
            "day": it.get("day"),
            "date_short": it.get("date_short"),
            "created_at": it.get("created_at"),
            "user_id": it.get("user_id"),
            "username": clean_username(it.get("username", "")),
            "fill": it.get("fill"),
            "player": it.get("player"),
            "fill_alt": it.get("fill_alt"),
            "player_alt": it.get("player_alt"),
        })

    # Always overwrite full dump
    write_csv(f"picks_event{API_EVENT_ID}.csv", fields, full_rows)

    # Keep a "day view" dump for the chosen target_date_short (optional)
    day_rows = [r for r in full_rows if (r.get("date_short") or "").strip() == target_date_short]
    out_day = f"picks_event{API_EVENT_ID}_{tag}.csv"
    write_csv(out_day, fields, day_rows)

    print(f"OK: total records (all days) = {len(full_rows)} (recordsTotal={total}) -> picks_event{API_EVENT_ID}.csv (overwritten)")
    print(f"OK: day view {target_date_short} rows = {len(day_rows)} -> {out_day} (overwritten)")

    # -------- NEW: counts for ALL date_short --------
    groups = defaultdict(list)
    for r in full_rows:
        ds = (r.get("date_short") or "").strip()
        if ds:
            groups[ds].append(r)

    generated_files: list[str] = []  # for publish
    all_tags: list[str] = []

    for ds in sorted(groups.keys()):
        rows = groups[ds]
        tag2 = ds_to_tag(ds)
        if not re.fullmatch(r"\d{4}", tag2):
            # skip weird/empty ds
            continue

        all_tags.append(tag2)

        main_c = Counter()
        alt_c = Counter()
        user_ids = set()

        for r in rows:
            uid = r.get("user_id")
            if uid is not None:
                user_ids.add(str(uid))

            p = (r.get("player") or "").strip()
            pa = (r.get("player_alt") or "").strip()
            if p:
                main_c[p] += 1
            if pa:
                alt_c[pa] += 1

        main_items = sorted(main_c.items(), key=lambda x: (-x[1], x[0]))
        alt_items = sorted(alt_c.items(), key=lambda x: (-x[1], x[0]))

        f_main = f"player_count_{tag2}_main.csv"
        f_alt = f"player_count_{tag2}_alt.csv"
        write_counter(f_main, main_items)
        write_counter(f_alt, alt_items)

        generated_files.extend([f_main, f_alt])

        print(f"OK: {ds} rows={len(rows)} users={len(user_ids)} -> {f_main} / {f_alt}")

    # Choose latest tag among observed date_short for docs/data/latest.json
    latest_tag = sorted(set(all_tags))[-1] if all_tags else None
    # -------- END NEW --------

    # ---- Hybrid model data layer: publish to docs/data ----
    import os, json, glob
    from datetime import datetime, timezone

    data_dir = os.path.join("docs", "data")
    os.makedirs(data_dir, exist_ok=True)

    # Copy ALL generated player_count files into docs/data (overwrite)
    published = 0
    for fn in sorted(set(generated_files)):
        if not os.path.exists(fn):
            continue
        dst = os.path.join(data_dir, fn)
        with open(fn, "rb") as fsrc, open(dst, "wb") as fdst:
            fdst.write(fsrc.read())
        published += 1

    # Build manifest.json from docs/data main files
    dates = []
    for p in glob.glob(os.path.join(data_dir, "player_count_*_main.csv")):
        m = re.search(r"player_count_(\d{4})_main\.csv$", os.path.basename(p))
        if m:
            dates.append(m.group(1))
    dates = sorted(set(dates))
    latest = latest_tag if latest_tag in dates else (dates[-1] if dates else None)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    version = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    manifest = {"dates": dates, "latest": latest}
    latest_json = {"latest": latest, "updated_at": now, "version": version}

    with open(os.path.join(data_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    with open(os.path.join(data_dir, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(latest_json, f, ensure_ascii=False, indent=2)

    print(f"[publish] copied {published} player_count files into {data_dir}", flush=True)
    print(f"[publish] updated docs/data/manifest.json and latest.json (latest={latest}, version={version})", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[info] Interrupted by user (Ctrl+C).")