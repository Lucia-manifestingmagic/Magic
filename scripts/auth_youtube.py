"""One-time YouTube authorisation. Prints the refresh token to paste into .env.

    python scripts/auth_youtube.py

Opens a browser, asks you to pick the Google account that manages Noble Key
Supply's channel, and prints a refresh token that does not expire. Run it once.
"""

import http.server
import json
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


def main():
    print("\nYouTube authorisation\n" + "-" * 40)
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
    try:
        channel_request = urllib.request.Request(
            "https://www.googleapis.com/youtube/v3/channels?part=id,snippet&mine=true",
            headers={"Authorization": "Bearer %s" % payload["access_token"]},
        )
        with urllib.request.urlopen(channel_request) as response:
            items = json.load(response).get("items") or []
        if items:
            channel_id = items[0]["id"]
            print("\nSigned in to channel: %s" % items[0]["snippet"]["title"])
    except Exception:
        pass

    print("\n" + "=" * 60)
    print("Paste these four lines into your .env file:\n")
    print("YT_CLIENT_ID=%s" % client_id)
    print("YT_CLIENT_SECRET=%s" % client_secret)
    print("YT_REFRESH_TOKEN=%s" % refresh)
    print("YT_CHANNEL_ID=%s" % (channel_id or "<paste the channel ID here>"))
    print("=" * 60 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
