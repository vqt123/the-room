"""Wait for one release to finish baking. Usage: wait_release.py <release-id> [timeout_s]"""
import json, sys, time
import devplatform as dp

BUILD = "463c3b3c-2a1b-4648-9651-8461392ec7ff"


def main():
    rel = sys.argv[1]
    limit = float(sys.argv[2]) if len(sys.argv) > 2 else 2400
    t0 = time.time()
    last = None
    while time.time() - t0 < limit:
        try:
            st, body = dp.call("GET", f"/builder/v1/builds/{BUILD}/releases")
            rows = body.get("releases", []) if isinstance(body, dict) else []
            row = next((r for r in rows if r["id"].startswith(rel[:8])), None)
        except Exception as e:  # a poll failure is never fatal
            print(f"{time.time()-t0:5.0f}s poll error: {str(e)[:70]}", flush=True)
            time.sleep(20)
            continue
        if row is None:
            print(f"{time.time()-t0:5.0f}s release not in the list yet", flush=True)
            time.sleep(20)
            continue
        state = (row["status"], json.dumps(row["artifactCounts"]))
        if state != last:
            print(f"{time.time()-t0:5.0f}s {state[0]} {state[1]}", flush=True)
            last = state
        counts = row["artifactCounts"]
        if counts.get("failed"):
            print("FAILED"); raise SystemExit(1)
        if counts.get("pending") == 0 and row["status"] == "complete":
            print(f"COMPLETE after {time.time()-t0:.0f}s, deployable={row['deployable']}")
            return
        time.sleep(20)
    raise SystemExit(f"still building after {limit:.0f}s")


if __name__ == "__main__":
    main()
