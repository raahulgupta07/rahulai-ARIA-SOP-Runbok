#!/usr/bin/env python3
"""Scale planner — user count -> deployment config for City Agent Aria.

Given a total user count (and a few assumptions) this prints:
  - recommended AWS instance + replica count + worker/DB topology
  - the exact .env.prod concurrency block (WEB_CONCURRENCY, DB_POOL_MAX,
    PG_MAX_CONNECTIONS, LLM_MAX_CONCURRENCY, STORAGE)
  - the pool-math safety check (replicas x WEB x POOL + 15 < PG_MAX)
  - LLM throughput ceiling vs estimated demand
  - rough monthly cost (AWS + OpenRouter)

It is a STARTING POINT, not a guarantee — validate the chosen config with a real
load test (the box is I/O-bound on OpenRouter; the LLM tier is the true ceiling).
No dependencies, pure stdlib.

    python scripts/scale_plan.py --users 100
    python scripts/scale_plan.py --users 300 --concurrency 0.5 --cache 0.5
    python scripts/scale_plan.py --users 500 --q-per-day 15 --tier 80
"""
import argparse
import math


def plan(users, concurrency, q_per_day, cold_s, cache_hit, tier):
    concurrent = users * concurrency                      # active sessions at peak
    # humans read each answer ~cold_s..60s before re-asking -> in-flight requests
    # are far fewer than active sessions (~1 per 7 active users).
    in_flight = max(1, round(concurrent / 7))

    # ---- demand (answers/min) ----
    work_days = 22
    answers_per_min = users * q_per_day / (work_days * 8 * 60)   # spread over an 8h day
    peak_per_min = answers_per_min * 4                          # peak ~4x daily average
    cold_per_min = peak_per_min * (1 - cache_hit)              # cache serves the rest free

    # ---- LLM slots needed: cold demand/sec * cold_s, x2 safety, floor 30 ----
    need_slots = math.ceil((cold_per_min / 60) * cold_s * 2)
    llm = max(30, math.ceil(need_slots / 10) * 10)
    if tier:
        llm = min(llm, tier)                                  # never exceed OpenRouter tier

    # ---- replicas: redundancy + connection spread (NOT cpu-bound) ----
    replicas = max(2, math.ceil(users / 120))

    web = 4
    pool_max = 10 if users <= 150 else 8
    raw_conns = replicas * web * pool_max + 15
    pg_max = max(200, math.ceil((raw_conns + 50) / 100) * 100)

    multi_host = users > 350
    storage = "s3" if multi_host else "local"

    # ---- topology ----
    if users <= 120:
        app = "1x t3.xlarge (4 vCPU / 16 GB)"
        db = "on-box Postgres (or db.t3.small RDS)"
        worker = "on the app host"
    elif users <= 320:
        app = "1x m5.xlarge (4/16)  [t3.2xlarge 8/32 if ingest overlaps peak]"
        db = "RDS db.t3.medium (2/4)"
        worker = "on the app host"
    else:
        app = "2x m5.xlarge behind ALB (or 1x m5.2xlarge 8/32)"
        db = "RDS db.m5.large Multi-AZ (+ read replica)"
        worker = "dedicated t3.large (2nd container = hot standby)"

    # ---- throughput sanity ----
    ceiling_per_min = round((llm / cold_s) * 60)

    # ---- rough monthly cost (us-east-1 on-demand) ----
    if users <= 120:
        aws = 130
    elif users <= 320:
        aws = 260
    else:
        aws = 700
    # LLM: ~3500 tok/answer, flash-lite blended ~$0.0009/answer, cache cuts cold count
    llm_cost = round(users * q_per_day * work_days * (1 - cache_hit) * 0.0011)

    ok = raw_conns < pg_max

    return locals()


def main():
    ap = argparse.ArgumentParser(description="Aria scale planner: users -> config")
    ap.add_argument("--users", type=int, required=True, help="total registered users")
    ap.add_argument("--concurrency", type=float, default=0.5,
                    help="peak active fraction (default 0.5 = 50%%)")
    ap.add_argument("--q-per-day", type=float, default=10, help="questions/user/day")
    ap.add_argument("--cold-s", type=float, default=15, help="avg cold answer seconds")
    ap.add_argument("--cache", type=float, default=0.4,
                    help="Q&A cache hit fraction (zero-LLM serves)")
    ap.add_argument("--tier", type=int, default=0,
                    help="OpenRouter max concurrent calls your tier allows (0 = unknown)")
    a = ap.parse_args()

    p = plan(a.users, a.concurrency, a.q_per_day, a.cold_s, a.cache, a.tier)
    L = lambda k: p[k]

    print(f"\n=== Aria scale plan — {a.users} users ===")
    print(f"assumptions: {int(a.concurrency*100)}% peak-active, {a.q_per_day} q/user/day, "
          f"{int(a.cache*100)}% cache hit, cold≈{a.cold_s}s"
          + (f", OpenRouter tier={a.tier}" if a.tier else ""))
    print(f"\n  peak active sessions : ~{round(L('concurrent'))}")
    print(f"  peak in-flight reqs  : ~{L('in_flight')}")
    print(f"  peak demand          : ~{round(L('peak_per_min'))} answers/min "
          f"({round(L('cold_per_min'))}/min cold after cache)")

    print(f"\n--- topology ---")
    print(f"  app    : {L('app')}")
    print(f"  scale  : --scale app={L('replicas')}")
    print(f"  worker : {L('worker')}  (leader-gated, never scale OUT)")
    print(f"  db     : {L('db')}")
    print(f"  images : {L('storage').upper()}"
          + ("  (S3 required: multi-host)" if L('multi_host') else "  (EBS ./data, single host)"))

    print(f"\n--- .env.prod concurrency block ---")
    print(f"  WEB_CONCURRENCY={L('web')}")
    print(f"  DB_POOL_MIN=2")
    print(f"  DB_POOL_MAX={L('pool_max')}")
    print(f"  PG_MAX_CONNECTIONS={L('pg_max')}")
    print(f"  LLM_MAX_CONCURRENCY={L('llm')}")
    print(f"  INGEST_IN_API=0")
    print(f"  STORAGE={L('storage')}")
    print(f"  AUTO_QA_SERVE_ENABLED=1   # keep the zero-LLM cache ON")

    print(f"\n--- checks ---")
    pm = f"{L('replicas')}x{L('web')}x{L('pool_max')}+15 = {L('raw_conns')} < {L('pg_max')}"
    print(f"  pool-math       : {pm}   {'OK' if L('ok') else 'FAIL -> lower DB_POOL_MAX or raise PG_MAX'}")
    head = round(100 * L('cold_per_min') / L('ceiling_per_min')) if L('ceiling_per_min') else 0
    print(f"  LLM throughput  : ceiling ~{L('ceiling_per_min')}/min vs cold demand "
          f"~{round(L('cold_per_min'))}/min  ({head}% utilized)")
    if a.tier and L('need_slots') > a.tier:
        print(f"  WARNING: demand wants {L('need_slots')} slots but tier caps at {a.tier} "
              f"-> raise OpenRouter tier or cache hit-rate")

    print(f"\n--- rough cost / month (on-demand) ---")
    print(f"  AWS  : ~${L('aws')}   (Reserved/Savings Plan ~-40%)")
    print(f"  LLM  : ~${L('llm_cost')}   (flash-lite, after {int(a.cache*100)}% cache)")
    print(f"  total: ~${L('aws') + L('llm_cost')}\n")
    print("NOTE: starting point — confirm with a real load test. The box is I/O-bound on")
    print("OpenRouter; LLM_MAX_CONCURRENCY + your rate tier is the true ceiling.\n")


if __name__ == "__main__":
    main()
