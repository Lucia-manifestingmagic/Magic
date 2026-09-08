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
import http.server
import json
import os
import sys
import threading
import webbrowser
import urllib.error
import urllib.parse
import urllib.request

VERSION = os.environ.get("META_API_VERSION", "v21.0")
API = "https://graph.facebook.com/%s" % VERSION

CLIENT = os.environ.get("CLIENT_NAME", "").strip() or "Noble Key Supply"

NEEDED = {
    "ads_read": "Meta ads spend and conversions",
    "instagram_basic": "Instagram account, media, likes and comments",
    "instagram_manage_insights": "Instagram reach, views, saves (optional)",
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


def store_app_credentials():
    """Capture just the App ID and secret, so the secret is never on screen."""
    print("\nMeta app credentials\n" + "-" * 46)
    print("From developers.facebook.com > App settings > Basic.")
    print("The secret is typed blind: nothing appears, no dots, no cursor move.")
    print("Paste it and press Enter.\n")

    app_id = input("App ID (safe to show): ").strip()
    if not app_id.isdigit():
        print("\nThat does not look like an App ID. It is a long number, digits only.")
        return 1

    secret = getpass.getpass("App secret (hidden):  ").strip()
    if len(secret) < 24:
        print("\nThat secret looks too short. Did the paste land? Nothing saved.")
        return 1

    if not write_env({"META_APP_ID": app_id, "META_APP_SECRET": secret}):
        print("\nNo .env file found. Nothing saved.")
        return 1

    print("\n" + "=" * 56)
    print("Saved to .env:")
    print("  META_APP_ID      %s" % app_id)
    print("  META_APP_SECRET  %d characters, hidden" % len(secret))
    print("=" * 56)
    print("\nNext: generate a User token in the Graph API Explorer,")
    print("then run  make auth-meta\n")
    return 0


PORT = 8765
REDIRECT = "http://localhost:%d/" % PORT
# instagram_manage_insights is a Business-app permission. On an app whose type
# was never set to Business, Meta lists it as "Ready for testing" but rejects it
# at the OAuth dialog with "Invalid Scopes". Asking for it there fails the whole
# request, taking the working scopes down with it, so it is requested only when
# explicitly enabled.
BASE_SCOPES = ["ads_read", "instagram_basic", "pages_read_engagement", "pages_show_list"]
OPTIONAL_SCOPES = ["instagram_manage_insights"]


def _scopes():
    override = os.environ.get("META_SCOPES", "").strip() or _env_scopes()
    if override:
        return [s.strip() for s in override.split(",") if s.strip()]
    return list(BASE_SCOPES)


def _env_scopes():
    return env_value("META_SCOPES")

_code = {}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _code["code"] = (params.get("code") or [None])[0]
        _code["error"] = (params.get("error_description") or params.get("error") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        note = "You can close this tab and return to the terminal."
        if _code.get("error"):
            note = "Authorisation failed: %s" % _code["error"]
        self.wfile.write(("<body style='font:16px system-ui;padding:40px'>%s</body>" % note).encode())

    def log_message(self, *args):
        pass


def browser_login(app_id, app_secret):
    """Request the scopes directly, bypassing the Explorer's permission picker.

    The Explorer only lists a subset of activated permissions, and
    instagram_manage_insights is one it omits. The OAuth dialog accepts any
    scope the app has activated, so we ask for them explicitly.
    """
    import socket
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            print("Port %d is busy. Close whatever is using it and retry." % PORT)
            return None

    dialog = "https://www.facebook.com/v21.0/dialog/oauth?" + urllib.parse.urlencode({
        "client_id": app_id,
        "redirect_uri": REDIRECT,
        "scope": ",".join(_scopes()),
        "response_type": "code",
        # Without this, Facebook reuses the earlier asset selection instead of
        # re-showing the Pages and Instagram chooser. The first attempt granted
        # no Pages, and every retry silently inherited that.
        "auth_type": "rerequest",
    })

    server = http.server.HTTPServer(("127.0.0.1", PORT), _Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    print("Requesting: %s" % ", ".join(_scopes()))
    print("\nOpening Facebook. Approve, and choose Noble Key Supply when asked")
    print("which Pages and Instagram accounts to allow.\n")
    print("If the browser does not open, paste this:\n%s\n" % dialog)
    webbrowser.open(dialog)

    for _ in range(1800):   # 15 minutes
        if _code.get("code") or _code.get("error"):
            break
        threading.Event().wait(0.5)

    if _code.get("error") or not _code.get("code"):
        print("No authorisation received: %s" % (_code.get("error") or "timed out"))
        return None

    exchanged, error = call("oauth/access_token", "", {
        "client_id": app_id, "client_secret": app_secret,
        "redirect_uri": REDIRECT, "code": _code["code"],
    })
    if error:
        print("Could not exchange the code: %s" % error)
        return None
    return (exchanged or {}).get("access_token")


def main():
    if "--app-only" in sys.argv:
        return store_app_credentials()

    print("\nMeta connection\n" + "-" * 46)

    app_id = env_value("META_APP_ID")
    app_secret = env_value("META_APP_SECRET")
    if not (app_id and app_secret):
        print("Run  make auth-meta-app  first to store the App ID and secret.")
        return 1

    token = browser_login(app_id, app_secret)
    if not token:
        return 1
    print("Token received.")

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

    blocking = [m for m in missing if m not in OPTIONAL_SCOPES]
    if blocking:
        print("\nMissing: %s" % ", ".join(blocking))
        print("These are required. Nothing was saved.")
        return 2
    if missing:
        print("\nNot granted (optional): %s" % ", ".join(missing))
        print("Instagram reach, views and saves need instagram_manage_insights,")
        print("which requires a Business-type app. Likes, comments, captions and")
        print("follower counts still work without it.")

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

    chosen = _pick(page_list, CLIENT)
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
    ad = _pick(ad_list, CLIENT)
    if ad:
        found["META_AD_ACCOUNT_ID"] = ad.get("id")
    elif ad_list:
        print("\n  none of these look like %r, so none was saved." % CLIENT)

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
    """Only ever return an asset whose name matches the client.

    The earlier version fell back to "the only one available", which on a
    personal login means the operator's own ad account gets written into the
    client's configuration. Being one asset short is recoverable; reporting the
    agency's own spend as the client's is not.
    """
    words = [w for w in name_hint.lower().split() if len(w) > 2]
    for item in items:
        name = (item.get("name") or "").lower()
        if any(word in name for word in words):
            return item
    return None


if __name__ == "__main__":
    sys.exit(main())
