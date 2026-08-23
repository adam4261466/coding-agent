"""Run Phase 4 (growth optimization engine) from the command line.

Phase 4 learns from Phase 3 funnel data only. Statistics are computed
deterministically; the LLM (if used) only interprets - it never invents
numbers. Nothing is applied automatically: every recommendation requires
human approval.

Usage:
    python run_phase4.py --performance [--campaign c1]
    python run_phase4.py --funnel [--campaign c1]
    python run_phase4.py --hypotheses
    python run_phase4.py --experiments
    python run_phase4.py --create-experiment --control problem_first --variant conversation_first
    python run_phase4.py --recommendations
    python run_phase4.py --icp-report
    python run_phase4.py --calibration
    python run_phase4.py --all [--json]
"""

import argparse
import json
import os
import sys
import threading
import traceback
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, Exception):
    pass

# ---------------------------------------------------------------------------
# Inline debugging: prints to stderr and appends to agent_debug.log
# (set AGENT_DEBUG=0 to disable)
# ---------------------------------------------------------------------------
_DEBUG_ON = os.environ.get("AGENT_DEBUG", "1") != "0"
_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_debug.log")


def _dbg(msg: str):
    if not _DEBUG_ON:
        return
    try:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        thread = threading.current_thread().name
        line = f"[{ts}] [run_phase4] [{thread}] {msg}"
        print(line[:4000], file=sys.stderr, flush=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


from linkedin_intelligence.utils import DB_PATH
from linkedin_intelligence.store import Store
from linkedin_intelligence.optimization.segment_performance import segment_performance
from linkedin_intelligence.optimization.strategy_performance import strategy_performance
from linkedin_intelligence.optimization.funnel_analysis import funnel_analysis
from linkedin_intelligence.optimization.hypothesis import generate_hypotheses
from linkedin_intelligence.optimization.experiment import (
    create_experiment, evaluate_experiment, summarize)
from linkedin_intelligence.optimization.recommendations import (
    recommendations, icp_learning_report)
from linkedin_intelligence.optimization.calibration import (
    build_optimization_calibration)


def _print_rate(label, r):
    print(f"  {label}: {r['rate']:.1%} "
          f"(n={r['n']}, sufficent={r['sufficient']}, "
          f"ci=[{r['ci_low']:.1%}, {r['ci_high']:.1%}])")


def main():
    ap = argparse.ArgumentParser(description="Phase 4 growth optimization")
    ap.add_argument("--performance", action="store_true")
    ap.add_argument("--funnel", action="store_true")
    ap.add_argument("--hypotheses", action="store_true")
    ap.add_argument("--experiments", action="store_true")
    ap.add_argument("--create-experiment", action="store_true")
    ap.add_argument("--control", default=None)
    ap.add_argument("--variant", default=None)
    ap.add_argument("--recommendations", action="store_true")
    ap.add_argument("--icp-report", action="store_true")
    ap.add_argument("--calibration", action="store_true")
    ap.add_argument("--all", dest="everything", action="store_true")
    ap.add_argument("--campaign", default=None)
    ap.add_argument("--json", dest="as_json", action="store_true")
    args = ap.parse_args()
    _dbg(f"main() args: {vars(args)}")

    store = Store(DB_PATH)
    try:
        bundle = {}

        if args.performance or args.everything:
            segs = segment_performance(store, campaign_id=args.campaign)
            strats = strategy_performance(store, campaign_id=args.campaign)
            bundle["segments"] = segs
            bundle["strategies"] = strats
            if not args.as_json:
                print("== segment performance ==")
                for s in segs:
                    print(f"  {s['segment']} (n={s['n']})")
                    _print_rate("  reply", s["reply_rate"])
                    _print_rate("  activation", s["activation_rate"])
                print("== strategy performance ==")
                for s in strats:
                    print(f"  {s['strategy']} (n={s['n']})")
                    _print_rate("  reply", s["reply_rate"])
                    _print_rate("  activation", s["activation_rate"])

        if args.funnel or args.everything:
            if args.campaign:
                f = funnel_analysis(store, campaign_id=args.campaign)
            else:
                f = funnel_analysis(store)
            bundle["funnel"] = f
            if not args.as_json:
                print("== funnel ==")
                for step in f["steps"]:
                    print(f"  {step['from']}->{step['to']}: {step['n_to']}/{step['n_from']} "
                          f"({step['rate']:.1%})")
                b = f["bottleneck"]
                if b:
                    print(f"  bottleneck: {b['from']}->{b['to']} "
                          f"(drop {b['drop']:.1%})")
                else:
                    print("  bottleneck: none (no data yet)")

        if args.hypotheses or args.everything:
            hs = generate_hypotheses(store)
            bundle["hypotheses"] = hs
            if not args.as_json:
                print("== suggested hypotheses ==")
                for h in hs:
                    print(f"  {h['hypothesis_id']}: {h['hypothesis']}")

        if args.experiments or args.everything:
            bundle["experiments"] = summarize(store)
            if not args.as_json:
                print("== experiments ==")
                for e in bundle["experiments"]:
                    print(f"  {e['experiment_id']} [{e['status']}] "
                          f"{e['control']} vs {e['variant']} -> "
                          f"{e['result']}")

        if args.create_experiment:
            if not args.control or not args.variant:
                raise SystemExit("--create-experiment needs --control and --variant")
            e = create_experiment(store, hypothesis="A/B of message strategy",
                                  control=args.control, variant=args.variant)
            out = evaluate_experiment(store, e["experiment_id"])
            bundle["experiment_created"] = e
            bundle["experiment_evaluation"] = out
            if not args.as_json:
                print(f"experiment {e['experiment_id']}: {out['result']['verdict']}")

        if args.recommendations or args.everything:
            recs = recommendations(store)
            bundle["recommendations"] = recs
            if not args.as_json:
                print("== recommendations (human approval required) ==")
                for r in recs:
                    print(f"  [{r['priority']}] {r['area']}: {r['action']}")
                    print(f"      evidence: {r['evidence']}")

        if args.icp_report or args.everything:
            bundle["icp_report"] = icp_learning_report(store)
            if not args.as_json:
                rep = bundle["icp_report"]
                rec = rep["recommendation"]
                print("== ICP learning ==")
                print(f"  icp={rep['icp_name']}")
                print(f"  recommendation ({rec['type']}): {rec['recommendation']}")
                print(f"  decision pending: {'accept' in rec}")

        if args.calibration or args.everything:
            bundle["calibration"] = build_optimization_calibration(store)
            if not args.as_json:
                c = bundle["calibration"]
                print("== optimization calibration ==")
                print(f"  approved={c['human_approved']} "
                      f"contacted={c['contacted']} "
                      f"tp={c['true_positives']} fp={c['false_positives']}")
                print(f"  precision={c['precision']}")

        if args.as_json and bundle:
            print(json.dumps(bundle, ensure_ascii=False, indent=2))
        elif not bundle:
            ap.print_help()
    except Exception as e:
        _dbg(f"EXCEPTION run_phase4 main: {e}\n{traceback.format_exc()}")
        raise
    finally:
        store.close()


if __name__ == "__main__":
    main()
