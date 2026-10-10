"""End-to-end CLI for the architectural fine-tune process.

Usage:
    # Run baseline + heuristic editor + lock
    python -m arch_finetune.cli --budget 20 --lock-on-converge

    # Verify a locked configuration
    python -m arch_finetune.cli --verify-lock ./data/locks/default.lock.json --key $SAWII_LOCK_KEY

    # Run drift detection from a capture JSONL
    python -m arch_finetune.cli --drift-check ./data/captures.jsonl --locked-score 0.85
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sawii_persona.config import Settings  # noqa: E402

from . import editor, lock as lock_mod, snapshot as snap_mod
from .drift import DriftDetector, load_captures_jsonl
from .runner import run_corpus
from .scorer import score_run


async def cmd_run(args) -> int:
    settings = Settings()
    prompts_module = snap_mod.load_prompts_module()
    session = editor.EditorSession(settings=settings, prompts_module=prompts_module,
                                   budget=args.budget)
    print("Capturing baseline...")
    base = await session.baseline()
    print(base.summary())
    print()
    print("Running heuristic editor...")
    final = await editor.heuristic_editor(session, base=base)
    print(final.summary())
    print()
    print(session.report())
    if args.lock_on_converge:
        key_hex = args.lock_key or os.environ.get("SAWII_LOCK_KEY", "")
        if not key_hex:
            print("--lock-on-converge requires --lock-key or SAWII_LOCK_KEY env")
            return 2
        key = bytes.fromhex(key_hex)
        if session.best_snapshot is None:
            print("no improvement found; not locking")
            return 0
        path = lock_mod.lock(session.best_snapshot, key=key,
                             harmony_score=session.best_score, locked_by="heuristic-editor-v1",
                             notes=f"budget={args.budget}")
        print(f"Locked at {path}")
    return 0


def cmd_verify(args) -> int:
    if not args.key:
        print("--verify-lock requires --key or SAWII_LOCK_KEY env")
        return 2
    key = bytes.fromhex(args.key)
    settings = Settings()
    prompts_module = snap_mod.load_prompts_module()
    snap_now = snap_mod.Snapshot.capture(settings, prompts_module)
    ok, reason = lock_mod.verify_lock(snap_now, args.verify_lock, key=key)
    print(f"{'OK' if ok else 'FAIL'}: {reason}")
    return 0 if ok else 1


def cmd_drift(args) -> int:
    captures = list(load_captures_jsonl(args.drift_check))
    if not captures:
        print("no captures to check")
        return 0
    detector = DriftDetector(locked_score=args.locked_score, threshold=args.threshold)
    for c in captures:
        detector.observe(c)
    alert = detector.check()
    if alert is None:
        print(f"OK: no drift detected in {len(captures)} captures (locked={args.locked_score})")
        return 0
    print(alert.summary())
    return 1


def main() -> int:
    p = argparse.ArgumentParser(description="Architectural fine-tune for sawii-persona")
    p.add_argument("--budget", type=int, default=20, help="max edits per editor session")
    p.add_argument("--lock-on-converge", action="store_true",
                   help="sign and lock the best configuration when the editor converges")
    p.add_argument("--lock-key", default="", help="hex-encoded HMAC key for lock signing")
    p.add_argument("--verify-lock", default="", help="path to lock file to verify against")
    p.add_argument("--key", default="", help="hex HMAC key (verify)")
    p.add_argument("--drift-check", default="", help="path to captures JSONL for drift detection")
    p.add_argument("--locked-score", type=float, default=0.85,
                   help="the harmony score that was locked")
    p.add_argument("--threshold", type=float, default=0.10,
                   help="max acceptable drop from locked score before alerting")
    args = p.parse_args()
    if args.verify_lock:
        return cmd_verify(args)
    if args.drift_check:
        return cmd_drift(args)
    return asyncio.run(cmd_run(args))


if __name__ == "__main__":
    sys.exit(main())