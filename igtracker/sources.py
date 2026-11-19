"""Data sources. Every source offers the same three methods:
  list_media(now, limit=25) -> newest-first dicts: id, caption, media_type, media_product_type, timestamp, permalink
  insights(media_id, now)   -> dict: views, reach, likes, comments, shares, saved
  followers(now)            -> int

InstagramSource is the real API client. Where its JSON comes from is a transport:
  HttpTransport -> graph.instagram.com (production)
  FileTransport -> a JSON file shaped like the account (demo/account.json), answering the same URLs
FakeSource invents posts from a seed (SOURCE=fake) and seeds the demo account file.
"""
import base64
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from urllib.request import urlopen

API = "https://graph.instagram.com/v23.0"
MEDIA_FIELDS = "id,caption,media_type,media_product_type,timestamp,permalink"
METRICS = "views,reach,likes,comments,shares,saved"
PAGE_SIZE = 25  # Meta's default page size for /me/media


class InstagramSource:
    """Instagram API with Instagram Login. `transport.get(url, now)` returns the parsed JSON body."""

    def __init__(self, transport):
        self.transport = transport

    def list_media(self, now, limit=PAGE_SIZE):
        """Newest first. limit=None follows paging.next to the end (used by simulate)."""
        url = f"{API}/me/media?" + urlencode(dict(fields=MEDIA_FIELDS, limit=limit or PAGE_SIZE))
        out = []
        while url:
            page = self.transport.get(url, now)
            out += page["data"]
            url = page.get("paging", {}).get("next") if limit is None else None
        return out

    def insights(self, media_id, now):
        data = self.transport.get(f"{API}/{media_id}/insights?" + urlencode(dict(metric=METRICS)), now)["data"]
        # Lifetime media metrics come as values[0].value; some newer metrics use total_value.value instead.
        return {m["name"]: m["values"][0]["value"] if "values" in m else m["total_value"]["value"] for m in data}

    def followers(self, now):
        return self.transport.get(f"{API}/me?" + urlencode(dict(fields="followers_count")), now)["followers_count"]


class HttpTransport:
    def __init__(self, token):
        self.token = token

    def get(self, url, now):
        if "access_token=" not in url:  # paging.next URLs from Meta already carry it
            url += "&access_token=" + quote(self.token)
        with urlopen(url, timeout=30) as r:
            return json.load(r)


def _ts(m):
    return datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")


def _cursor(n):
    return base64.b64encode(str(n).encode()).decode()


class FileTransport:
    """Answers Instagram API URLs from a JSON account file, in Meta's response format.

    The file has "user_id", "followers_count", "media" (entries exactly as /me/media returns them) and
    "performance" (each post's totals after a week). Insights grow towards those totals with the post's age,
    and posts with a timestamp after `now` do not exist yet. The file is reread on every call, like a live API.
    """

    def __init__(self, path):
        self.path = Path(path)

    def get(self, url, now):
        acc = json.loads(self.path.read_text(encoding="utf-8"))
        u = urlsplit(url)
        q = dict(parse_qsl(u.query))
        parts = u.path.strip("/").split("/")[1:]  # drop the API version
        me = parts[0] in ("me", acc["user_id"])
        if me and len(parts) == 1:
            return {"followers_count": acc["followers_count"], "id": acc["user_id"]}
        if me and parts[1:] == ["media"]:
            media = sorted((m for m in acc["media"] if _ts(m) <= now), key=_ts, reverse=True)
            start = int(base64.b64decode(q["after"])) if "after" in q else 0
            end = start + int(q.get("limit", PAGE_SIZE))
            fields = q["fields"].split(",")
            body = {"data": [{k: m[k] for k in fields if k in m} for m in media[start:end]]}
            if body["data"]:
                body["paging"] = {"cursors": {"before": _cursor(start), "after": _cursor(end)}}
                if end < len(media):
                    body["paging"]["next"] = f"{API}/{acc['user_id']}/media?" + urlencode({**q, "after": _cursor(end)})
            return body
        m = next((m for m in acc["media"] if m["id"] == parts[0] and _ts(m) <= now), None)
        if m is None or parts[1:] != ["insights"]:
            raise LookupError(f"Unsupported get request. Object with ID '{parts[0]}' does not exist ({url})")
        tau = 30 if m["media_product_type"] == "REELS" else 15  # hours; Reels keep growing longer
        grown = 1 - math.exp(-((now - _ts(m)).total_seconds() / 3600) / tau)
        final = acc["performance"][m["id"]]
        return {"data": [{"name": n, "period": "lifetime", "values": [{"value": round(final[n] * grown)}],
                          "id": f"{m['id']}/insights/{n}/lifetime"} for n in q["metric"].split(",")]}


def write_demo_account(path, now, days, seed=7):
    """Export FakeSource's posts of the last `days` days as an account file for FileTransport."""
    fake = FakeSource(seed)
    media, performance = [], {}
    for p in reversed(fake.posts.values()):
        if not now - timedelta(days=days) <= p["ts"] <= now:
            continue
        n = int(p["id"][4:])
        mid = str(17_900_000_000_000_000 + n)  # Meta media ids are long numeric strings
        api = fake._api(p)
        kind = "reel" if api["media_product_type"] == "REELS" else "p"
        media.append({**api, "id": mid, "caption": f"Demo {p['format'].lower()} #{n}",
                      "permalink": f"https://www.instagram.com/{kind}/demo{n:04d}/"})
        performance[mid] = fake.insights(p["id"], p["ts"] + timedelta(days=60))
    acc = dict(user_id="17841400000000000", username="instagram_tracker_demo", followers_count=fake.followers(now),
               media=media, performance=performance)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(acc, indent=2) + "\n", encoding="utf-8")
    return len(media)

# final views, reach/views, base ER (interactions/reach), growth time constant in hours
PROFILE = {
    "REEL": (1500, 0.65, 0.04, 30),
    "CAROUSEL": (600, 0.55, 0.07, 15),
    "IMAGE": (400, 0.55, 0.05, 15),
}
API_TYPE = {"REEL": ("VIDEO", "REELS"), "CAROUSEL": ("CAROUSEL_ALBUM", "FEED"), "IMAGE": ("IMAGE", "FEED")}


class FakeSource:
    """Deterministic stand-in for the Instagram API: same seed and time give the same numbers."""

    def __init__(self, seed=7, start=datetime(2025, 1, 1, tzinfo=timezone.utc),
                 end=datetime(2028, 1, 1, tzinfo=timezone.utc)):
        rng = random.Random(seed)
        self.start = start
        self.posts = {}
        t = start
        while t < end:
            t += timedelta(minutes=rng.randint(48 * 60, 96 * 60))
            fmt = rng.choices(list(PROFILE), weights=[40, 35, 25])[0]
            quality = rng.lognormvariate(0, 0.6)
            r = rng.random()
            quality *= 4 if r < 0.05 else 0.25 if r < 0.10 else 1  # a few real outliers so alerts fire
            pid = f"fake{len(self.posts):04d}"
            self.posts[pid] = dict(id=pid, format=fmt, ts=t.replace(second=0, microsecond=0), quality=quality)

    def list_media(self, now, limit=25):
        out = [self._api(p) for p in reversed(self.posts.values()) if p["ts"] <= now]
        return out[:limit] if limit else out

    def insights(self, media_id, now):
        p = self.posts[media_id]
        views0, reach_ratio, er0, tau = PROFILE[p["format"]]
        age = max((now - p["ts"]).total_seconds() / 3600, 0)
        views = views0 * p["quality"] * (1 - math.exp(-age / tau))
        reach = views * reach_ratio
        inter = reach * er0 * math.sqrt(p["quality"])
        return dict(views=round(views), reach=round(reach), likes=round(inter * 0.75),
                    comments=round(inter * 0.05), shares=round(inter * 0.08), saved=round(inter * 0.12))

    def followers(self, now):
        return 1200 + 2 * (now - self.start).days

    def _api(self, p):
        media_type, product = API_TYPE[p["format"]]
        return dict(id=p["id"], caption=f"Fake {p['format'].lower()} post {p['id']}",
                    media_type=media_type, media_product_type=product,
                    timestamp=p["ts"].strftime("%Y-%m-%dT%H:%M:%S+0000"),
                    permalink=f"https://www.instagram.com/p/{p['id']}/")
