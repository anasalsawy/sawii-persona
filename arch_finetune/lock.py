"""Cryptographic lock-and-sign for the harmonized configuration.

Once the editor converges on a configuration, we want to:
  1. Save the configuration as a Snapshot.
  2. Sign the snapshot with an HMAC key held outside the system.
  3. On every boot, verify the live configuration against the lock.
  4. On drift, refuse to boot (or alert, depending on policy).

The HMAC key is loaded from SAWII_LOCK_KEY (a shared secret). In production
this would be a KMS-managed key. For dev, generate one with:

    python -c "import secrets; print(secrets.token_hex(32))"

Usage:
    lock_path = lock(snap, key=hmac_key)
    ok, reason = verify_lock(snap_now, lock_path, key=hmac_key)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

from .snapshot import Snapshot


LOCK_FILE_HEADER = "SAWII-LOCK-V1"


@dataclass
class LockRecord:
    config_id: str
    snapshot_path: str
    signature: str
    harmony_score: float
    locked_at: float
    locked_by: str  # editor model identifier or "human"
    notes: str = ""

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "header": LOCK_FILE_HEADER,
                "config_id": self.config_id,
                "snapshot_path": self.snapshot_path,
                "signature": self.signature,
                "harmony_score": self.harmony_score,
                "locked_at": self.locked_at,
                "locked_by": self.locked_by,
                "notes": self.notes,
            }, f, indent=2)

    @classmethod
    def load(cls, path: str) -> "LockRecord":
        with open(path) as f:
            data = json.load(f)
        if data.get("header") != LOCK_FILE_HEADER:
            raise ValueError(f"not a sawii lock file: {path}")
        return cls(
            config_id=data["config_id"],
            snapshot_path=data["snapshot_path"],
            signature=data["signature"],
            harmony_score=data["harmony_score"],
            locked_at=data["locked_at"],
            locked_by=data["locked_by"],
            notes=data.get("notes", ""),
        )


def _snapshot_bytes(snap: Snapshot) -> bytes:
    payload = json.dumps({
        "config_id": snap.config_id,
        "settings": snap.settings,
        "prompts": snap.prompts,
        "captured_at": snap.captured_at,
    }, sort_keys=True).encode("utf-8")
    return payload


def _sign(payload: bytes, key: bytes) -> str:
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def lock(snap: Snapshot, *, key: bytes, harmony_score: float, locked_by: str = "human",
         lock_dir: str = "./data/locks", snapshot_dir: str = "./data/snapshots",
         notes: str = "") -> str:
    """Sign a snapshot and write the lock record. Returns the lock file path."""
    os.makedirs(snapshot_dir, exist_ok=True)
    os.makedirs(lock_dir, exist_ok=True)
    snap_path = os.path.join(snapshot_dir, f"{snap.config_id}.json")
    snap.save(snap_path)
    record = LockRecord(
        config_id=snap.config_id,
        snapshot_path=snap_path,
        signature="",  # filled in below
        harmony_score=harmony_score,
        locked_at=time.time(),
        locked_by=locked_by,
        notes=notes,
    )
    # Sign the record together with the snapshot so tampering with either
    # the record fields (harmony_score, notes, ...) or the snapshot breaks
    # the signature.
    record_bytes = json.dumps({
        "config_id": record.config_id,
        "snapshot_path": record.snapshot_path,
        "harmony_score": record.harmony_score,
        "locked_at": record.locked_at,
        "locked_by": record.locked_by,
        "notes": record.notes,
        "snapshot": _snapshot_bytes(snap).decode("utf-8"),
    }, sort_keys=True).encode("utf-8")
    record.signature = _sign(record_bytes, key)
    lock_path = os.path.join(lock_dir, f"{snap.config_id}.lock.json")
    record.save(lock_path)
    return lock_path


def verify_lock(snap_now: Snapshot, lock_path: str, *, key: bytes) -> tuple[bool, str]:
    """Verify the live snapshot matches the locked one. Returns (ok, reason)."""
    if not os.path.exists(lock_path):
        return False, "no lock file"
    record = LockRecord.load(lock_path)
    if not os.path.exists(record.snapshot_path):
        return False, f"snapshot missing: {record.snapshot_path}"
    snap_locked = Snapshot.load(record.snapshot_path)
    expected_sig = _sign(json.dumps({
        "config_id": record.config_id,
        "snapshot_path": record.snapshot_path,
        "harmony_score": record.harmony_score,
        "locked_at": record.locked_at,
        "locked_by": record.locked_by,
        "notes": record.notes,
        "snapshot": _snapshot_bytes(snap_locked).decode("utf-8"),
    }, sort_keys=True).encode("utf-8"), key)
    if not hmac.compare_digest(expected_sig, record.signature):
        return False, "signature mismatch - lock file tampered"
    # Compare current snapshot to locked snapshot.
    if snap_now.settings != snap_locked.settings or snap_now.prompts != snap_locked.prompts:
        diff = snap_locked.diff(snap_now)
        n_settings = len(diff["settings"])
        n_prompts = len(diff["prompts"])
        return False, f"config drift: {n_settings} settings changed, {n_prompts} prompts changed"
    return True, "ok"


def generate_key() -> str:
    """Generate a new lock key. Print and store securely."""
    import secrets
    return secrets.token_hex(32)