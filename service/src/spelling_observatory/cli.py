"""Command-line interface: `observatory <command>`."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import date
from pathlib import Path

from .config import ensure_home, load_config
from .resources import fetch_resources


def _user_exclusions(cfg) -> set[str]:
    if not cfg.user_exclusions_path.exists():
        return set()
    return {l.strip().lower() for l in cfg.user_exclusions_path.read_text().splitlines()
            if l.strip() and not l.startswith("#")}


def _analyzer(cfg, no_llm: bool = False):
    from .classify.pipeline import Analyzer
    if no_llm:
        cfg.llm.enabled = False
    return Analyzer(cfg, _user_exclusions(cfg))


def _store(cfg):
    from .store.db import connect
    return connect(cfg)


def _scope(store, args):
    from .analysis.metrics import Scope
    cv = args.classifier_version or store.latest_classifier_version()
    if cv is None:
        sys.exit("No data yet. Run `observatory import-chatgpt <export>` first.")
    return Scope(cv, args.provenance, args.stage, args.domain)


def cmd_setup(cfg, args):
    ensure_home(cfg)
    fetch_resources(cfg.resources_dir)
    store = _store(cfg)
    store.close()
    print(f"Home: {cfg.home}\nConfig: {cfg.config_path}\nDatabase: {cfg.database_path} "
          f"(encryption: {cfg.storage.encryption})\nPersonal exclusions: {cfg.user_exclusions_path}")


def cmd_import(cfg, args):
    from .importers.chatgpt import import_chatgpt
    an = _analyzer(cfg, args.no_llm)
    store = _store(cfg)
    print(f"Classifier fingerprint {an.fingerprint()} (LLM: {'on' if an.llm else 'off'})")
    stats = import_chatgpt(Path(args.path), an, store, cfg.report.timezone)
    for k, v in stats.items():
        print(f"  {k}: {v}")
    store.close()


def cmd_report(cfg, args):
    from .report.build import build, text_summary
    from .report.html import render
    store = _store(cfg)
    scope = _scope(store, args)
    if args.baseline_start:
        cfg.report.baseline_start = args.baseline_start
    if args.baseline_end:
        cfg.report.baseline_end = args.baseline_end
    res = build(store, cfg, scope)
    print(text_summary(res))
    if args.html:
        out = Path(args.html)
        out.write_text(render(res))
        os.chmod(out, 0o600)
        print(f"\nHTML report written to {out}")
    store.close()


def cmd_classify(cfg, args):
    an = _analyzer(cfg, args.no_llm)
    a = an.analyze(args.text)
    an.resolve_pending([a])
    for w in a.words:
        if w.is_error:
            print(f"ERROR {w.observed!r} -> {w.intended!r}  category {w.category}  ({w.resolution}; {w.reason}; "
                  f"{w.edit_ops}) candidates={w.candidates}")
        else:
            print(f"ok    {w.observed}")
    print("excluded:", dict(a.excluded))


def cmd_cohort(cfg, args):
    from .analysis.mastered import freeze_cohort
    store = _store(cfg)
    scope = _scope(store, args)
    cid = freeze_cohort(store, scope, args.name, date.fromisoformat(args.start), date.fromisoformat(args.end),
                        cfg.mastered.min_baseline_observations, cfg.mastered.min_baseline_accuracy)
    n = store.conn.execute("SELECT COUNT(*) FROM cohort_words WHERE cohort_id=?", (cid,)).fetchone()[0]
    print(f"Cohort {args.name!r} frozen with {n} words.")


def cmd_exclude(cfg, args):
    ensure_home(cfg)
    existing = _user_exclusions(cfg)
    store = _store(cfg)
    with open(cfg.user_exclusions_path, "a") as fh:
        for w in args.words:
            if w.lower() not in existing:
                fh.write(w.lower() + "\n")
            n = store.apply_exclusion(w)
            print(f"{w}: excluded; {n} stored observations removed")


def cmd_unknowns(cfg, args):
    store = _store(cfg)
    cv = store.latest_classifier_version()
    for tok, n, first, last in store.conn.execute(
            "SELECT token, n, first_date, last_date FROM unknown_tokens WHERE classifier_version_id=? "
            "ORDER BY n DESC LIMIT ?", (cv, args.limit)):
        print(f"{n:6}  {tok:24} {first} .. {last}")


def cmd_versions(cfg, args):
    store = _store(cfg)
    for vid, fp, created, n, _ in store.classifier_versions():
        print(f"#{vid}  {fp}  created {created}  texts={n}")


def cmd_prune(cfg, args):
    store = _store(cfg)
    keep = args.keep or store.latest_classifier_version()
    n = store.prune_versions(keep)
    print(f"Removed {n} texts from classifier versions other than #{keep}.")


def cmd_export(cfg, args):
    store = _store(cfg)
    store.export_json(Path(args.out))
    print(f"Exported to {args.out} (unencrypted JSON, mode 0600).")


def cmd_delete_all(cfg, args):
    if not args.yes:
        sys.exit("This permanently deletes the database, configuration and resources. Re-run with --yes.")
    db_path = cfg.database_path
    shutil.rmtree(cfg.home, ignore_errors=True)
    cfg.home.mkdir(parents=True, exist_ok=True)  # resolve() in keyring_account needs an existing parent
    if cfg.storage.encryption == "sqlcipher":
        try:
            import keyring
            from .store.db import KEYRING_SERVICE, keyring_account
            keyring.delete_password(KEYRING_SERVICE, keyring_account(db_path))
        except Exception:
            pass
    shutil.rmtree(cfg.home, ignore_errors=True)
    print(f"Deleted {cfg.home}.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="observatory", description="Personal Spelling Observatory (local only)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def scoped(sp):
        sp.add_argument("--domain", default=None, help="restrict to one website domain (default: all)")
        sp.add_argument("--provenance", default="historical_submitted", choices=["historical_submitted", "prospective"])
        sp.add_argument("--stage", default="submitted", choices=["submitted", "initial", "final"])
        sp.add_argument("--classifier-version", type=int, default=None)

    sub.add_parser("setup", help="create config, database and fetch pinned dictionaries").set_defaults(fn=cmd_setup)
    sp = sub.add_parser("import-chatgpt", help="import a ChatGPT data export (.zip, directory or conversations.json)")
    sp.add_argument("path")
    sp.add_argument("--no-llm", action="store_true", help="disable local-LLM disambiguation for this run")
    sp.set_defaults(fn=cmd_import)
    sp = sub.add_parser("report", help="print a summary and optionally write an HTML report")
    scoped(sp)
    sp.add_argument("--html", help="write the HTML report to this path")
    sp.add_argument("--baseline-start")
    sp.add_argument("--baseline-end")
    sp.set_defaults(fn=cmd_report)
    sp = sub.add_parser("classify", help="debug: analyse a piece of text and show every decision")
    sp.add_argument("text")
    sp.add_argument("--no-llm", action="store_true")
    sp.set_defaults(fn=cmd_classify)
    sp = sub.add_parser("cohort", help="freeze a mastered-word cohort from a baseline window")
    scoped(sp)
    sp.add_argument("name")
    sp.add_argument("start")
    sp.add_argument("end")
    sp.set_defaults(fn=cmd_cohort)
    sp = sub.add_parser("exclude", help="add words to the personal exclusion dictionary (applied retroactively)")
    sp.add_argument("words", nargs="+")
    sp.set_defaults(fn=cmd_exclude)
    sp = sub.add_parser("unknowns", help="list frequent out-of-vocabulary tokens (candidates for exclusion)")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(fn=cmd_unknowns)
    sub.add_parser("versions", help="list classifier versions in the database").set_defaults(fn=cmd_versions)
    sp = sub.add_parser("prune", help="delete observations from all classifier versions except one")
    sp.add_argument("--keep", type=int)
    sp.set_defaults(fn=cmd_prune)
    sp = sub.add_parser("export", help="export all stored data as JSON")
    sp.add_argument("out")
    sp.set_defaults(fn=cmd_export)
    sp = sub.add_parser("delete-all", help="delete all data, configuration and the database key")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(fn=cmd_delete_all)

    args = p.parse_args(argv)
    cfg = load_config()
    args.fn(cfg, args)


if __name__ == "__main__":
    main()
