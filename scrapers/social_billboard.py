"""PlanFind 'billboard' poster: screenshot of the home page + a short caption + the link.
Posts to every platform whose secrets are set; skips the rest. DRY_RUN=1 only takes the screenshot and prints the caption.
Captions rotate by ISO week so the same words are not repeated back to back."""
import io, os, re, sys, json, datetime, urllib.request, urllib.parse, uuid

SITE = os.environ.get("SITE_URL", "https://planfind.co.uk")
DRY = os.environ.get("DRY_RUN", "0").strip() in ("1", "true", "yes")
OUT = os.environ.get("SHOT_FILE", "billboard.png")

OPENERS = [
    "Planning applications near you, in plain English.",
    "Who's applying to build next door?",
    "Extension, new house or change of use on your street?",
    "Know what's happening nearby before the neighbours do.",
    "Thinking of an extension? See what's been approved near you first.",
    "Planning applications without the jargon.",
    "Curious what that scaffolding is for?",
    "Got a planning notice through the door and no idea what it means?",
]
TIPS = [
    "Type your postcode and see what's been applied for.",
    "Free email alerts when a new application goes in near your home.",
    "No account needed. Just search.",
    "Covers England, Scotland, Wales and Northern Ireland.",
    "Bigger applications get a short plain-English summary of what they mean for people nearby.",
    "See whether nearby applications were approved or refused.",
    "Builders, architects and roofers can see how much work is being planned in their patch.",
    "Handy guides explain planning terms like permitted development and prior approval.",
    "Looking for a local professional? You can find one on the site.",
    "Free to use.",
]


def caption() -> str:
    wk = datetime.date.today().isocalendar()[1] + datetime.date.today().year * 53
    # different strides so the opener and the tip pair up differently each week
    return f"{OPENERS[wk % len(OPENERS)]} {TIPS[(wk * 3 + wk // len(TIPS)) % len(TIPS)]}\n{SITE}"


def screenshot() -> bytes:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1200, "height": 630}, device_scale_factor=1)
        pg.goto(SITE, wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(1500)
        for sel in ("button:has-text('Accept')", "button:has-text('Got it')", "button:has-text('Close')"):   # cookie banners
            try:
                pg.locator(sel).first.click(timeout=800)
            except Exception:
                pass
        img = pg.screenshot(type="png")
        b.close()
    open(OUT, "wb").write(img)
    return img


def _req(url, data=None, headers=None, method=None):
    r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read().decode() or "{}")


def post_bluesky(img: bytes, text: str):
    h, pw = os.environ.get("BSKY_HANDLE"), os.environ.get("BSKY_APP_PASSWORD")
    if not (h and pw):
        return "skipped (BSKY_HANDLE / BSKY_APP_PASSWORD not set)"
    base = "https://bsky.social/xrpc/"
    sess = _req(base + "com.atproto.server.createSession", json.dumps({"identifier": h, "password": pw}).encode(), {"Content-Type": "application/json"})
    auth = {"Authorization": "Bearer " + sess["accessJwt"]}
    blob = _req(base + "com.atproto.repo.uploadBlob", img, {**auth, "Content-Type": "image/png"})["blob"]
    tb = text.encode()
    facets = []
    for m in re.finditer(rb"https?://\S+", tb):
        facets.append({"index": {"byteStart": m.start(), "byteEnd": m.end()},
                       "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group().decode()}]})
    rec = {"$type": "app.bsky.feed.post", "text": text, "facets": facets,
           "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
           "embed": {"$type": "app.bsky.embed.images", "images": [{"alt": "The PlanFind home page: search planning applications by postcode", "image": blob, "aspectRatio": {"width": 1200, "height": 630}}]}}
    out = _req(base + "com.atproto.repo.createRecord", json.dumps({"repo": sess["did"], "collection": "app.bsky.feed.post", "record": rec}).encode(), {**auth, "Content-Type": "application/json"})
    return "posted " + out.get("uri", "")


def post_facebook(img: bytes, text: str):
    pid, tok = os.environ.get("FB_PAGE_ID"), os.environ.get("FB_PAGE_TOKEN")
    if not (pid and tok):
        return "skipped (FB_PAGE_ID / FB_PAGE_TOKEN not set)"
    bd = uuid.uuid4().hex
    parts = []
    for k, v in (("caption", text), ("access_token", tok), ("published", "true")):
        parts.append(f'--{bd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{bd}\r\nContent-Disposition: form-data; name="source"; filename="planfind.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + img + b"\r\n")
    parts.append(f"--{bd}--\r\n".encode())
    out = _req(f"https://graph.facebook.com/v21.0/{pid}/photos", b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={bd}"})
    return "posted " + str(out.get("post_id") or out.get("id"))


def post_x(img: bytes, text: str):
    keys = [os.environ.get(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")]
    if not all(keys):
        return "skipped (X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET not set)"
    import tweepy
    auth = tweepy.OAuth1UserHandler(*keys)
    media = tweepy.API(auth).media_upload(filename="planfind.png", file=io.BytesIO(img))
    cl = tweepy.Client(consumer_key=keys[0], consumer_secret=keys[1], access_token=keys[2], access_token_secret=keys[3])
    r = cl.create_tweet(text=text[:270], media_ids=[media.media_id])
    return "posted " + str(r.data.get("id"))


def main():
    text = caption()
    print(f"[{datetime.datetime.now(datetime.timezone.utc).isoformat()}] PlanFind billboard  DRY_RUN={DRY}\nCaption:\n{text}\n")
    img = screenshot()
    print(f"Screenshot {len(img)//1024} KB saved to {OUT}")
    if DRY:
        print("DRY_RUN: nothing posted.")
        return
    failed = 0
    for name, fn in (("Bluesky", post_bluesky), ("Facebook", post_facebook), ("X", post_x)):
        try:
            print(f"  {name}: {fn(img, text)}")
        except Exception as e:
            failed += 1
            body = getattr(e, "read", lambda: b"")()[:300]
            print(f"  {name}: FAILED {type(e).__name__}: {str(e)[:200]} {body}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
