"""One-time YouTube authorisation. Prints the refresh token to paste into .env.

    python scripts/auth_youtube.py                    # finds the JSON for you
    python scripts/auth_youtube.py path/to/client.json

Reads the OAuth client JSON that Google Cloud gives you on download, so there
is nothing to copy by hand. Looks in this folder, then ~/Downloads, then falls
back to asking. Opens a browser, you pick the Google account that manages the
channel, and it prints a refresh token that does not expire. Run it once.
"""

import glob
import http.server
import json
import os
import socket
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

SCOPES = " ".join([
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
])
PORT = 8765
REDIRECT = "http://localhost:%d" % PORT

code_box = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        code_box["code"] = (params.get("code") or [None])[0]
        code_box["error"] = (params.get("error") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        message = "You can close this tab and go back to the terminal."
        if code_box.get("error"):
            message = "Authorisation was declined: %s" % code_box["error"]
        self.wfile.write(
            ("<body style='font:16px system-ui;padding:40px'>%s</body>" % message).encode()
        )

    def log_message(self, *args):
        pass


def write_env(values, path=".env"):
    """Update the YT_ keys in .env in place, leaving everything else alone.

    Writing straight to the file means the refresh token never has to be
    printed, pasted, or read aloud. It is a live credential; the fewer places
    it lands the better.
    """
    if not os.path.isfile(path):
        return False
    with open(path) as handle:
        lines = handle.read().splitlines()

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

    with open(path, "w") as handle:
        handle.write("\n".join(out) + "\n")
    return True


def _client_name_from_env(path=".env"):
    """The client name the dashboard is configured for, used as a sanity check."""
    for source in (path, ".env.example"):
        if not os.path.isfile(source):
            continue
        for line in open(source):
            if line.startswith("CLIENT_NAME="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value
    return "Noble Key Supply"


def find_client_json(explicit=None):
    """Locate the OAuth client JSON Google Cloud hands you on download."""
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    patterns = [
        "client_secret*.json",
        os.path.expanduser("~/Downloads/client_secret*.json"),
        os.path.expanduser("~/Desktop/client_secret*.json"),
    ]
    found = []
    for pattern in patterns:
        found.extend(glob.glob(pattern))
    if not found:
        return None
    # Most recent download wins, in case an earlier one was superseded.
    return max(found, key=os.path.getmtime)


def read_client_json(path):
    """Both 'installed' (desktop) and 'web' clients carry the same two fields."""
    try:
        with open(path) as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        print("Could not read %s: %s" % (path, exc))
        return None, None
    block = payload.get("installed") or payload.get("web") or {}
    client_id = block.get("client_id")
    client_secret = block.get("client_secret")
    if not client_id or not client_secret:
        print("%s has no client_id/client_secret. Is it the right file?" % path)
        return None, None
    if "web" in payload and "installed" not in payload:
        print("\nHeads up: this is a Web application client, not a Desktop one.")
        print("Add %s to its Authorized redirect URIs, or create a Desktop client." % REDIRECT)
    return client_id, client_secret


def main():
    print("\nYouTube authorisation\n" + "-" * 40)

    explicit = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else None
    path = find_client_json(explicit)
    client_id = client_secret = None

    if path:
        client_id, client_secret = read_client_json(path)
        if client_id:
            print("Using %s" % path)
            print("Client ID: %s\n" % (client_id[:28] + "..."))

    if not client_id:
        if not path:
            print("No client_secret*.json found here or in ~/Downloads.")
        print("Falling back to manual entry.\n")
        client_id = input("Paste your OAuth Client ID:     ").strip()
        client_secret = input("Paste your OAuth Client Secret: ").strip()
    if not client_id or not client_secret:
        print("Both are required. Stopping.")
        return 1

    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            print("Port %d is busy. Close whatever is using it and retry." % PORT)
            return 1

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
    })

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    print("\nOpening your browser.")
    print("Sign in as the account that MANAGES the channel, and if Google offers")
    print("a list, choose the Noble Key Supply channel rather than your own.\n")
    print("If the browser does not open, paste this in yourself:\n%s\n" % auth_url)
    webbrowser.open(auth_url)

    for _ in range(600):
        if code_box.get("code") or code_box.get("error"):
            break
        threading.Event().wait(0.5)

    if code_box.get("error") or not code_box.get("code"):
        print("No authorisation received. Nothing was saved.")
        return 1

    body = urllib.parse.urlencode({
        "code": code_box["code"],
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT,
        "grant_type": "authorization_code",
    }).encode()
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request) as response:
        payload = json.load(response)

    refresh = payload.get("refresh_token")
    if not refresh:
        print("Google did not return a refresh token. Revoke the app's access at")
        print("myaccount.google.com/permissions and run this again.")
        return 1

    channel_id = ""
    channel_title = ""
    try:
        channel_request = urllib.request.Request(
            "https://www.googleapis.com/youtube/v3/channels?part=id,snippet&mine=true",
            headers={"Authorization": "Bearer %s" % payload["access_token"]},
        )
        with urllib.request.urlopen(channel_request) as response:
            items = json.load(response).get("items") or []
        if items:
            channel_id = items[0]["id"]
            channel_title = items[0]["snippet"]["title"]
            print("\nSigned in to channel: %s" % channel_title)
    except Exception:
        pass

    # Google returns whichever channel the chosen account IS, not the ones it
    # manages. Picking the personal account at the chooser silently authorises
    # the wrong channel, and the dashboard would then report the agency's own
    # numbers under the client's name. Check rather than trust the operator to
    # notice one line of output.
    expected = os.environ.get("CLIENT_NAME", "").strip() or _client_name_from_env()
    if expected and channel_title:
        words = [w for w in expected.lower().split() if len(w) > 2]
        if not any(w in channel_title.lower() for w in words):
            print("\n" + "!" * 60)
            print("STOP. That channel does not look like %r." % expected)
            print("You authorised: %s" % channel_title)
            print("")
            print("At the Google account chooser you have to pick the CHANNEL,")
            print("not just the account. If the client's channel is a Brand")
            print("Account you manage, it appears as a second choice after you")
            print("pick your login.")
            print("")
            print("Nothing was saved. Re-run and choose the client's channel.")
            print("!" * 60 + "\n")
            return 2

    values = {
        "YT_CLIENT_ID": client_id,
        "YT_CLIENT_SECRET": client_secret,
        "YT_REFRESH_TOKEN": refresh,
        "YT_CHANNEL_ID": channel_id or "",
    }

    if "--print" in sys.argv:
        print("\n" + "=" * 60)
        for key, value in values.items():
            print("%s=%s" % (key, value))
        print("=" * 60 + "\n")
        return 0

    if write_env(values):
        print("\n" + "=" * 60)
        print("Written to .env. The refresh token was not printed.")
        print("  YT_CHANNEL_ID=%s" % (channel_id or "NOT FOUND - set this by hand"))
        print("  YT_REFRESH_TOKEN=<%d characters, saved>" % len(refresh))
        print("=" * 60)
        print("\nNext:  python -m app.sync --days 28 --only youtube_organic\n")
        if not channel_id:
            return 1
        return 0

    print("No .env file found. Re-run with --print to show the values instead.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
