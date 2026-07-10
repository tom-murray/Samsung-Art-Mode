"""CLI wrapper over tvcontrol.diagnose. Run inside the container/VLAN.

    python diagnose.py <tv_ip>
"""
import json
import sys

import tvcontrol


def main(ip):
    print(json.dumps(tvcontrol.diagnose(ip, token_dir="."), indent=2, default=str))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python diagnose.py <tv_ip>")
    main(sys.argv[1])
