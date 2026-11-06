"""Checks whether the reserve controller can change the backup reserve, locally and through Tesla's cloud.

Run it inside the server's container on percula, so it uses the same settings and login caches:
    docker exec mcp-server uv run --no-sync python scripts/check_reserve_write.py
By default it only reads. With --write-test it writes the current reserve back to each path, which proves
the path can write without changing the setting. It never prints passwords or tokens.
"""
import argparse
import os

from dotenv import load_dotenv

from server import powerwall

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--write-test", action="store_true",
                    help="write the current reserve back (no change) to test each path's write permission")
args = parser.parse_args()
load_dotenv()


def show(ok: bool | None, label: str, detail: str = "") -> None:
    mark = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    print(f"[{mark}] {label}" + (f": {detail}" if detail else ""))


def explain(reason: str, messages: list[str]) -> str:
    return f"{reason} ({' / '.join(messages)})" if messages else reason


print(f"Gateway {os.getenv('PW_HOST', '(PW_HOST not set)')}, cache folder {os.getenv('PW_CACHE_DIR', '.')}")
print(f"RESERVE_MODE={os.getenv('RESERVE_MODE', '') or '(not set, so dry-run)'}\n")

# Local gateway
current = raw = None
with powerwall.pypowerwall_errors() as messages:
    try:
        local = powerwall.get_connection()
        show(True, "Local login", f"firmware {local.version()}")
        current = local.get_reserve(scale=True)
        raw = local.get_reserve(scale=False)
        if current is None:
            show(False, "Local read of the reserve", explain("no value returned", messages))
        else:
            show(True, "Local read of the reserve", f"{current:.0f}% in the app (gateway value {raw}), "
                                                     f"mode {local.get_mode()}")
    except Exception as e:
        local = None
        show(False, "Local login", explain(str(e), messages))

if local is not None and raw is not None and args.write_test:
    with powerwall.pypowerwall_errors() as messages:
        try:
            result = local.set_reserve(raw)  # the gateway's own scale, so the value is unchanged
            show(result is not None, "Local write", "accepted" if result is not None else
                 explain("refused", messages))
        except Exception as e:
            show(False, "Local write", explain(str(e), messages))
elif local is not None:
    show(None, "Local write", "not tested; add --write-test")

# Tesla cloud
print()
auth_file = os.path.join(os.getenv("PW_CACHE_DIR", "."), ".pypowerwall.auth")
if not os.path.exists(auth_file):
    show(False, "Cloud sign-in", f"{auth_file} does not exist, so the one-time Tesla sign-in was never saved here")
else:
    with powerwall.pypowerwall_errors() as messages:
        try:
            cloud = powerwall.get_cloud_connection()
            cloud_current = cloud.get_reserve(scale=True)
            if cloud_current is None:
                show(False, "Cloud read of the reserve", explain("no value returned", messages))
            else:
                show(True, "Cloud read of the reserve", f"{cloud_current:.0f}%")
            if args.write_test and cloud_current is not None:
                result = cloud.set_reserve(cloud_current)
                show(result is not None, "Cloud write", "accepted" if result is not None else
                     explain("refused", messages))
            elif cloud_current is not None:
                show(None, "Cloud write", "not tested; add --write-test")
        except Exception as e:
            show(False, "Cloud sign-in", explain(str(e), messages))
