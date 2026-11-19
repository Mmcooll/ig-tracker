import argparse
import os
from datetime import datetime, timedelta, timezone

from . import alerts, db, pipeline
from .sources import FakeSource, FileTransport, HttpTransport, InstagramSource, write_demo_account

DEMO_ACCOUNT = "demo/account.json"


def source_from_env():
    name = os.environ.get("SOURCE", "fake")
    if name == "fake":
        return FakeSource()
    if name == "file":
        return InstagramSource(FileTransport(os.environ.get("DEMO_ACCOUNT", DEMO_ACCOUNT)))
    if name == "instagram":
        # shortcut: no automatic token refresh yet; replace the IG_TOKEN secret by hand every 60 days
        return InstagramSource(HttpTransport(os.environ["IG_TOKEN"]))
    raise SystemExit(f"unknown SOURCE={name} (use fake, file or instagram)")


def main():
    p = argparse.ArgumentParser(prog="igtracker")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="create/update tables and views")
    sub.add_parser("run", help="one hourly pass")
    s = sub.add_parser("simulate", help="replay fake history")
    s.add_argument("--days", type=int, default=120)
    r = sub.add_parser("report", help="print a post's checkpoint table")
    r.add_argument("post_id")
    d = sub.add_parser("demo-account", help="write a fake account file for SOURCE=file")
    d.add_argument("--days", type=int, default=120)
    d.add_argument("--out", default=DEMO_ACCOUNT)
    d.add_argument("--force", action="store_true", help="overwrite an existing file (and the posts added to it)")
    a = p.parse_args()

    now = datetime.now(timezone.utc)
    if a.cmd == "demo-account":
        if os.path.exists(a.out) and not a.force:
            raise SystemExit(f"{a.out} exists; pass --force to overwrite it")
        print(f"{write_demo_account(a.out, now, a.days)} posts written to {a.out}")
        return
    conn = db.connect(os.environ["DATABASE_URL"])
    if a.cmd == "init-db":
        db.init_db(conn)
    elif a.cmd == "run":
        print(f"{pipeline.run(conn, source_from_env(), now, alerts.send_alert)} snapshot(s) taken")
    elif a.cmd == "simulate":
        n = pipeline.simulate(conn, source_from_env(), now - timedelta(days=a.days), now, alerts.send_alert)
        print(f"{n} snapshot(s) taken")
    else:
        rows = db.report_rows(conn, a.post_id)
        print(alerts.render_table(rows) if rows else f"no snapshots for {a.post_id}")


if __name__ == "__main__":
    main()
