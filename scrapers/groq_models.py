"""Prints the model IDs your GROQ_API_KEY can use."""
import os, json, urllib.request
req = urllib.request.Request("https://api.groq.com/openai/v1/models",
    headers={"Authorization": "Bearer " + os.environ["GROQ_API_KEY"], "User-Agent": "planfind"})
data = json.load(urllib.request.urlopen(req, timeout=30))
for m in sorted(data["data"], key=lambda m: m["id"]):
    print(m["id"], "| owner:", m.get("owned_by"), "| context:", m.get("context_window"), "| active:", m.get("active"))
