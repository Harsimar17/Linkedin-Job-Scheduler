# LinkedIn Job Scheduler

An automated LinkedIn job fetcher that runs every 15 minutes, collecting freshly posted jobs with low applicant competition and saving them to a CSV file.

## What It Does

- Fetches LinkedIn job listings posted within the **past hour** on every run
- Filters for **low-competition jobs** (fewer than 100 applicants)
- Further filters for **recently posted** jobs (posted minutes or hours ago, not days)
- Fetches the **full job description** for each qualifying job
- Appends results to a **CSV file** that grows over time
- Runs on a **15-minute schedule** aligned to the clock (:00, :15, :30, :45)

## Requirements

```
pip install requests beautifulsoup4
```

## Setup

### 1. Get your `li_at` cookie

The tool uses LinkedIn's authenticated session to access job data.

1. Log into [linkedin.com](https://www.linkedin.com) in your browser
2. Open DevTools → Application → Cookies → `https://www.linkedin.com`
3. Copy the value of the `li_at` cookie
4. Paste it into the `LI_AT` variable at the top of `LinkedIn_Job_Scheduler.py`

> **Note:** The `li_at` cookie expires periodically. If you see a login redirect warning, refresh it.

### 2. Run the script

```
python LinkedIn_Job_Scheduler.py
```

You will be prompted for:

| Prompt | Example |
|--------|---------|
| Location | `Toronto, Canada` |
| Job role | `Software Engineer` |
| Output CSV file | *(press Enter for default `linkedin_jobs_hourly.csv`)* |

Press **Ctrl+C** to stop the scheduler.

## Output

Results are appended to the CSV file after each run. Columns:

| Column | Description |
|--------|-------------|
| `title` | Job title |
| `company` | Company name |
| `location` | Job location |
| `date_posted` | Date the job was listed |
| `applicants` | Applicant count or click-apply count |
| `job_url` | Direct link to the LinkedIn job posting |
| `description` | Full job description text |
| `fetched_at` | Timestamp of the scheduler run that found this job |

## Configuration

Edit these constants at the top of the script to tune behavior:

| Constant | Default | Description |
|----------|---------|-------------|
| `JOB_COUNT` | `10` | Max jobs to collect per run |
| `FETCH_WINDOW` | `3600` | Look-back window in seconds (1 hour) |
| `OUTPUT_FILE` | `linkedin_jobs_hourly.csv` | Default output file name |
| `INTERVAL_MINUTES` | `15` | How often the scheduler fires |

## How It Works

1. **Scheduler** waits until the next 15-minute clock mark, then fires
2. **Search** hits LinkedIn's job search with a 1-hour time filter (`f_TPR`)
3. **Parsing** extracts job data from embedded JSON blobs, falling back to HTML parsing
4. **Filtering** keeps only jobs with < 100 applicants posted within hours (not days)
5. **Description fetch** tries LinkedIn's Voyager API first, then the guest API
6. **CSV append** writes qualifying jobs without overwriting previous runs

## Notes

- The tool respects LinkedIn's redirect signals — if your `li_at` cookie expires, it will warn you and stop rather than loop infinitely
- Jobs are deduplicated within each run by URL
- The CSV grows indefinitely; archive or truncate it manually as needed
