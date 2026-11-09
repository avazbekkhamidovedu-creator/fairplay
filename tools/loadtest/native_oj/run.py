#!/usr/bin/env python3
import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import metrics as metrics_mod
import safety
from client import OK, Config, Runner

COUNTDOWN_S = 5


def build_parser():
    p = argparse.ArgumentParser(description="Simulated FairPlay Mac clients against a STAGING OJ and FairPlay server.")
    p.add_argument("--i-understand-this-is-staging", dest="staging", action="store_true")
    p.add_argument("--clients", type=int, required=True, help="up to %d (use 100, 500, 1000, 5000)" % safety.MAX_CLIENTS)
    p.add_argument("--fairplay-url", default="", help="staging FairPlay server base URL (escrow step)")
    p.add_argument("--oj-url", default="", help="staging DKU OJ base URL")
    p.add_argument("--ojmode", choices=["ingress-bypass", "plain"], default="ingress-bypass")
    p.add_argument("--contest", default="", help="contest key of the staging exam")
    p.add_argument("--users-file", help="user:password per line, one staging account per client")
    p.add_argument("--pages", default="/,/contests/,/contest/{contest}", help="comma separated GET paths")
    p.add_argument("--start-window", type=float, default=120.0)
    p.add_argument("--duration", type=float, default=600.0, help="steady state seconds after a client starts")
    p.add_argument("--time-scale", type=float, default=1.0, help="multiplies every wait (tests only)")
    p.add_argument("--loss", type=float, default=0.0, help="simulated packet loss/timeout fraction, e.g. 0.05")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--ca-bundle", help="PEM bundle that signs the staging server certificates")
    p.add_argument("--seed", type=int)
    p.add_argument("--out", help="write the metrics JSON here")
    p.add_argument("--yes", action="store_true", help="skip the 5 s wait (tests)")
    return p


def load_users(path):
    users = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and ":" in line:
                users.append(tuple(line.split(":", 1)))
    return users


def prepare(args, env=None, resolver=safety.system_resolver):
    env = os.environ if env is None else env
    if not args.staging:
        raise safety.Refused("pass --i-understand-this-is-staging")
    safety.check_clients(args.clients)
    if not 0 <= args.loss < 1 or args.time_scale <= 0:
        raise safety.Refused("bad --loss or --time-scale")
    if not (args.fairplay_url or args.oj_url):
        raise safety.Refused("give --fairplay-url and/or --oj-url")
    targets = {}
    for url in (args.fairplay_url, args.oj_url):
        if url:
            targets[url] = safety.check_target(url, env, resolver)
    app_key = env.get("LOADTEST_APP_KEY", "")
    if args.fairplay_url and not app_key:
        raise safety.Refused("LOADTEST_APP_KEY (the staging app key) is not set")
    ingress_key = env.get("LOADTEST_INGRESS_KEY", "")
    users = load_users(args.users_file) if args.users_file else []
    if args.oj_url:
        if args.ojmode == "ingress-bypass":
            if not ingress_key:
                raise safety.Refused("LOADTEST_INGRESS_KEY is not set (needed for --ojmode ingress-bypass)")
            if not args.contest:
                raise safety.Refused("--contest is required for --ojmode ingress-bypass")
            if len(users) < args.clients:
                raise safety.Refused("--users-file needs one account per client for ingress-bypass")
    cfg = Config(clients=args.clients, fairplay_url=args.fairplay_url.rstrip("/"), oj_url=args.oj_url.rstrip("/"),
                 oj_mode=args.ojmode, contest=args.contest, app_key=app_key, ingress_key=ingress_key,
                 users=users, pages=[x for x in args.pages.split(",") if x], start_window=args.start_window,
                 duration=args.duration, time_scale=args.time_scale, loss=args.loss, timeout=args.timeout,
                 seed=args.seed, ca_bundle=args.ca_bundle)
    cfg.pins = {(h, p): list(a) for h, p, a in targets.values()}
    cfg.guard = safety.make_guard(list(targets), env, resolver, {u: a for u, (_, _, a) in targets.items()})
    return cfg, targets


async def execute(cfg):
    m = metrics_mod.Metrics()
    await Runner(cfg, m).run()
    return m.summary(cfg.clients, OK)


def main(argv=None, env=None, resolver=safety.system_resolver, sleep=time.sleep):
    args = build_parser().parse_args(argv)
    try:
        cfg, targets = prepare(args, env, resolver)
    except safety.Refused as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2
    for url, (host, port, addresses) in targets.items():
        print(f"target {url} -> {host}:{port} resolves to {', '.join(addresses)}")
    print(f"{cfg.clients} clients, start window {cfg.start_window}s, steady {cfg.duration}s, mode {cfg.oj_mode}")
    if not args.yes:
        print(f"starting in {COUNTDOWN_S} s, Ctrl-C to abort")
        sleep(COUNTDOWN_S)
    try:
        cfg.guard()
        summary = asyncio.run(execute(cfg))
    except safety.Refused as error:
        print(f"ABORTED: {error}", file=sys.stderr)
        return 3
    print(metrics_mod.render(summary))
    if args.out:
        metrics_mod.write_json(summary, args.out)
    return 0 if summary["verdict"]["result"] in ("PASS", "ESCROW_ONLY") else 1


if __name__ == "__main__":
    sys.exit(main())
