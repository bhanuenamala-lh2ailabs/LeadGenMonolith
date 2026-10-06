#!/usr/bin/env python3
"""Convert Claude Code session .jsonl files into readable, secret-redacted markdown.

Keeps: user prompts, assistant text, condensed tool calls (name + short input), AI titles.
Drops: tool result bodies (summarised to a size note), file-history/attachment/queue noise.
Redacts: every value in ./.env and ./secrets/*.json, plus common token shapes.
Usage: chats_to_markdown.py <raw_dir> <out_dir>
"""
import json, re, sys, glob, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
secrets = set()
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        v = line.split("=", 1)[1].strip().strip("\"'")
        if len(v) >= 8: secrets.add(v)
def walk(o):
    if isinstance(o, dict):
        for v in o.values(): yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)
    elif isinstance(o, str) and len(o) >= 12: yield o
for f in glob.glob(str(ROOT / "secrets" / "*.json")):
    try:
        for s in walk(json.load(open(f))):
            if not s.startswith("http") and " " not in s: secrets.add(s)
    except Exception: pass
secrets_sorted = sorted(secrets, key=len, reverse=True)
PATTERNS = [re.compile(p) for p in (
    r"sk-ant-[A-Za-z0-9_\-]{20,}", r"sk-[A-Za-z0-9]{32,}", r"pat-[a-z0-9]+-[0-9a-f\-]{30,}",
    r"ghp_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{30,}", r"ya29\.[A-Za-z0-9_\-]{20,}",
    r"1//[A-Za-z0-9_\-]{30,}", r"AIza[A-Za-z0-9_\-]{30,}", r"GOCSPX-[A-Za-z0-9_\-]{20,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    r"(?i)bearer\s+[A-Za-z0-9._\-]{25,}",
)]
def redact(t):
    for s in secrets_sorted: t = t.replace(s, "[REDACTED]")
    for p in PATTERNS: t = p.sub("[REDACTED]", t)
    return t

def clip(s, n): s = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False); return s if len(s) <= n else s[:n] + f"… [+{len(s)-n} chars]"

def convert(jsonl, out):
    title = None; n_user = n_asst = 0
    with open(jsonl) as fh, open(out, "w") as o:
        buf = []
        for line in fh:
            try: d = json.loads(line)
            except Exception: continue
            t = d.get("type"); ts = (d.get("timestamp") or "")[:19]
            if t == "ai-title": title = d.get("aiTitle"); buf.append(f"\n## === Session title: {title} ===\n"); continue
            if t not in ("user", "assistant") or d.get("isSidechain"): continue
            msg = d.get("message") or {}; c = msg.get("content")
            if isinstance(c, str): c = [{"type": "text", "text": c}]
            for b in c or []:
                bt = b.get("type")
                if bt == "text" and b.get("text", "").strip():
                    txt = b["text"]
                    if t == "user":
                        if txt.lstrip().startswith(("<ide_", "<system-reminder", "<command-")) and len(txt) < 400: continue
                        n_user += 1; buf.append(f"\n### USER [{ts}]\n{clip(txt, 20000)}\n")
                    else: n_asst += 1; buf.append(f"\n### ASSISTANT [{ts}]\n{clip(txt, 20000)}\n")
                elif bt == "tool_use": buf.append(f"- 🔧 `{b.get('name')}` {clip(b.get('input'), 400)}")
                elif bt == "tool_result":
                    r = b.get("content"); sz = len(r) if isinstance(r, str) else len(json.dumps(r))
                    buf.append(f"  ↳ result ({sz} chars) {clip(r if isinstance(r,str) else json.dumps(r), 200)}")
        o.write(redact(f"# Transcript: {jsonl.name}\nsource: {jsonl}\nuser msgs: {n_user}, assistant msgs: {n_asst}\n" + "\n".join(buf)))
    return n_user, n_asst

if __name__ == "__main__":
    raw, outd = Path(sys.argv[1]), Path(sys.argv[2]); outd.mkdir(parents=True, exist_ok=True)
    for j in sorted(raw.glob("*.jsonl")):
        out = outd / (j.stem + ".md"); u, a = convert(j, out)
        print(j.name, "→", out.name, f"user={u} asst={a} size={out.stat().st_size//1024}KB", flush=True)
