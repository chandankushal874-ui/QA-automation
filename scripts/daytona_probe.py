import urllib.request
import json

KEY = "dtn_d3df76816ba0a9b4d2d90c05f9c2cb817fc4d72aee87209a5332c7c097c2145a"
BASE = "https://app.daytona.io/api"
paths = [
    "/regions",
    "/organization/regions",
    "/organization",
    "/orgs",
    "/sandbox/regions",
    "/default-region",
    "/profile",
    "/user",
    "/me",
    "/account",
    "/workspaces",
    "/snapshots",
]
for p in paths:
    url = BASE + p
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {KEY}"})
    try:
        body = urllib.request.urlopen(req, timeout=20).read()[:400].decode(errors="replace")
        print(f"OK  {p}: {body}")
    except urllib.error.HTTPError as e:
        print(f"{e.code} {p}: {e.reason}")
    except Exception as e:
        print(f"ERR {p}: {e}")