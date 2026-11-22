#!/usr/bin/env python3
"""SpaceKeep — find verified duplicate recovery copies. Dry-run by default.

SpaceKeep never deletes anything. Apply mode only moves already
hash-verified extras into a quarantine folder (with a JSONL manifest), and
--restore moves them back. It stops if the system drive is under a free-space
floor (5 GB by default).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

CHUNK = 1024 * 1024
DEFAULT_MIN_FREE_BYTES = 5 * 1024 * 1024 * 1024
# Filled per run from --forbid-drive / config "forbidden_drives".
FORBIDDEN_DRIVE_LETTERS: Set[str] = set()
# Folder names that are always protected, wherever they appear. Filled per run
# from --protect-name / config "protected_names".
PROTECTED_NAMES: Set[str] = set()
BACKUP_HINT_RE = re.compile(
    r"(backup|salvage|archive|recovery|from_[a-z]|hold_|approved_unique|"
    r"unique_archives|copy|copies|_old|\.bak$|sidecars)",
    re.IGNORECASE,
)

DEFAULT_PROTECTED_WINDOWS = [
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
]


@dataclass
class FileInfo:
    path: Path
    size: int
    mtime: float
    digest: Optional[str] = None
    protected: bool = False
    backupish: bool = False


@dataclass
class PlanItem:
    digest: str
    keeper: Path
    extras: List[Path]
    size: int


@dataclass
class Plan:
    items: List[PlanItem] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def extra_count(self) -> int:
        return sum(len(i.extras) for i in self.items)

    @property
    def extra_bytes(self) -> int:
        return sum(i.size * len(i.extras) for i in self.items)


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def normalize(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser()


def drive_letter(path: Path) -> Optional[str]:
    s = str(path).replace("/", "\\")
    match = re.search(r"(?:^|[\\/])([A-Za-z]):", s)
    if match:
        return match.group(1).upper()
    s2 = str(path)
    if len(s2) >= 2 and s2[1] == ":":
        return s2[0].upper()
    return None


def is_forbidden_drive(path: Path) -> bool:
    letter = drive_letter(path)
    return letter in FORBIDDEN_DRIVE_LETTERS


def load_config(path: Optional[Path]) -> dict:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config must be a JSON object")
    return data


def system_root_from_args(args: argparse.Namespace) -> Path:
    if args.system_root:
        return Path(args.system_root)
    if os.name == "nt":
        return Path("C:/")
    return Path("/")


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(str(path)).free


def check_free_space(system_root: Path, minimum: int) -> None:
    available = free_bytes(system_root)
    if available < minimum:
        raise SystemExit(
            f"SpaceKeep stopped: {system_root} has {available} bytes free, "
            f"which is under the {minimum} byte safety floor. "
            "Free space and run again."
        )


def is_protected_path(path: Path, protected_roots: Sequence[Path]) -> bool:
    resolved = normalize(path)
    for part in resolved.parts:
        if part.lower() in PROTECTED_NAMES:
            return True
    for root in protected_roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    # Also treat the protected root itself as protected
    return False


def looks_like_backup(path: Path) -> bool:
    return bool(BACKUP_HINT_RE.search(str(path)))


def iter_files(root: Path, errors: List[str]) -> Iterable[Path]:
    if not root.exists():
        errors.append(f"scan root missing: {root}")
        return
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(dirpath)
        if is_forbidden_drive(current):
            dirnames[:] = []
            errors.append(f"skipped forbidden drive under {current}")
            continue
        # Do not descend into a protected project folder when it appears as a
        # scan target for extras; keepers are collected separately.
        # We still walk them if the caller marked the root as a keeper root.
        keep = []
        for name in dirnames:
            child = current / name
            if is_forbidden_drive(child):
                errors.append(f"skipped forbidden drive {child}")
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            yield current / name


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def collect(
    roots: Sequence[Path],
    protected_roots: Sequence[Path],
    errors: List[str],
    as_keepers: bool,
) -> List[FileInfo]:
    out: List[FileInfo] = []
    for root in roots:
        root = normalize(root)
        if is_forbidden_drive(root):
            errors.append(f"refused forbidden drive: {root}")
            continue
        if not root.exists():
            errors.append(f"{'keeper' if as_keepers else 'scan'} root missing: {root}")
            continue
        if root.is_file():
            paths = [root]
        else:
            paths = list(iter_files(root, errors))
        for path in paths:
            if is_forbidden_drive(path):
                continue
            try:
                st = path.stat()
            except OSError as exc:
                errors.append(f"stat failed: {path} ({exc})")
                continue
            if not path.is_file():
                continue
            protected = is_protected_path(path, protected_roots) or as_keepers
            out.append(
                FileInfo(
                    path=path,
                    size=st.st_size,
                    mtime=st.st_mtime,
                    protected=protected,
                    backupish=looks_like_backup(path),
                )
            )
    return out


def hash_needed(files: Sequence[FileInfo], errors: List[str]) -> None:
    by_size: Dict[int, List[FileInfo]] = defaultdict(list)
    for info in files:
        by_size[info.size].append(info)
    for group in by_size.values():
        if len(group) < 2:
            continue
        for info in group:
            try:
                info.digest = sha256_file(info.path)
            except OSError as exc:
                errors.append(f"hash failed: {info.path} ({exc})")


def choose_keeper(group: Sequence[FileInfo]) -> FileInfo:
    protected = [f for f in group if f.protected]
    if protected:
        return max(protected, key=lambda f: f.mtime)
    live = [f for f in group if not f.backupish]
    if live:
        return max(live, key=lambda f: f.mtime)
    return max(group, key=lambda f: f.mtime)


def build_plan(files: Sequence[FileInfo]) -> Plan:
    plan = Plan()
    by_digest: Dict[str, List[FileInfo]] = defaultdict(list)
    for info in files:
        if info.digest:
            by_digest[info.digest].append(info)
    for digest, group in sorted(by_digest.items(), key=lambda kv: -kv[1][0].size):
        if len(group) < 2:
            continue
        keeper = choose_keeper(group)
        extras: List[Path] = []
        for info in group:
            if info.path == keeper.path:
                continue
            if info.protected:
                plan.skipped.append(f"protected extra left in place: {info.path}")
                continue
            if not info.backupish:
                plan.skipped.append(
                    f"non-backup extra left in place (not a recovery copy): {info.path}"
                )
                continue
            extras.append(info.path)
        if extras:
            plan.items.append(
                PlanItem(digest=digest, keeper=keeper.path, extras=extras, size=keeper.size)
            )
    return plan


def format_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{n} B"


def print_plan(plan: Plan) -> None:
    if not plan.items:
        print("No verified recovery-copy duplicates found.")
    else:
        print(f"Verified duplicate recovery copies: {plan.extra_count} files, {format_bytes(plan.extra_bytes)}")
        for item in plan.items:
            print(f"\nSHA-256 {item.digest}  ({format_bytes(item.size)})")
            print(f"  KEEP  {item.keeper}")
            for extra in item.extras:
                print(f"  MOVE  {extra}")
    if plan.skipped:
        print("\nLeft in place:")
        for line in plan.skipped:
            print(f"  {line}")
    if plan.errors:
        print("\nWarnings:")
        for line in plan.errors:
            print(f"  {line}")


def confirm_still_duplicate(extra: Path, expected: str) -> bool:
    try:
        return sha256_file(extra) == expected
    except OSError:
        return False


def unique_dest(folder: Path, source: Path) -> Path:
    dest = folder / source.name
    if not dest.exists():
        return dest
    stem, suffix = source.stem, source.suffix
    n = 1
    while True:
        cand = folder / f"{stem}__{n}{suffix}"
        if not cand.exists():
            return cand
        n += 1


def apply_plan(
    plan: Plan,
    quarantine: Path,
    errors: List[str],
    protected_roots: Sequence[Path] = (),
) -> Tuple[int, int]:
    quarantine.mkdir(parents=True, exist_ok=True)
    manifest_path = quarantine / "spacekeep_manifest.jsonl"
    moved = 0
    bytes_moved = 0
    with manifest_path.open("a", encoding="utf-8") as man:
        for item in plan.items:
            if not item.keeper.exists():
                errors.append(f"keeper missing, skipped group {item.digest}: {item.keeper}")
                continue
            try:
                keeper_hash = sha256_file(item.keeper)
            except OSError as exc:
                errors.append(f"could not re-hash keeper {item.keeper}: {exc}")
                continue
            if keeper_hash != item.digest:
                errors.append(f"keeper hash changed, skipped group {item.digest}: {item.keeper}")
                continue
            for extra in item.extras:
                extra = Path(extra)
                if is_protected_path(extra, protected_roots):
                    errors.append(f"refused to move protected path: {extra}")
                    continue
                if is_forbidden_drive(extra):
                    errors.append(f"refused forbidden drive: {extra}")
                    continue
                if not extra.exists():
                    errors.append(f"extra already gone: {extra}")
                    continue
                if not confirm_still_duplicate(extra, item.digest):
                    errors.append(f"hash no longer matches, left in place: {extra}")
                    continue
                dest = unique_dest(quarantine, extra)
                try:
                    shutil.move(str(extra), str(dest))
                except OSError as exc:
                    errors.append(f"move failed: {extra} -> {dest} ({exc})")
                    continue
                record = {
                    "digest": item.digest,
                    "size": item.size,
                    "keeper": str(item.keeper),
                    "source": str(extra),
                    "quarantine": str(dest),
                    "moved_at_unix": int(time.time()),
                }
                man.write(json.dumps(record) + "\n")
                moved += 1
                bytes_moved += item.size
    return moved, bytes_moved


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "SpaceKeep finds hash-verified duplicate recovery copies. "
            "Dry-run is the default. It never deletes files; --apply only moves verified extras to a quarantine folder."
        )
    )
    p.add_argument("--config", type=Path, help="JSON config with roots, keepers, and protected paths")
    p.add_argument("--roots", nargs="*", default=[], help="Folders to scan for recovery copies")
    p.add_argument("--keepers", nargs="*", default=[], help="Live folders that must be kept (hashed as originals)")
    p.add_argument("--protected", nargs="*", default=[], help="Extra protected roots that must never be moved")
    p.add_argument("--system-root", help="Drive or path whose free space is checked (default C:\\ on Windows, / elsewhere)")
    p.add_argument("--min-free-gb", type=float, default=5.0, help="Abort if system root has less than this many GB free")
    p.add_argument("--dry-run", action="store_true", default=True, help="Print the plan only (default)")
    p.add_argument("--apply", action="store_true", help="Move verified extras into --quarantine (not a raw delete)")
    p.add_argument(
        "--quarantine",
        type=Path,
        help="Folder to move verified extras into when --apply is set",
    )
    p.add_argument("--json-out", type=Path, help="Write the plan as JSON")
    p.add_argument(
        "--forbid-drive",
        nargs="*",
        default=[],
        help="Drive letters SpaceKeep must never read or move from (e.g. E F)",
    )
    p.add_argument(
        "--protect-name",
        nargs="*",
        default=[],
        help="Folder names that are always protected wherever they appear",
    )
    p.add_argument(
        "--restore",
        metavar="MANIFEST",
        help="Move every file listed in a quarantine manifest back to where it came from",
    )
    return p.parse_args(argv)


def restore_from_manifest(manifest: Path) -> int:
    """Move quarantined files back to their original locations.

    Never overwrites: if something already exists at the original path, the
    file stays in quarantine and a warning is printed.
    """
    if not manifest.is_file():
        eprint(f"Manifest not found: {manifest}")
        return 2
    restored = 0
    problems: List[str] = []
    remaining: List[str] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            src = Path(rec["quarantine"])
            dest = Path(rec["source"])
        except (ValueError, KeyError) as exc:
            problems.append(f"bad manifest line skipped ({exc})")
            remaining.append(line)
            continue
        if not src.exists():
            problems.append(f"not in quarantine (already restored?): {src}")
            continue
        if dest.exists():
            problems.append(f"original path is occupied, left in quarantine: {dest}")
            remaining.append(line)
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        except OSError as exc:
            problems.append(f"restore failed: {src} -> {dest} ({exc})")
            remaining.append(line)
            continue
        restored += 1
    backup = manifest.with_suffix(manifest.suffix + f".restored_{int(time.time())}")
    shutil.copy2(str(manifest), str(backup))
    manifest.write_text("".join(l + "\n" for l in remaining), encoding="utf-8")
    print(f"Restored {restored} file(s). Previous manifest saved as {backup.name}.")
    for p in problems:
        print(f"  {p}")
    return 0


def merge_paths(from_config: Sequence[str], from_args: Sequence[str]) -> List[Path]:
    merged: List[Path] = []
    seen: Set[str] = set()
    for raw in list(from_config) + list(from_args):
        path = normalize(Path(raw))
        key = str(path).lower()
        if key not in seen:
            merged.append(path)
            seen.add(key)
    return merged


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    cfg = load_config(args.config)
    FORBIDDEN_DRIVE_LETTERS.clear()
    FORBIDDEN_DRIVE_LETTERS.update(
        str(d).strip().rstrip(":\\/").upper()
        for d in list(cfg.get("forbidden_drives", [])) + list(args.forbid_drive)
        if str(d).strip()
    )
    PROTECTED_NAMES.clear()
    PROTECTED_NAMES.update(
        str(n).strip().lower()
        for n in list(cfg.get("protected_names", [])) + list(args.protect_name)
        if str(n).strip()
    )
    if args.restore:
        return restore_from_manifest(Path(args.restore))
    system_root = system_root_from_args(args)
    minimum = int(args.min_free_gb * 1024 * 1024 * 1024)
    try:
        check_free_space(system_root, minimum)
    except SystemExit as exc:
        eprint(str(exc))
        return 2

    protected = merge_paths(
        cfg.get("protected", DEFAULT_PROTECTED_WINDOWS if os.name == "nt" else []),
        args.protected,
    )
    roots = merge_paths(cfg.get("roots", []), args.roots)
    keepers = merge_paths(cfg.get("keepers", []), args.keepers)
    if not roots:
        eprint("No scan roots given. Pass --roots or a --config file. See README.md.")
        return 2
    for path in roots + keepers + protected:
        if is_forbidden_drive(path):
            eprint(f"Refused forbidden drive: {path}")
            return 2

    errors: List[str] = []
    files = collect(keepers, protected, errors, as_keepers=True)
    files.extend(collect(roots, protected, errors, as_keepers=False))
    hash_needed(files, errors)
    plan = build_plan(files)
    plan.errors.extend(errors)
    print_plan(plan)

    if args.json_out:
        payload = {
            "extra_count": plan.extra_count,
            "extra_bytes": plan.extra_bytes,
            "items": [
                {
                    "digest": i.digest,
                    "size": i.size,
                    "keeper": str(i.keeper),
                    "extras": [str(p) for p in i.extras],
                }
                for i in plan.items
            ],
            "skipped": plan.skipped,
            "errors": plan.errors,
        }
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWrote plan JSON to {args.json_out}")

    if not args.apply:
        print("\nDry-run only. Nothing was moved or deleted.")
        return 0

    if not args.quarantine:
        eprint("--apply requires --quarantine <folder>")
        return 2
    quarantine = normalize(args.quarantine)
    if is_forbidden_drive(quarantine):
        eprint(f"Refused forbidden quarantine drive: {quarantine}")
        return 2
    if is_protected_path(quarantine, protected):
        eprint(f"Refused quarantine inside a protected path: {quarantine}")
        return 2
    check_free_space(system_root, minimum)
    if free_bytes(quarantine if quarantine.exists() else quarantine.parent) < minimum:
        eprint("SpaceKeep stopped: quarantine destination is under the free-space floor.")
        return 2
    moved, nbytes = apply_plan(plan, quarantine, plan.errors, protected)
    print(f"\nMoved {moved} verified extras ({format_bytes(nbytes)}) to {quarantine}")
    print("Nothing was permanently deleted. Review the quarantine folder and its manifest before discarding anything.")
    if plan.errors:
        print("Warnings during apply:")
        for line in plan.errors:
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
