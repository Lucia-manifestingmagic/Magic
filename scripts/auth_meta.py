"""Set up the Meta connection without a System User.

    python scripts/auth_meta.py

Creating a System User needs Business Verification, which many portfolios do
not have. This takes the ordinary route instead:

    your login -> short-lived user token -> long-lived (60 days) -> Page token

The Page token is the useful one: Meta does not expire Page tokens derived
from a long-lived user token, and it reads both the Facebook Page and the
Instagram business account linked to it. The user token is kept as well,
because ad account reads need it and Page tokens cannot do that.

Nothing is echoed as you type and no token is ever printed.
"""

import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

VERSION = os.environ.get("META_API_VERSION", "v21.0")
API = "https://graph.facebook.com/%s" % VERSION

NEEDED = {
    "ads_read": "Meta ads spend and conversions",
    "instagram_basic": "Instagram account and media",
    "instagram_manage_insights": "Instagram reach, views, saves",
    "pages_read_engagement": "Facebook Page insights",
    "pages_show_list": "listing the Pages this token can see",
}


def call(path, token, params=None):
    query = dict(params or {})
    query["access_token"] = token
    url = "%s/%s?%s" % (API, path.lstrip("/"), urllib.parse.urlencode(query))
    try:
        with urllib.request.urlopen(url) as response:
            return json.load(response), None
    except urllib.error.HTTPError as exc:
        try:
            message = json.load(exc).get("error", {}).get("message", "")
        except Exception:
            message = exc.reason
        return None, "HTTP %s: %s" % (exc.code, message)
    except Exception as exc:
        return None, str(exc)


def write_env(values, path=".env"):
    if not os.path.isfile(path):
        return False
    lines = open(path).read().splitlines()
    remaining = dict(values)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in remaining:
            out.append("%s=%s" % (key, remaining.pop(key)))
        else:
            out.append(line)
    for key, value in remaining.items():
        out.append("%s=%s" % (key, value))
    open(path, "w").write("\n".join(out) + "\n")
    return True


def env_value(key, path=".env"):
    if not os.path.isfile(path):
        return ""
    for line in open(path):
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return ""


def main():
    print("\nMeta connection\n" + "-" * 46)

    print("Paste the short-lived User token from the Graph API Explorer.")
    print("Nothing will appear as you type.\n")
    token = getpass.getpass("User token: ").strip()
    if not token:
        print("No token given. Nothing saved.")
        return 1

    app_id = env_value("META_APP_ID") or input("App ID:     ").strip()
    app_secret = env_value("META_APP_SECRET")
    if not app_secret:
        app_secret = getpass.getpass("App secret (hidden): ").strip()
    if not (app_id and app_secret):
        print("App ID and secret are both needed to make the token long-lived.")
        return 1

    # --- short-lived -> long-lived ---------------------------------------
    exchanged, error = call("oauth/access_token", token, {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": token,
    })
    if error:
        print("\nCould not extend the token: %s" % error)
        return 1
    long_lived = (exchanged or {}).get("access_token")
    if not long_lived:
        print("\nMeta did not return a long-lived token.")
        return 1
    print("\nToken extended to long-lived.")
    token = long_lived

    # --- scopes -----------------------------------------------------------
    debug, error = call("debug_token", token, {"input_token": token})
    if error:
        print("\nThat token was rejected: %s" % error)
        print("Most often it is expired, or was generated without the scopes below.")
        return 1

    data = (debug or {}).get("data", {})
    scopes = set(data.get("scopes") or [])
    expires = data.get("data_access_expires_at") or data.get("expires_at")

    print("\nScopes on this token:")
    missing = []
    for scope, why in NEEDED.items():
        ok = scope in scopes
        print("  [%s] %-28s %s" % ("x" if ok else " ", scope, why))
        if not ok:
            missing.append(scope)
    if expires == 0:
        print("\nExpiry: never")
    elif expires:
        import datetime as dt
        print("\nExpiry: %s" % dt.datetime.fromtimestamp(expires).strftime("%d %b %Y"))

    if missing:
        print("\nMissing: %s" % ", ".join(missing))
        print("Regenerate the token with those boxes ticked, then run this again.")
        print("Nothing was saved.")
        return 2

    # --- what can this token see? ----------------------------------------
    found = {"META_ACCESS_TOKEN": token, "META_APP_ID": app_id, "META_APP_SECRET": app_secret}

    pages, error = call("me/accounts", token, {
        "fields": "id,name,access_token,instagram_business_account{id,username}"})
    page_list = (pages or {}).get("data", []) if not error else []
    if error:
        print("\nCould not list Pages: %s" % error)

    print("\nPages this token can read:")
    if not page_list:
        print("  none. The Page has to be assigned to the System User as an asset,")
        print("  not merely present in the business portfolio.")
    for page in page_list:
        ig = page.get("instagram_business_account") or {}
        print("  %-34s page id %s%s" % (
            page.get("name", "?")[:34], page.get("id"),
            "   IG @%s" % ig.get("username") if ig.get("username") else "   (no linked Instagram)",
        ))

    chosen = _pick(page_list, "Noble Key Supply")
    if chosen:
        found["FB_PAGE_ID"] = chosen.get("id")
        ig = chosen.get("instagram_business_account") or {}
        if ig.get("id"):
            found["IG_USER_ID"] = ig["id"]
        # Page tokens derived from a long-lived user token do not expire, so
        # organic keeps working after the 60-day user token lapses.
        if chosen.get("access_token"):
            found["META_PAGE_TOKEN"] = chosen["access_token"]
            print("\nPage token obtained (these do not expire).")
        else:
            print("\nNo Page token returned. pages_show_list may be missing.")

    accounts, error = call("me/adaccounts", token, {"fields": "id,name,account_status"})
    ad_list = (accounts or {}).get("data", []) if not error else []
    print("\nAd accounts this token can read:")
    if not ad_list:
        print("  none found (fine if you are only doing organic for now)")
    for account in ad_list:
        print("  %-34s %s" % (account.get("name", "?")[:34], account.get("id")))
    ad = _pick(ad_list, "Noble Key Supply")
    if ad:
        found["META_AD_ACCOUNT_ID"] = ad.get("id")

    if not write_env(found):
        print("\nNo .env file found. Nothing saved.")
        return 1

    print("\n" + "=" * 60)
    print("Written to .env (no tokens printed):")
    for key in ("FB_PAGE_ID", "IG_USER_ID", "META_AD_ACCOUNT_ID"):
        print("  %-20s %s" % (key, found.get(key, "NOT FOUND - set by hand")))
    print("  %-20s %s" % ("META_PAGE_TOKEN", "saved, does not expire"
                          if found.get("META_PAGE_TOKEN") else "NOT OBTAINED"))
    print("=" * 60)
    print("\nNext:  python -m app.sync --days 28 --only meta_organic\n")
    return 0


def _pick(items, name_hint):
    """Prefer the asset whose name matches the client; fall back to the only one."""
    for item in items:
        if name_hint.lower() in (item.get("name") or "").lower():
            return item
    return items[0] if len(items) == 1 else None


if __name__ == "__main__":
    sys.exit(main())
