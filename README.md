# igtracker

Tracks every Instagram post at 6, 12, 24, 48 and 168 hours after it is published. Each post is compared with earlier posts of the same format (Reel, carousel, image) at the same age. If a post is far above or below the usual numbers at 12 hours, an email goes out. The results are shown in a Looker Studio dashboard.

## Architecture

```
GitHub Actions (private repo)                 every 30 min, or "Run workflow" by hand
  python -m igtracker run
    │ 1. list posts, fetch insights  ──►  Instagram API (production)  or  demo/account.json (demo)
    │ 2. save snapshots, comparisons ──►  Supabase Postgres
    │ 3. alert at 12 h               ──►  email (SMTP), or an .eml file in the run's "outbox" download
                                                │
Looker Studio  ◄── reads two SQL views ─────────┘  (read-only user dashboard_reader)
```

| Piece | Job |
|---|---|
| `igtracker/sources.py` | Where posts come from. `InstagramSource` is the API client; its transport is either HTTP (Meta) or a file (demo). `FakeSource` invents posts (`SOURCE=fake`) and seeds the demo file. |
| `igtracker/pipeline.py` | One run: detect new posts, decide which checkpoints are due, take snapshots, compare, alert. |
| `igtracker/stats.py` | Engagement rate, median, MAD, robust z-score, percentile. Pure functions, no database. |
| `igtracker/db.py`, `schema.sql` | The SQL. Tables `posts`, `snapshots`, `comparisons`, `alerts`, `kv`; views `v_post_report` and `v_format_summary` for the dashboard. |
| `igtracker/alerts.py` | Builds the email and sends it, or writes it to `outbox/`. |
| `.github/workflows/track.yml` | Runs the tracker. |

## What happens in one run

1. GitHub starts a fresh Linux machine, installs the code and runs `init-db`, which creates any missing tables.
2. It asks the source for the latest 25 posts and saves new ones to `posts`. Stories are skipped.
3. For each post under about 7 days old, it computes the age from the post's own timestamp. When a checkpoint has passed and is not stored, it fetches the numbers and saves a snapshot with the real age. Only the latest missed checkpoint is taken: Instagram reports current totals, so a "6 h" row taken at 30 h would hold 30 h numbers.
4. Each snapshot is compared with earlier posts, and the result is frozen in `comparisons`.
5. At 12 h, if views or engagement rate is far from normal, it saves an alert and then sends the email. A mail failure loses no data and turns the run red, so GitHub notifies the repo owner.

Inserts skip existing rows and a `concurrency` lock prevents overlapping runs, so a second run in a row does nothing.

## Why these tools

- **GitHub Actions:** free (about 1,440 of the 2,000 monthly minutes of a private repo), no server to maintain, and it does not depend on anyone's own computer. The repo is private because GitHub disables schedules in public repos after 60 days without commits.
- **Supabase Postgres** rather than a CSV or Google Sheet: the database enforces one snapshot per post and checkpoint and one alert per post, nobody edits it by accident, and Looker Studio connects to it directly. The free 500 MB covers decades at about 2 MB a year. Connections go through the Session pooler, because the direct connection is IPv6-only and GitHub cannot reach it.
- **Looker Studio**, because its readers need no technical setup: they open a link, filter, and export tables to CSV. It logs in as `dashboard_reader`, which can only read the two views. Supabase's web API is locked out.
- **Polling:** Instagram sends no notification when your own account publishes, so every run asks.

## How "normal" is decided

- **Engagement rate (ER)** = (likes + comments + shares + saves) / reach: how much the people who saw the post interacted with it.
- **Like with like.** A Reel at 12 h is compared only with earlier Reels at 12 h: the latest 20 on-time snapshots from the last 180 days.
- **Median, not mean.** Instagram numbers are skewed: one viral Reel would pull the mean up and make every normal post look bad.
- **Robust z-score.** The distance from the median, on a log scale, divided by the typical spread of that format (MAD). z ≥ 2 means "way above", z ≤ −2 "way below"..
- **Safety rules.** A format alerts only once it has 5 earlier posts. Before that, the dashboard compares with all formats, but sends no email, since a normal image looks far below a Reel. A snapshot more than 1 h late is marked `late`: shown, but never a baseline and never an alert.

## Demo mode (`SOURCE=file`)

`demo/account.json` plays the Instagram account. The tracker reads it through the same `InstagramSource` that calls Meta; `FileTransport` answers the API's URLs in Meta's response format. Posts with a future timestamp do not exist yet, and insights grow with age towards the totals in `performance`.

1. To publish, edit the file on GitHub: add a post to `media` with a timestamp 12 h 6 min ago (PowerShell: `[DateTime]::UtcNow.AddHours(-12.1).ToString("yyyy-MM-ddTHH:mm:ss+0000")`), and its weekly totals to `performance` under the same new id. About 40,000 views and 26,000 reach make a Reel alert.
2. Within the hour, run *Actions → track → Run workflow*, then refresh the data in Looker Studio.

Keep the posts of the last 8 days in the file: runs still ask for them, and a missing one fails the run.

## Production with the Meta Graph API

Production uses the *Instagram API with Facebook Login* (`graph.facebook.com`) with a system user token. A system user belongs to the organisation's Meta Business portfolio, not to a person, and its token can be set to never expire: the tracker needs no renewals and keeps working when members leave. Each run makes one call for the post list, one per due checkpoint and one for the follower count: a handful of calls against a limit of about 200 an hour.

| Call | Gives |
|---|---|
| `GET /{ig-user-id}/media?fields=id,caption,media_type,media_product_type,timestamp,permalink` | The latest posts, with the publish time used for every checkpoint |
| `GET /{media-id}/insights?metric=views,reach,likes,comments,shares,saved` | The post's current totals |
| `GET /{ig-user-id}?fields=followers_count` | Followers, for ER by followers |

Switching over:

1. The account owner switches the account to **Professional** (free, in the app) and links it to a Facebook Page. Insights exist only for posts published after the switch.
2. Create a Business portfolio that owns the Page, the Instagram account and a new app at developers.facebook.com. Check in Meta's documentation whether the app needs review at this access level.
3. In the portfolio, create a system user, give it access to the account and the app with the permissions `instagram_basic` and `instagram_manage_insights`, and generate a token that never expires. Save it as the repository secret `IG_TOKEN`. Change `SOURCE=instagram`.

### API limitations

| Limitation | How the tracker deals with it |
|---|---|
| Insights are current totals only; the API cannot say what a post had at 12 h. | Snapshots are taken at the checkpoints and stored. A missed checkpoint cannot be recovered, so it stays empty rather than holding later numbers. Runs must be frequent and punctual. |
| No notification when the account publishes. | Every run polls the post list. A new post is found within one run interval. |
| Meta renames and retires metrics, and each API version is supported for about two years. | Metric names live in one constant (`METRICS` in `sources.py`) and the version in `API`. Check both against Meta's changelog when upgrading. |
| Calls are rate-limited. | A run makes one to a few calls, far below the limits. |
## Run locally

Install with `pip install -e .`, keeping the Windows venv at a short path (psycopg fails to load from long folders). With `DATABASE_URL` and `SOURCE` set, `python -m igtracker simulate --days 120` builds the history and `python -m igtracker report <post_id>` prints one post's checkpoints.
