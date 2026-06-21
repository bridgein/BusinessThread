import json

with open("seen.json") as f:
    remote_ids = set(json.load(f).get("ids", []))
with open("/tmp/seen_current.json") as f:
    current_ids = set(json.load(f).get("ids", []))

merged = list(remote_ids | current_ids)[-2000:]

with open("seen.json", "w") as f:
    json.dump({"ids": merged}, f, ensure_ascii=False, indent=0)
