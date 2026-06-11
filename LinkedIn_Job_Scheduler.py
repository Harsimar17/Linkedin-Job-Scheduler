"""
LinkedIn Job Scheduler — Hourly Fetcher
Runs indefinitely, fetching jobs posted in the past hour every hour on the hour.
New results are appended to the CSV so the file grows over time.

HOW TO USE:
    python LinkedIn_Job_Scheduler.py

    You will be prompted once for location, job role, and  file.
    The script then waits until the next top-of-hour and fires every 60 minutes.
    Press Ctrl+C to stop.
"""

import csv
import json
import os
import re
import time
import random
from dataclasses import dataclass, fields, astuple
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

# ─── Configuration ────────────────────────────────────────────────────────────

JOB_COUNT   = 10          # max jobs per hourly run
FETCH_WINDOW = 3600        # 1 hour in seconds — always fetch the past hour
_FILE = "linkedin_jobs_hourly"

LI_AT = "AQEDAU60xhACGa-kAAABnqetD0MAAAGey7mTQ1YAT_2eKF22aKQzQpDRIeurRfnGiuCfLdvaY2PHxHJOqaiwmJjICoeYB5sSPKhMrbyxUxlAnN0H7_rYvh0TzHaxLAYFxQrR4ex0MXSAu45yAdt-07-C"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.linkedin.com/jobs/",
}

APPLICANT_KEYS = (
    "applicantCountText", "formattedApplicantCount",
    "numApplicants", "clickedApplyText", "applyClickCount",
)

APPLICANT_PATTERN = re.compile(
    r"(?:"
    r"[Oo]ver\s+[\d,]+\s*(?:people\s+clicked\s+apply|applicants?)"
    r"|[\d,]+\+?\s*(?:people\s+clicked\s+apply|applicants?)"
    r"|[Bb]e among the first\s+[\d,]+\s*applicants?"
    r")",
    re.IGNORECASE,
)

SPONSORSHIP_PATTERN = re.compile(
    r"\b(?:relocation|relocat\w+|sponsor(?:ship)?|visa\s+sponsor\w*|work\s+authoriz\w*)\b",
    re.IGNORECASE,
)

# ─── Data Model ───────────────────────────────────────────────────────────────

@dataclass
class Job:
    title:        str
    company:      str
    location:     str
    date_posted:  str
    applicants:   str
    job_url:      str
    description:  str
    fetched_at:   str  # timestamp of the hourly run that found this job

# ─── Session ──────────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    session.max_redirects = 5  # prevent redirect loops (LinkedIn login loops)
    if LI_AT:
        session.cookies.set("li_at", LI_AT, domain=".linkedin.com")
    else:
        print("No li_at cookie — falling back to unauthenticated scraping.")
    return session


def _is_login_redirect(resp: requests.Response) -> bool:
    """Return True if LinkedIn redirected us to the login/authwall page."""
    url = resp.url
    return any(x in url for x in ("/login", "/authwall", "/checkpoint", "/uas/"))

# ─── Fetching ─────────────────────────────────────────────────────────────────

def build_search_url(keyword: str, location: str, start: int, seconds: int) -> str:
    params = {
        "keywords": keyword,
        "location": location,
        "f_TPR":    seconds,
        "start":    start,
        "f_EA":"true"
    }
    return "https://www.linkedin.com/jobs/search/?" + urlencode(params)


def fetch_page(session: requests.Session, keyword: str, location: str, start: int, seconds: int) -> str | None:
    url = build_search_url(keyword, location, start, seconds)
    try:
        resp = session.get(url, timeout=15, allow_redirects=True)
        if _is_login_redirect(resp):
            print("  [!] LinkedIn redirected to login — li_at cookie is expired or invalid.")
            print("      Please refresh your li_at cookie and update LI_AT in the script.")
            return None
        if resp.status_code == 200:
            return resp.text
        print(f"  [!] Unexpected status {resp.status_code}")
        return None
    except requests.TooManyRedirects:
        print("  [!] Too many redirects — li_at cookie is likely expired. Please refresh it.")
        return None
    except requests.RequestException as e:
        print(f"  [!] Request error: {e}")
        return None


def fetch_job_page(session: requests.Session, job_url: str) -> str | None:
    try:
        resp = session.get(job_url, timeout=15, allow_redirects=True)
        if _is_login_redirect(resp):
            return None
        return resp.text if resp.status_code == 200 else None
    except requests.TooManyRedirects:
        return None
    except requests.RequestException:
        return None

# ─── Parsing ──────────────────────────────────────────────────────────────────

def _timestamp_to_date(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _extract_job_url(entity_urn: str) -> str:
    match = re.search(r":(\d+)$", entity_urn) or re.search(r":\((\d+),", entity_urn)
    return f"https://www.linkedin.com/jobs/view/{match.group(1)}" if match else ""


def _extract_title(item: dict) -> str:
    maybe_title = item.get("title")
    if not maybe_title:
        return ""
    if isinstance(maybe_title, dict):
        return maybe_title.get("text", "").strip()
    return (item.get("title") or item.get("jobTitle") or "").strip()


def _extract_company(item: dict) -> str:
    company_details = item.get("companyDetails") or {}
    if not isinstance(company_details, dict):
        return ""
    return (
        company_details.get("company", {}).get("name", "")
        or company_details.get("companyName", "")
        or ""
    ).strip()


def _parse_job_item(item: dict, seen_urls: set[str]) -> "Job | None":
    item_type = item.get("$type", "")
    if "JobPosting" not in item_type and "jobPosting" not in item_type:
        return None

    title = _extract_title(item)
    if not title:
        return None

    company     = _extract_company(item)
    location    = (item.get("formattedLocation") or item.get("location") or "").strip()
    date_posted = item.get("listedAt") or item.get("originalListedAt") or ""
    if isinstance(date_posted, int):
        date_posted = _timestamp_to_date(date_posted)

    job_url = _extract_job_url(item.get("entityUrn", ""))
    if job_url in seen_urls:
        return None

    seen_urls.add(job_url)
    return Job(title, company, location, str(date_posted), "N/A", job_url, "N/A", "")


def parse_jobs_from_json(soup: BeautifulSoup) -> list["Job"]:
    jobs: list[Job] = []
    seen_urls: set[str] = set()

    for code_tag in soup.find_all("code"):
        raw = code_tag.get_text()
        if '"jobTitle"' not in raw and '"title"' not in raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        for item in data.get("included", []):
            job = _parse_job_item(item, seen_urls)
            if job:
                jobs.append(job)

    return jobs


def parse_jobs_from_html(soup: BeautifulSoup) -> list["Job"]:
    jobs: list[Job] = []

    for card in soup.find_all("li"):
        title_tag    = card.find("h3", class_="base-search-card__title")
        company_tag  = card.find("h4", class_="base-search-card__subtitle")
        location_tag = card.find("span", class_="job-search-card__location")
        date_tag     = card.find("time")
        link_tag     = card.find("a", class_="base-card__full-link")

        title    = title_tag.get_text(strip=True)    if title_tag    else ""
        company  = company_tag.get_text(strip=True)  if company_tag  else ""
        location = location_tag.get_text(strip=True) if location_tag else ""
        date_posted = date_tag.get("datetime", "")   if date_tag     else ""
        job_url  = link_tag["href"].split("?")[0]    if link_tag and link_tag.get("href") else ""

        if title:
            jobs.append(Job(title, company, location, date_posted, "N/A", job_url, "N/A", ""))

    return jobs


def parse_jobs(html: str) -> list["Job"]:
    soup = BeautifulSoup(html, "html.parser")
    json_jobs = parse_jobs_from_json(soup)
    return json_jobs if json_jobs else parse_jobs_from_html(soup)

# ─── Applicant Count ──────────────────────────────────────────────────────────

def _applicants_from_json(soup: BeautifulSoup) -> str | None:
    for code_tag in soup.find_all("code"):
        raw = code_tag.get_text()
        if not any(k in raw for k in ("applicant", "clicked apply", "clickedApply")):
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in (data.get("included") or [data]):
            if not isinstance(item, dict):
                continue
            for key in APPLICANT_KEYS:
                val = item.get(key)
                if val:
                    return str(val).strip()
    return None


def _applicants_from_html(soup: BeautifulSoup) -> str | None:
    tag = soup.find("span", class_=re.compile(r"num-applicants"))
    return tag.get_text(strip=True) if tag else None


def _applicants_from_text(page_text: str) -> str | None:
    match = APPLICANT_PATTERN.search(page_text)
    return match.group(0).strip() if match else None


def fetch_applicants_and_posted_time(session: requests.Session, job_url: str) -> str:
    if not job_url:
        return "N/A"
    page_text = fetch_job_page(session, job_url)
    if not page_text:
        return "N/A"
    soup = BeautifulSoup(page_text, "html.parser")
    hours_posted = re.findall(r'(\d+\s+(?:minute|hour|day|week|month|year)s?\s+ago)',page_text, re.IGNORECASE)
    
    posted_time = ""
    if hours_posted is not None and len(hours_posted) > 0:
        posted_time = hours_posted[0]
    return (
        _applicants_from_json(soup)
        or _applicants_from_html(soup)
        or _applicants_from_text(page_text)
        or "N/A"
    ), posted_time


def extract_applicant_count(applicant_str: str) -> int:
    match = re.search(r"[\d,]+", applicant_str)
    if not match:
        return -1
    return int(match.group(0).replace(",", ""))

# ─── Description Fetching ─────────────────────────────────────────────────────

def _job_id_from_url(job_url: str) -> str | None:
    match = re.search(r"/jobs/view/(\d+)", job_url)
    return match.group(1) if match else None


def _fetch_via_voyager(session: requests.Session, job_id: str, retries: int, backoff: float) -> str | None:
    api_url = (
        f"https://www.linkedin.com/voyager/api/jobs/jobPostings/{job_id}"
        "?decorationId=com.linkedin.voyager.deco.jobs.web.shared.WebFullJobPosting-65"
    )
    voyager_headers = {
        "accept": "application/vnd.linkedin.normalized+json+2.1",
        "csrf-token": "ajax:0",
        "x-restli-protocol-version": "2.0.0",
        "x-li-lang": "en_US",
        "x-li-track": '{"clientVersion":"1.13.5361"}',
        "Referer": f"https://www.linkedin.com/jobs/view/{job_id}/",
    }

    for attempt in range(1, retries + 1):
        try:
            resp = session.get(api_url, headers=voyager_headers, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("included", [data.get("data", {})]):
                    if not isinstance(item, dict):
                        continue
                    desc = item.get("description") or item.get("descriptionText")
                    if not desc:
                        continue
                    text = desc.get("text") if isinstance(desc, dict) else str(desc)
                    if text and len(text.strip()) > 100:
                        return text.strip()
            elif resp.status_code in (429, 503):
                wait = backoff * attempt + random.uniform(0.5, 1.5)
                time.sleep(wait)
            else:
                break
        except requests.TooManyRedirects:
            break
        except requests.RequestException as exc:
            wait = backoff * attempt
            time.sleep(wait)
        except Exception:
            break
    return None


def _fetch_via_guest_api(session: requests.Session, job_id: str, retries: int, backoff: float) -> str | None:
    api_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

    for attempt in range(1, retries + 1):
        try:
            resp = session.get(api_url, timeout=20)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                desc_tag = (
                    soup.find("div", class_=re.compile(r"description"))
                    or soup.find("div")
                )
                if desc_tag:
                    for bad in desc_tag.find_all(["script", "style", "nav"]):
                        bad.decompose()
                    text = desc_tag.get_text(separator="\n", strip=True)
                    if len(text) > 100:
                        return text
            elif resp.status_code in (429, 503):
                wait = backoff * attempt + random.uniform(0.5, 1.5)
                time.sleep(wait)
            else:
                break
        except requests.TooManyRedirects:
            break
        except Exception:
            break
    return None


def fetch_description(session: requests.Session, job_url: str, retries: int = 3, backoff: float = 2.0) -> str:
    if not job_url:
        return "N/A"
    job_id = _job_id_from_url(job_url)
    if not job_id:
        return "N/A"
    try:
        description = (
            _fetch_via_voyager(session, job_id, retries, backoff)
            or _fetch_via_guest_api(session, job_id, retries, backoff)
            or "N/A"
        )
    except Exception:
        return "N/A"

    if description != "N/A":
        lines = [ln.strip() for ln in description.splitlines()]
        description = "\n".join(ln for ln in lines if ln)

    return description

# ─── Filtering & Collection ───────────────────────────────────────────────────

def is_low_competition(job: Job) -> bool:
    count = extract_applicant_count(job.applicants)
    return count != -1 and count < 100


def collect_jobs(session: requests.Session, location: str, job_role: str, fetched_at: str) -> list[Job]:
    all_jobs: list[Job] = []
    seen_urls: set[str] = set()
    start = 0

    while True:
        if len(all_jobs) >= JOB_COUNT:
            break

        html = fetch_page(session, job_role, location, start, FETCH_WINDOW)
        if not html:
            print("  Stopping early due to fetch error.")
            break

        page_jobs = parse_jobs(html)
        if not page_jobs:
            url = build_search_url(job_role, location, start, FETCH_WINDOW)
            print(f"  No more jobs — end of results or blocked.")
            print(f"  URL: {url}")
            break

        for job in page_jobs:
            if job.job_url in seen_urls:
                continue
            seen_urls.add(job.job_url)

            job.applicants, posted_time = fetch_applicants_and_posted_time(session, job.job_url)
            if is_low_competition(job) and posted_time.strip() != "" and ("minutes" in posted_time or "hours" in posted_time):
                print("Posted Time : ", posted_time)
                job.description = fetch_description(session, job.job_url)
                job.fetched_at  = fetched_at
                all_jobs.append(job)
                print(f"  Collected ({len(all_jobs)}/{JOB_COUNT}): {job.title} @ {job.company}")

            if len(all_jobs) >= JOB_COUNT:
                break

        start += 25

    return all_jobs

# ─── CSV  ───────────────────────────────────────────────────────────────

def save_csv(jobs: list[Job], filename: str):
    file_exists = os.path.exists(filename) and os.path.getsize(filename) > 0
    headers = [f.name for f in fields(Job)]

    with open(filename, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(headers)
        for job in jobs:
            writer.writerow(astuple(job))

    print(f"  Saved {len(jobs)} jobs → '{filename}'")

# ─── Scheduler ────────────────────────────────────────────────────────────────

INTERVAL_MINUTES = 60  # run every 60 minutes


def seconds_until_next_interval() -> float:
    """Return seconds until the next 15-minute mark (:00, :15, :30, :45)."""
    now = datetime.now()
    minutes_past = now.minute % INTERVAL_MINUTES
    seconds_past = minutes_past * 60 + now.second
    wait = INTERVAL_MINUTES * 60 - seconds_past
    return float(wait)


def run_scheduler(location: str, job_role: str, _file: str):
    session = make_session()

    print(f"\nScheduler started for '{job_role}' in '{location}'.")
    print(f" file  : {_file}")
    print(f"Interval     : every {INTERVAL_MINUTES} minutes")
    print(f"Fetch window : past 1 hour of jobs each run")
    print("Press Ctrl+C to stop.\n")

    while True:
        fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{'─'*60}")
        print(f"[{fetched_at}] Fetching jobs posted in the past hour …")
        print(f"{'─'*60}")

        jobs = collect_jobs(session, location, job_role, fetched_at)

        if jobs:
            save_csv(jobs, _file + "_" + str(datetime.now()) + ".csv")
        else:
            print("  No qualifying jobs found this run.")

        # Sleep until the next 15-minute mark
        wait = seconds_until_next_interval()
        next_run = datetime.now() + timedelta(seconds=wait)
        print(f"\nNext run at {next_run.strftime('%H:%M:%S')} (waiting {wait/60:.1f} min) …")
        time.sleep(wait)

# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    location = input("Enter location for job postings:\n").strip()
    job_role = input("Enter job role:\n").strip()
    _file = input(f" CSV file name (press Enter for '{_FILE}'):\n").strip()
    if not _file:
        _file = _FILE

    try:
        run_scheduler(location, job_role, _file)
    except KeyboardInterrupt:
        print("\n\nScheduler stopped.")


if __name__ == "__main__":
    main()

