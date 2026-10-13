#!/usr/bin/env python3
"""Server-scheduled regression CI client. Never embeds credentials in URLs."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import urllib.request


def request(base, token, path, body=None):
    req = urllib.request.Request(base.rstrip('/') + '/api/regressions' + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.getenv('SAB_URL', 'http://localhost:8000'))
    parser.add_argument('--config', help='JSON create-plan payload')
    parser.add_argument('--plan-id', help='Observe an existing plan instead of creating')
    parser.add_argument('--timeout', type=float, default=1800)
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--output', default='regression-results')
    args = parser.parse_args(argv)
    token = os.getenv('SAB_API_KEY', '')
    if not token or bool(args.config) == bool(args.plan_id) or args.interval <= 0 or args.timeout <= 0:
        parser.error('SAB_API_KEY required; choose exactly one of --config / --plan-id; positive timeout/interval required')
    try:
        plan_id = args.plan_id
        if args.config:
            config = json.loads(Path(args.config).read_text())
            plan_id = json.loads(request(args.url, token, '', config))['id']
        print('Plan:', plan_id, flush=True)
        deadline = time.monotonic() + args.timeout
        while True:
            gate = json.loads(request(args.url, token, '/' + plan_id + '/gate'))
            if gate['gate'] != 'waiting':
                break
            if time.monotonic() >= deadline:
                print('Timeout; release blocked. Server plan remains running.', file=sys.stderr)
                return 2
            time.sleep(min(args.interval, max(0, deadline-time.monotonic())))
        output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
        for suffix in ('junit.xml', 'evidence.zip'):
            (output / suffix).write_bytes(request(args.url, token, '/' + plan_id + '/' + suffix))
        (output / 'gate.json').write_text(json.dumps(gate, indent=2))
        print('Gate:', gate['gate'], flush=True)
        return 0 if gate['gate'] == 'passed' and gate['release_allowed'] is True else 1
    except Exception as exc:
        # HTTP exception text never contains the bearer header.
        print('Regression request failed:', str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
