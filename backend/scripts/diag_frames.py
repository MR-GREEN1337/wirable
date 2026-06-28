"""Reproduce the white-frame bug: run the audit harness in a sandbox, then pull
EVERY screenshot it captured so we can SEE which frames went white and after
which action.

Writes /tmp/diag/NNNN.jpg (+ manifest.txt with seq, bytes, caption) in the
backend container; copy them out to view.

Usage (inside backend container):
    python -m scripts.diag_frames https://www.notion.so
"""
import asyncio
import base64
import os
import sys

from src.core.sandbox import DaytonaClient
from src.core.llm import key_pool
from src.core.config import settings
from src.services.test_service import AUDIT_DRIVER_PATH, SKILLS_PATH
from src.services.test_service import _frame_seq

OUT = "/tmp/diag"


async def main(url: str) -> None:
    os.makedirs(OUT, exist_ok=True)
    key = key_pool.next_key()
    env = {"WIRABLE_MAX_STEPS": "8"}
    if key:
        env["ANTHROPIC_API_KEY"] = key
        env["ANTHROPIC_MODEL"] = settings.ANTHROPIC_MODEL

    driver = AUDIT_DRIVER_PATH.read_text()
    skills = SKILLS_PATH.read_text() if SKILLS_PATH.exists() else ""

    async with DaytonaClient.sandbox(env=env) as sb:
        await sb.upload("/tmp/audit_driver.py", driver.encode())
        if skills:
            await sb.upload("/tmp/skills.py", skills.encode())
        print(f"running audit on {url} …", flush=True)
        cmd = f"cd /tmp && python3 /tmp/audit_driver.py {url!r} deep 2>&1 | tail -40 || true"
        log = await sb.exec(cmd, timeout=600)
        print("---- driver tail ----", flush=True)
        print(log[-2000:], flush=True)

        files = await sb.list_files("/tmp/screenshots/*.jpg")
        files = sorted(files, key=_frame_seq)
        print(f"\n==== {len(files)} frames ====", flush=True)
        man = []
        for f in files:
            seq = _frame_seq(f)
            b64 = await sb.read_b64(f)
            nbytes = len(base64.b64decode(b64)) if b64 else 0
            # sidecar caption
            cap = ""
            try:
                j = await sb.exec(f"cat {f[:-4]}.json 2>/dev/null", timeout=10)
                if j.strip():
                    import json
                    cap = json.loads(j).get("caption", "")
            except Exception:
                pass
            if b64:
                with open(f"{OUT}/{seq:04d}.jpg", "wb") as fh:
                    fh.write(base64.b64decode(b64))
            flag = "  <-- TINY (likely white/blank)" if 0 < nbytes < 9000 else ""
            line = f"frame {seq:04d}  {nbytes:>7}B  {cap[:60]}{flag}"
            print(line, flush=True)
            man.append(line)
        with open(f"{OUT}/manifest.txt", "w") as fh:
            fh.write("\n".join(man))
        print(f"\nwrote frames to {OUT}", flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "https://www.notion.so"))
