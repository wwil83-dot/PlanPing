"""Quick check of every proxy in PROXY_URLS: TCP connect, then a raw HTTP CONNECT with the login.
Prints only ip:port and the proxy's reply (never the username or password)."""
import base64, os, socket, time
from urllib.parse import urlsplit, unquote

urls = [u.strip() for u in os.environ.get("PROXY_URLS", "").split(",") if u.strip()]
print(f"{len(urls)} proxies loaded\n")
for i, u in enumerate(urls):
    p = urlsplit(u)
    host, port = p.hostname, p.port
    user, pw = unquote(p.username or ""), unquote(p.password or "")
    tag = f"proxy {i}: {host}:{port}"
    print(f"{tag}  username length {len(user)}, password length {len(pw)}, scheme {p.scheme}")
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=15)
    except Exception as e:
        print(f"   TCP connect FAILED after {time.time() - t0:.1f}s: {type(e).__name__} {e}\n")
        continue
    print(f"   TCP connect ok in {time.time() - t0:.1f}s")
    try:
        auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
        s.settimeout(20)
        s.sendall((f"CONNECT api.ipify.org:443 HTTP/1.1\r\nHost: api.ipify.org:443\r\n"
                   f"Proxy-Authorization: Basic {auth}\r\n\r\n").encode())
        reply = s.recv(512).decode(errors="replace").split("\r\n")[0]
        print(f"   proxy replied: {reply or '(nothing)'}\n")
    except Exception as e:
        print(f"   CONNECT failed: {type(e).__name__} {e}\n")
    finally:
        s.close()
