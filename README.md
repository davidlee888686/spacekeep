# SpaceKeep

**Free and MIT licensed.** A small, offline Windows command-line tool that finds **exact duplicate copies** of your files sitting in old backup, recovery and archive folders, so you can win back disk space safely.

SpaceKeep never deletes anything. By default it only prints a plan.

## What it does

1. You point it at your **keeper** folders (the copies you want to keep) and your **scan roots** (old backups, recovered files, archive dumps).
2. It groups files by size, then fingerprints the candidates with **SHA-256**. Only byte-for-byte identical files count as duplicates. Names and dates don't matter.
3. It lists each duplicate group: which copy it keeps and which extra copies could be moved.
4. Only if you ask (`--apply`), it **moves** the extras into a quarantine folder and writes a manifest. You review the quarantine, and you decide what to throw away.

## Safety

- **Dry run is the default.** Nothing moves unless you pass `--apply`.
- **Never deletes.** `--apply` only moves verified extras into a quarantine folder you choose.
- **Quarantine + restore.** Every move is logged to `spacekeep_manifest.jsonl` in the quarantine folder. `--restore <manifest>` puts every file back where it came from (it never overwrites anything already there).
- **Double hash check.** Each extra and its keeper are re-hashed right before a move. If anything changed, the file is left alone.
- **Only recovery-looking copies move.** An extra must sit in a folder that looks like a backup/archive/recovery/copy tree. Duplicates elsewhere are reported but left in place.
- **Protected paths.** Keeper folders, `--protected` folders and `--protect-name` folder names are never moved. On Windows, `C:\Windows`, `C:\Program Files`, `C:\Program Files (x86)` and `C:\ProgramData` are protected by default.
- **Forbidden drives.** `--forbid-drive E F` makes SpaceKeep refuse to read or move anything on those drives.
- **Free-space floor.** It stops if the system drive has less than 5 GB free (change with `--min-free-gb`).
- Locked or unreadable files are skipped and reported.
- Fully offline. No network access, no telemetry, no third-party packages.

## Install

You need Python 3.9 or newer. No other packages.

```text
git clone https://github.com/davidlee888686/spacekeep
cd spacekeep
python spacekeep.py --help
```

Or download `spacekeep.exe` from the [Releases page](https://github.com/davidlee888686/spacekeep/releases) if one is attached, and run it the same way (`spacekeep.exe --help`).

## Use

**1. Dry run (safe, just a report):**

```text
python spacekeep.py --keepers C:\Users\YOU\Documents --roots D:\Old_Backups --dry-run
```

Or copy `spacekeep_config.example.json`, edit the folders, and run:

```text
python spacekeep.py --config my_config.json --dry-run --json-out plan.json
```

**2. Apply (moves extras to quarantine, after you've read the dry run):**

```text
python spacekeep.py --config my_config.json --apply --quarantine D:\SpaceKeep_Quarantine
```

**3. Changed your mind? Restore:**

```text
python spacekeep.py --restore D:\SpaceKeep_Quarantine\spacekeep_manifest.jsonl
```

Once you're happy, you can delete the quarantine folder yourself.

## Tests

```text
python -m pytest -q
```

## What it does not do

- It doesn't empty the Recycle Bin, uninstall programs or clean system/temp files.
- It doesn't run automatically or on a schedule.
- It doesn't delete files. Ever.

## Contact

Questions, bugs or ideas: open an [Issue](https://github.com/davidlee888686/spacekeep/issues) or start a [Discussion](https://github.com/davidlee888686/spacekeep/discussions).

## Sibling projects

- [Serenie](https://github.com/davidlee888686/serenie-cli): free, local file fingerprinting with SHA-256 and a hash-chained ledger you can verify later.
- **Aria**, a private, honest desktop assistant, is coming: [join the beta list](https://github.com/davidlee888686/serenie-cli/discussions/6).

## Support

Free and MIT licensed. If it helps you, an optional tip goes toward a faster PC for development: [Venmo](https://venmo.com/u/David-CAMPBELL-967)

## License

MIT. See [LICENSE](LICENSE).
