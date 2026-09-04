#!/usr/bin/env python3
from __future__ import annotations
"""
Dr. Pie KB hybrid search: FTS5 multi-field + RRF (+ optional cosine vector).
Usage:
  python3 search.py <hotspot|papers> <query> [--limit N] [--embed-json <path>] [--embed-text TEXT] [--debug]

<query>        search string (Chinese or English)
--limit N      max results to return (default 10)
--embed-json   path to a JSON file containing a float list (query embedding);
               when provided, adds cosine similarity ranking via RRF
--embed-text   text to embed on-the-fly via embed.py for semantic search
--debug        print per-source intermediate results to stderr (fts / vec / rrf breakdown)
"""
import sys
import os
import sqlite3
import json
import argparse
import math
import struct
import subprocess


_DEBUG = False  # set to True via --debug flag


def _dbg(msg: str) -> None:
    if _DEBUG:
        print(msg, file=sys.stderr)


def get_kb_dir() -> str:
    if d := os.environ.get("BIGPIE_KB_DIR"):
        return d
    # Electron derives userData from package.json's canonical `dr-pie` name.
    # Keep standalone skill runs on the same cache that the desktop app syncs.
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/dr-pie/kb-cache")
    if sys.platform == "win32":
        app_data = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
        return os.path.join(app_data, "dr-pie", "kb-cache")
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config_home, "drpie", "kb-cache")


# ── On-demand S3 sync (triggered when snapshot.db is missing at query time) ──
_S3_BASE = os.environ.get("BIGPIE_KB_S3_BASE", "")
_MANIFEST_URLS = {
    "hotspot": f"{_S3_BASE}/drpie-hotspot/manifest.json",
    "papers": f"{_S3_BASE}/drpie-paper-library/manifest.json",
}


def _s3_get(url: str, timeout: int = 60) -> bytes | None:
    try:
        import ssl
        import urllib.request
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": "drpie-kb/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read()
    except Exception as e:
        print(f"[KB:sync] download error: {e}", file=sys.stderr)
        return None


def _check_db_valid(path: str) -> bool:
    """Return True if the SQLite file at path exists, is openable, and passes quick_check."""
    if not os.path.exists(path):
        return False
    # Guard against TOCTOU where sqlite3.connect() creates a new empty file if the
    # path was deleted between os.path.exists() and connect().  A valid SQLite DB is
    # at least one 1024-byte page; 100 bytes is a safe floor that also catches the
    # empty-file case without opening it.
    try:
        if os.path.getsize(path) < 100:
            return False
    except Exception:
        return False
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("PRAGMA quick_check(1)").fetchone()
            return row is not None and row[0] == "ok"
        finally:
            conn.close()
    except Exception:
        return False


def _count_snapshot_rows(path: str, mode: str) -> int | None:
    """Return row count in the primary snapshot table, or None on failure."""
    table = "hotspot_items" if mode == "hotspot" else "papers"
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            return int(row[0]) if row else None
        finally:
            conn.close()
    except Exception:
        return None


def _cleanup_stale_temps(mode: str) -> None:
    """Delete leftover .tmp / .tmp-shm / .tmp-wal files from previously crashed downloads."""
    target_dir = os.path.join(get_kb_dir(), mode)
    try:
        for name in os.listdir(target_dir):
            if name.endswith(".tmp") or name.endswith(".tmp-shm") or name.endswith(".tmp-wal"):
                try:
                    os.unlink(os.path.join(target_dir, name))
                except Exception:
                    pass
    except Exception:
        pass


def _clean_tmp_wal(tmp: str) -> None:
    """Remove SQLite WAL sidecar files (.tmp-shm/.tmp-wal) left after opening a .tmp file."""
    for suffix in ("-shm", "-wal"):
        try:
            os.unlink(tmp + suffix)
        except Exception:
            pass


def _ensure_vec_if_missing(mode: str) -> None:
    """Download vec.db if it is absent or corrupt."""
    import gzip
    target_dir = os.path.join(get_kb_dir(), mode)
    vec_path_candidate = os.path.join(target_dir, "vec.db")

    # Skip if vec.db exists and passes integrity check
    if os.path.exists(vec_path_candidate):
        if _check_db_valid(vec_path_candidate):
            return
        # Corrupt — keep old file in place; os.replace() will atomically overwrite
        # if re-download succeeds. This way search degrades gracefully (FTS-only)
        # if download fails, rather than losing all vec data.
        print(f"[KB:sync] {mode} vec.db is corrupt — re-downloading...", file=sys.stderr)

    # Try to read URL from cached manifest first (avoids extra S3 round-trip)
    manifest: dict = {}
    manifest_file = os.path.join(target_dir, "manifest.json")
    try:
        with open(manifest_file) as f:
            manifest = json.load(f)
    except Exception:
        body = _s3_get(_MANIFEST_URLS.get(mode, ""), timeout=15)
        if body:
            try:
                manifest = json.loads(body)
            except Exception:
                return
    if mode == "hotspot":
        vec_url = manifest.get("vecSnapshotUrl")
    else:
        vec_key = manifest.get("vec_snapshot")
        vec_url = f"{_S3_BASE}/drpie-paper-library/{vec_key}" if vec_key else None
    if not vec_url:
        return
    print(f"[KB:sync] {mode} vec.db missing — downloading...", file=sys.stderr)
    gz = _s3_get(vec_url, timeout=180)
    if not gz:
        return
    try:
        vec_data = gzip.decompress(gz)
        vec_path = vec_path_candidate
        tmp = vec_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(vec_data)
        if not _check_db_valid(tmp):
            print(f"[KB:sync] {mode} vec.db download validation failed", file=sys.stderr)
            try:
                os.unlink(tmp)
            except Exception:
                pass
            return
        os.replace(tmp, vec_path)
        _clean_tmp_wal(tmp)  # remove WAL sidecars left by _check_db_valid opening .tmp
        # Write vec-manifest.json so the TypeScript startup sync skips re-downloading
        # on next launch (TS uses this file to version-gate syncVec).
        vec_version = str(manifest.get("version", ""))
        if vec_version:
            try:
                with open(os.path.join(target_dir, "vec-manifest.json"), "w") as vf:
                    json.dump({"version": vec_version}, vf)
            except Exception:
                pass
        print(f"[KB:sync] {mode} vec.db saved ({len(vec_data)//1024//1024} MB)", file=sys.stderr)
    except Exception as e:
        print(f"[KB:sync] vec.db download failed: {e}", file=sys.stderr)


def ensure_snapshot(mode: str) -> bool:
    """Return True if snapshot.db is available, downloading from S3 if missing or corrupt."""
    import gzip
    # Clean up .tmp files left by crashed previous runs before doing anything else.
    _cleanup_stale_temps(mode)
    db_path = os.path.join(get_kb_dir(), mode, "snapshot.db")

    if os.path.exists(db_path):
        if not _check_db_valid(db_path):
            # Corrupt — keep file in place as fallback; os.replace() atomically
            # overwrites if re-download succeeds, so search still works (returns [])
            # if download fails rather than having no file at all.
            print(f"[KB:sync] {mode} snapshot.db is corrupt — re-downloading...", file=sys.stderr)
            # Fall through to download
        else:
            # Integrity OK — also validate row count against cached manifest.
            # On mismatch, fall through to re-download WITHOUT deleting the old file.
            redownload = False
            manifest_file = os.path.join(get_kb_dir(), mode, "manifest.json")
            try:
                with open(manifest_file) as f:
                    cached_manifest = json.load(f)
                expected = cached_manifest.get("items") or cached_manifest.get("papers_count")
                if expected is not None:
                    actual = _count_snapshot_rows(db_path, mode)
                    if actual is not None and actual != int(expected):
                        print(f"[KB:sync] {mode} snapshot.db row count mismatch "
                              f"(expected {expected}, got {actual}) — re-downloading...", file=sys.stderr)
                        redownload = True
            except Exception:
                pass  # can't read manifest — trust integrity check alone
            if not redownload:
                _ensure_vec_if_missing(mode)
                return True
            # Fall through to re-download (old file stays as fallback)

    # --- Download section: file missing, corrupt, or count mismatch ---
    manifest_url = _MANIFEST_URLS.get(mode)
    if not manifest_url:
        return False

    body = _s3_get(manifest_url, timeout=15)
    if not body:
        return False

    try:
        manifest = json.loads(body)
    except Exception:
        return False

    if mode == "hotspot":
        snap_url = manifest.get("snapshotUrl") or f"{_S3_BASE}/drpie-hotspot/snapshot.db.gz"
    else:
        snap_url = (
            manifest.get("snapshotUrl")
            or (f"{_S3_BASE}/drpie-paper-library/{manifest['snapshot']}" if manifest.get("snapshot") else None)
            or f"{_S3_BASE}/drpie-paper-library/snapshot.db.gz"
        )

    print(f"[KB:sync] downloading {mode} snapshot (first-time or cache deleted)...", file=sys.stderr)
    gz = _s3_get(snap_url, timeout=180)
    if not gz:
        return False

    try:
        db_data = gzip.decompress(gz)
    except Exception as e:
        print(f"[KB:sync] gunzip failed: {e}", file=sys.stderr)
        return False

    target_dir = os.path.join(get_kb_dir(), mode)
    os.makedirs(target_dir, exist_ok=True)
    tmp = db_path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(db_data)
        if not _check_db_valid(tmp):
            print(f"[KB:sync] {mode} snapshot download validation failed", file=sys.stderr)
            try:
                os.unlink(tmp)
            except Exception:
                pass
            return False
        # Validate row count against manifest before accepting the file
        expected_count = manifest.get("items") or manifest.get("papers_count")
        if expected_count is not None:
            actual_count = _count_snapshot_rows(tmp, mode)
            if actual_count is not None and actual_count != int(expected_count):
                print(f"[KB:sync] {mode} snapshot row count mismatch "
                      f"(expected {expected_count}, got {actual_count})", file=sys.stderr)
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
                return False
        os.replace(tmp, db_path)
        _clean_tmp_wal(tmp)  # remove WAL sidecars left by _check_db_valid opening .tmp
        print(
            f"[KB:sync] {mode} snapshot saved ({len(db_data) // 1024 // 1024} MB)",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"[KB:sync] write failed: {e}", file=sys.stderr)
        try:
            os.unlink(tmp)
        except Exception:
            pass
        return False

    try:
        with open(os.path.join(target_dir, "manifest.json"), "w") as f:
            json.dump(manifest, f, indent=2)
    except Exception:
        pass

    # Download vec.db alongside snapshot.db (embedding index for KNN search)
    if mode == "hotspot":
        vec_url = manifest.get("vecSnapshotUrl")
    else:
        vec_key = manifest.get("vec_snapshot")
        vec_url = f"{_S3_BASE}/drpie-paper-library/{vec_key}" if vec_key else None

    if vec_url:
        print(f"[KB:sync] downloading {mode} vec.db...", file=sys.stderr)
        vec_gz = _s3_get(vec_url, timeout=180)
        if vec_gz:
            try:
                vec_data = gzip.decompress(vec_gz)
                vec_path = os.path.join(target_dir, "vec.db")
                vec_tmp = vec_path + ".tmp"
                with open(vec_tmp, "wb") as f:
                    f.write(vec_data)
                if _check_db_valid(vec_tmp):
                    os.replace(vec_tmp, vec_path)
                    _clean_tmp_wal(vec_tmp)  # remove WAL sidecars
                    # Write vec-manifest.json so TS startup sync skips re-downloading.
                    vec_version = str(manifest.get("version", ""))
                    if vec_version:
                        try:
                            with open(os.path.join(target_dir, "vec-manifest.json"), "w") as vf:
                                json.dump({"version": vec_version}, vf)
                        except Exception:
                            pass
                    print(
                        f"[KB:sync] {mode} vec.db saved ({len(vec_data) // 1024 // 1024} MB)",
                        file=sys.stderr,
                    )
                else:
                    print(f"[KB:sync] {mode} vec.db download validation failed (non-fatal)", file=sys.stderr)
                    try:
                        os.unlink(vec_tmp)
                    except Exception:
                        pass
            except Exception as e:
                print(f"[KB:sync] vec.db failed (non-fatal): {e}", file=sys.stderr)

    return True


CJK_RE = set(range(0x4E00, 0xA000)) | set(range(0x3400, 0x4E00))


def is_cjk(text: str) -> bool:
    return any(ord(c) in CJK_RE for c in text)


def safe_fts_query(raw: str, column_filter: str = "") -> str:
    q = raw.strip()
    if not q:
        return '""'
    if is_cjk(q):
        # Mixed CJK+ASCII (e.g. "Anthropic 最近动态"): phrase-quoting the whole
        # string fails — FTS5 looks for the exact token sequence verbatim.
        # Extract only ASCII tokens; CJK content gets a LIKE fallback in callers.
        ascii_tokens = [t for t in q.split() if t and not is_cjk(t)]
        if ascii_tokens:
            en_q = ' '.join(ascii_tokens)
            return f"{column_filter}:({en_q})" if column_filter else en_q
        # Pure CJK: phrase match is best-effort; callers add a LIKE fallback.
        quoted = q.replace('"', '""')
        phrase = f'"{quoted}"'
        return f"{column_filter}:{phrase}" if column_filter else phrase
    if column_filter:
        # Wrap in parens so column filter applies to the whole expression, not just first token
        return f"{column_filter}:({q})"
    return q


def run_fts(conn: sqlite3.Connection, sql: str, params: tuple) -> list:
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        # FTS5 parse error — retry with full-phrase quote on second param (the query)
        try:
            q_raw = params[0] if params else ""
            quoted = f'"{str(q_raw).replace(chr(34), chr(34)*2)}"'
            return conn.execute(sql, (quoted,) + params[1:]).fetchall()
        except Exception:
            return []


def rrf(rank_lists: list[list], id_key: str, k: int = 60) -> list[str]:
    """Reciprocal Rank Fusion across multiple ranked lists."""
    scores: dict[str, float] = {}
    for ranked in rank_lists:
        for i, item in enumerate(ranked):
            item_id = item[id_key] if isinstance(item, dict) else item
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + i + 1)
    return sorted(scores, key=lambda x: scores[x], reverse=True)


def cosine_sim(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Try to load sqlite-vec extension in-process. Return True on success."""
    vec0 = _find_vec0()
    if vec0 is None:
        return False
    try:
        conn.enable_load_extension(True)
        try:
            conn.load_extension(vec0)
            return True
        finally:
            conn.enable_load_extension(False)
    except Exception:
        return False


# ── sqlite-vec subprocess fallback ───────────────────────────────────────────
# System Python 3.9 on macOS is compiled without enable_load_extension.
# When load_sqlite_vec() fails, we fall back to any Python that supports it
# plus the bundled vec0.dylib (so no sqlite_vec package install is required).

_VEC_PYTHON_CACHE: str | None | bool = False  # False=not yet checked


def _find_vec0() -> str | None:
    """Return path to the vec0 SQLite extension (bundled or installed).

    Bundled in arch subdirectories (arm64/vec0.dylib, x64/vec0.dylib) so
    SQLite always resolves the entry point as sqlite3_vec0_init regardless of arch.
    Selected at runtime via platform.machine().
    """
    import platform
    machine = platform.machine().lower()  # 'arm64' on Apple Silicon, 'x86_64' on Intel
    arch_dir = "arm64" if machine == "arm64" else "x64"

    script_dir = os.path.dirname(os.path.abspath(__file__))
    bundled_base = os.environ.get("BIGPIE_BUNDLED_SKILLS_DIR", "")

    def _dir_candidates(d: str) -> list[str]:
        return [
            os.path.join(d, arch_dir, "vec0.dylib"),  # arch-specific subdir (preferred)
            os.path.join(d, "vec0.dylib"),             # legacy / universal binary
            os.path.join(d, "vec0"),                   # no extension
        ]

    candidates: list[str] = []
    if bundled_base:
        skill_dir = os.path.join(bundled_base, "cs-research", "search-knowledge-base")
        candidates += _dir_candidates(skill_dir)
    candidates += _dir_candidates(script_dir)

    for p in candidates:
        if os.path.exists(p):
            return p
    # Last resort: sqlite_vec Python package
    try:
        import sqlite_vec  # type: ignore
        return sqlite_vec.loadable_path()
    except Exception:
        return None


def _get_vec_python() -> str | None:
    """Return a Python path that supports enable_load_extension, or None."""
    global _VEC_PYTHON_CACHE
    if _VEC_PYTHON_CACHE is not False:
        return _VEC_PYTHON_CACHE  # type: ignore[return-value]
    vec0 = _find_vec0()
    if vec0 is None:
        _VEC_PYTHON_CACHE = None
        return None
    # Check script: try to enable extension loading and load the bundled vec0
    check = (
        "import sqlite3, sys;"
        "c = sqlite3.connect(':memory:');"
        "c.enable_load_extension(True);"
        "c.load_extension(sys.argv[1]);"
        "print('ok')"
    )
    candidates = [
        sys.executable,
        "/opt/homebrew/bin/python3.13",
        "/opt/homebrew/bin/python3.12",
        "/opt/homebrew/bin/python3.11",
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3.13",
        "/usr/local/bin/python3.12",
        "/usr/local/bin/python3.11",
        "/usr/local/bin/python3",
        # Python.org macOS installer paths
        "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
        "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
        "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3",
        "/Library/Frameworks/Python.framework/Versions/3.10/bin/python3",
        # Conda / Miniforge
        os.path.expanduser("~/miniforge3/bin/python3"),
        os.path.expanduser("~/anaconda3/bin/python3"),
        os.path.expanduser("~/miniconda3/bin/python3"),
        "/opt/homebrew/anaconda3/bin/python3",
        "/opt/homebrew/miniconda3/bin/python3",
    ]
    for py in candidates:
        try:
            r = subprocess.run(
                [py, "-c", check, vec0],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and "ok" in r.stdout:
                _VEC_PYTHON_CACHE = py
                return py
        except Exception:
            pass
    _VEC_PYTHON_CACHE = None
    return None


# KNN search script: reads JSON from stdin, writes rowid list to stdout.
# Uses bundled vec0.dylib directly — no sqlite_vec package required.
_KNN_SCRIPT = r"""
import sys, json, sqlite3, struct
d = json.loads(sys.stdin.read())
packed = struct.pack(f"{len(d['emb'])}f", *d['emb'])
conn = sqlite3.connect(d['db'])
conn.enable_load_extension(True)
conn.load_extension(d['vec0'])
conn.enable_load_extension(False)
rows = conn.execute(
    "SELECT rowid FROM " + d['table'] + " WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
    (packed, d['limit'])
).fetchall()
rowids = [int(r[0]) for r in rows]
if d.get('id_table'):
    ph = ','.join('?' * len(rowids))
    mr = conn.execute(
        f"SELECT rowid, {d['id_col']} FROM {d['id_table']} WHERE rowid IN ({ph})", rowids
    ).fetchall() if rowids else []
    m = {int(r[0]): str(r[1]) for r in mr}
    print(json.dumps([m[i] for i in rowids if i in m]))
else:
    print(json.dumps(rowids))
"""


def _knn_subprocess(payload: dict) -> list:
    """Run KNN via a subprocess Python with enable_load_extension support."""
    py = _get_vec_python()
    if not py:
        return []
    vec0 = _find_vec0()
    if not vec0:
        return []
    try:
        r = subprocess.run(
            [py, "-c", _KNN_SCRIPT],
            input=json.dumps({**payload, "vec0": vec0}),
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return json.loads(r.stdout)
    except Exception:
        pass
    return []


def get_vec_db_path(mode: str) -> str | None:
    """Return path to vec.db for the given mode, or None if file doesn't exist."""
    path = os.path.join(get_kb_dir(), mode, "vec.db")
    return path if os.path.exists(path) else None


def generate_query_embedding(text: str) -> list[float] | None:
    """Call embed.py via subprocess to generate a query embedding.

    Discovers embed.py from BIGPIE_BUNDLED_SKILLS_DIR env var first,
    then falls back to the same directory as this script.
    Returns None on any failure so callers degrade gracefully to FTS-only.
    """
    embed_script: str | None = None

    # Primary: discover from BIGPIE_BUNDLED_SKILLS_DIR
    bundled_dir = os.environ.get("BIGPIE_BUNDLED_SKILLS_DIR")
    if bundled_dir:
        candidate = os.path.join(
            bundled_dir, "cs-research", "search-knowledge-base", "embed.py"
        )
        if os.path.exists(candidate):
            embed_script = candidate

    # Fallback: same directory as this script
    if embed_script is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidate = os.path.join(script_dir, "embed.py")
        if os.path.exists(candidate):
            embed_script = candidate

    if embed_script is None:
        return None

    try:
        result = subprocess.run(
            [sys.executable, embed_script, text],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            _dbg(f"[DEBUG:embed] FAILED (exit {result.returncode}) — FTS-only mode")
            return None
        embedding = json.loads(result.stdout)
        _dbg(f"[DEBUG:embed] OK dim={len(embedding)}")
        return embedding
    except Exception as e:
        _dbg(f"[DEBUG:embed] exception: {e}")
        return None


def vec_search_papers(
    query_embedding: list[float],
    fetch_limit: int,
    main_conn: sqlite3.Connection,
) -> list[str]:
    """Return list of unified_ids ranked by cosine similarity via sqlite-vec KNN.

    rowid in vec_papers equals seq_no in the papers table.
    Returns [] on any failure so FTS search is unaffected.
    """
    try:
        vec_db = get_vec_db_path("papers")
        if vec_db is None:
            return []

        rowids: list[int] = []
        vec_conn = sqlite3.connect(vec_db)
        try:
            if load_sqlite_vec(vec_conn):
                packed = struct.pack(f"{len(query_embedding)}f", *query_embedding)
                vec_rows = vec_conn.execute(
                    "SELECT rowid FROM vec_papers "
                    "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                    (packed, fetch_limit),
                ).fetchall()
                rowids = [int(r[0]) for r in vec_rows]
        finally:
            vec_conn.close()

        # Fallback: subprocess using a Python that supports enable_load_extension
        if not rowids:
            rowids = _knn_subprocess({
                "db": vec_db, "table": "vec_papers",
                "emb": query_embedding, "limit": fetch_limit,
            })

        if not rowids:
            return []

        # rowid == seq_no; look up unified_id in snapshot.db
        placeholders = ",".join("?" * len(rowids))
        uid_rows = main_conn.execute(
            f"SELECT seq_no, unified_id FROM papers WHERE seq_no IN ({placeholders})",
            rowids,
        ).fetchall()
        seq_to_uid: dict[int, str] = {int(r[0]): str(r[1]) for r in uid_rows}
        return [seq_to_uid[rid] for rid in rowids if rid in seq_to_uid]
    except Exception:
        return []


def vec_search_hotspot(
    query_embedding: list[float],
    fetch_limit: int,
) -> list[str]:
    """Return list of hotspot item ids ranked by cosine similarity via sqlite-vec KNN.

    vec_id_map lives in vec.db (not in snapshot.db).
    Returns [] on any failure so FTS search is unaffected.
    """
    try:
        vec_db = get_vec_db_path("hotspot")
        if vec_db is None:
            return []

        hotspot_ids: list[str] = []
        vec_conn = sqlite3.connect(vec_db)
        try:
            if load_sqlite_vec(vec_conn):
                packed = struct.pack(f"{len(query_embedding)}f", *query_embedding)
                vec_rows = vec_conn.execute(
                    "SELECT rowid FROM vec_items "
                    "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                    (packed, fetch_limit),
                ).fetchall()
                rowids = [int(r[0]) for r in vec_rows]
                if rowids:
                    placeholders = ",".join("?" * len(rowids))
                    id_rows = vec_conn.execute(
                        f"SELECT rowid, hotspot_id FROM vec_id_map WHERE rowid IN ({placeholders})",
                        rowids,
                    ).fetchall()
                    rowid_to_hid = {int(r[0]): str(r[1]) for r in id_rows}
                    hotspot_ids = [rowid_to_hid[rid] for rid in rowids if rid in rowid_to_hid]
        finally:
            vec_conn.close()

        # Fallback: subprocess using a Python that supports enable_load_extension
        if not hotspot_ids:
            hotspot_ids = _knn_subprocess({
                "db": vec_db, "table": "vec_items",
                "emb": query_embedding, "limit": fetch_limit,
                "id_table": "vec_id_map", "id_col": "hotspot_id",
            })

        return hotspot_ids
    except Exception:
        return []


def search_hotspot(query: str, limit: int, query_embedding: list[float] | None) -> list[dict]:
    if not ensure_snapshot("hotspot"):
        return []
    db_path = os.path.join(get_kb_dir(), "hotspot", "snapshot.db")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    fetch_limit = limit * 3

    try:
        # FTS run 1: broad query on all columns
        broad_q = safe_fts_query(query)
        rows_broad = run_fts(conn, (
            "SELECT h.id, h.title, h.title_en, h.url, h.source, "
            "h.publishedAt, h.summary, h.category, h.date "
            "FROM hotspot_fts fts JOIN hotspot_items h ON h.rowid = fts.rowid "
            "WHERE hotspot_fts MATCH ? ORDER BY rank LIMIT ?"
        ), (broad_q, fetch_limit))

        # FTS run 2: title-boosted query
        title_q = safe_fts_query(query, "title")
        rows_title = run_fts(conn, (
            "SELECT h.id, h.title, h.title_en, h.url, h.source, "
            "h.publishedAt, h.summary, h.category, h.date "
            "FROM hotspot_fts fts JOIN hotspot_items h ON h.rowid = fts.rowid "
            "WHERE hotspot_fts MATCH ? ORDER BY rank LIMIT ?"
        ), (title_q, fetch_limit))

        # Convert to plain dicts while conn is still open; reuse for both
        # all_by_id lookup and RRF id-lists (avoids calling dict(r) twice).
        broad_dicts = [dict(r) for r in rows_broad]
        title_dicts = [dict(r) for r in rows_title]

        # LIKE fallback: when FTS returns nothing and query has CJK, unicode61
        # can't segment Chinese words — fall back to substring LIKE on title+summary.
        like_dicts: list[dict] = []
        if not broad_dicts and not title_dicts and is_cjk(query):
            cjk_tokens = [t for t in query.split() if is_cjk(t) and len(t) >= 2]
            seen_ids: set[str] = set()
            for tok in cjk_tokens[:3]:
                pat = f'%{tok}%'
                for r in conn.execute(
                    "SELECT h.id, h.title, h.title_en, h.url, h.source, "
                    "h.publishedAt, h.summary, h.category, h.date "
                    "FROM hotspot_items h WHERE h.title LIKE ? OR h.summary LIKE ? LIMIT ?",
                    (pat, pat, fetch_limit),
                ):
                    d = dict(r)
                    if d["id"] not in seen_ids:
                        seen_ids.add(d["id"])
                        like_dicts.append(d)

        # Vector search: fetch item details for vec-ranked ids while conn is open
        vec_dicts: list[dict] = []
        if query_embedding:
            vec_ids = vec_search_hotspot(query_embedding, fetch_limit)
            if vec_ids:
                placeholders = ",".join("?" * len(vec_ids))
                vec_rows = conn.execute(
                    "SELECT h.id, h.title, h.title_en, h.url, h.source, "
                    "h.publishedAt, h.summary, h.category, h.date "
                    f"FROM hotspot_items h WHERE h.id IN ({placeholders})",
                    vec_ids,
                ).fetchall()
                vec_row_map = {dict(r)["id"]: dict(r) for r in vec_rows}
                vec_dicts = [vec_row_map[hid] for hid in vec_ids if hid in vec_row_map]
    finally:
        conn.close()

    # Build id-keyed lookup (later entries win on duplicate ids)
    all_by_id: dict[str, dict] = {}
    for d in broad_dicts + title_dicts + like_dicts + vec_dicts:
        all_by_id[d["id"]] = d

    # Two-stage RRF (K=20): pre-merge FTS sub-lists → 2-way fusion with vec.
    # Stage 1: union FTS sub-lists preserving first-seen (= highest-rank) order.
    fts_seen: dict[str, int] = {}
    for fts_list in [broad_dicts, title_dicts, like_dicts]:
        for d in fts_list:
            item_id = d["id"]
            if item_id not in fts_seen:
                fts_seen[item_id] = len(fts_seen)
    fts_ranked = sorted(fts_seen, key=lambda x: fts_seen[x])
    vec_id_list = [d["id"] for d in vec_dicts]

    _dbg(f"[DEBUG:fts] {len(fts_ranked)} unique items (broad={len(broad_dicts)} title={len(title_dicts)} like={len(like_dicts)})")
    _dbg(f"[DEBUG:vec] {len(vec_id_list)} items from vec_items KNN")
    if _DEBUG:
        for i, vid in enumerate(vec_id_list[:5]):
            t = all_by_id.get(vid, {}).get("title", "")[:50]
            _dbg(f"  vec #{i+1}: {vid} | {t}")

    # Stage 2: 2-way RRF at K=20 between merged-FTS and vec (equal voting power).
    k = 20
    score_map: dict[str, float] = {}
    for rank, item_id in enumerate(fts_ranked):
        score_map[item_id] = score_map.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    for rank, item_id in enumerate(vec_id_list):
        score_map[item_id] = score_map.get(item_id, 0.0) + 1.0 / (k + rank + 1)

    sorted_ids = sorted(score_map, key=lambda x: score_map[x], reverse=True)
    vec_id_set = set(vec_id_list)
    if _DEBUG:
        _dbg(f"[DEBUG:rrf] top-{limit} final ranking:")
        for i, id_ in enumerate(sorted_ids[:limit]):
            in_fts = id_ in fts_seen
            in_vec = id_ in vec_id_set
            src = "fts+vec" if in_fts and in_vec else ("vec" if in_vec else "fts")
            t = all_by_id.get(id_, {}).get("title", "")[:45]
            _dbg(f"  #{i+1} [{src}] score={score_map[id_]:.4f} | {t}")
    return [all_by_id[id_] for id_ in sorted_ids[:limit] if id_ in all_by_id]


def search_papers(query: str, limit: int, query_embedding: list[float] | None) -> list[dict]:
    if not ensure_snapshot("papers"):
        return []
    db_path = os.path.join(get_kb_dir(), "papers", "snapshot.db")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    fetch_limit = limit * 3

    try:
        cols = ("p.unified_id, p.title, p.title_zh, p.abstract, p.abstract_zh, "
                "p.summary_zh, p.tldr_zh, p.keywords, p.authors, p.published_date, "
                "p.domain, p.pdf_url, p.code_url, p.importance_score")
        fts_from = "papers_fts fts JOIN papers p ON p.rowid = fts.rowid"

        # FTS run 1: broad across all FTS columns
        broad_q = safe_fts_query(query)
        rows_broad = run_fts(conn, (
            f"SELECT {cols} FROM {fts_from} WHERE papers_fts MATCH ? ORDER BY rank LIMIT ?"
        ), (broad_q, fetch_limit))

        # FTS run 2: title-boosted (English title)
        title_q = safe_fts_query(query, "title")
        rows_title = run_fts(conn, (
            f"SELECT {cols} FROM {fts_from} WHERE papers_fts MATCH ? ORDER BY rank LIMIT ?"
        ), (title_q, fetch_limit))

        # FTS run 3: title_zh boosted (if CJK query)
        rows_title_zh: list = []
        if is_cjk(query):
            zh_q = safe_fts_query(query, "title_zh")
            rows_title_zh = run_fts(conn, (
                f"SELECT {cols} FROM {fts_from} WHERE papers_fts MATCH ? ORDER BY rank LIMIT ?"
            ), (zh_q, fetch_limit))

        # Convert to plain dicts while conn is still open; reuse for both
        # all_by_id lookup and RRF id-lists (avoids calling dict(r) twice).
        broad_dicts = [dict(r) for r in rows_broad]
        title_dicts = [dict(r) for r in rows_title]
        title_zh_dicts = [dict(r) for r in rows_title_zh]

        # LIKE fallback for pure-CJK queries that FTS5 can't segment.
        like_paper_dicts: list[dict] = []
        if not broad_dicts and not title_dicts and not title_zh_dicts and is_cjk(query):
            cjk_tokens = [t for t in query.split() if is_cjk(t) and len(t) >= 2]
            seen_ids: set[str] = set()
            for tok in cjk_tokens[:3]:
                pat = f'%{tok}%'
                for r in conn.execute(
                    f"SELECT {cols} FROM papers p "
                    "WHERE p.title_zh LIKE ? OR p.abstract_zh LIKE ? LIMIT ?",
                    (pat, pat, fetch_limit),
                ):
                    d = dict(r)
                    if d["unified_id"] not in seen_ids:
                        seen_ids.add(d["unified_id"])
                        like_paper_dicts.append(d)

        # Vector search via separate vec.db (rowid in vec_papers == seq_no in papers)
        vec_ranked: list[str] = vec_search_papers(query_embedding, fetch_limit, conn) if query_embedding else []

        # Fetch full dicts for vec-only results not already covered by FTS
        # (must happen before conn.close())
        vec_extra_dicts: list[dict] = []
        if vec_ranked:
            fts_ids = {
                d["unified_id"]
                for d in broad_dicts + title_dicts + title_zh_dicts + like_paper_dicts
            }
            vec_only = [uid for uid in vec_ranked if uid not in fts_ids]
            if vec_only:
                placeholders = ",".join("?" * len(vec_only))
                extra_rows = conn.execute(
                    f"SELECT {cols} FROM papers p WHERE p.unified_id IN ({placeholders})",
                    vec_only,
                ).fetchall()
                vec_extra_dicts = [dict(r) for r in extra_rows]
    finally:
        conn.close()

    # Build id-keyed lookup from FTS + vec-only extras
    all_by_id: dict[str, dict] = {}
    for d in broad_dicts + title_dicts + title_zh_dicts + like_paper_dicts + vec_extra_dicts:
        all_by_id[d["unified_id"]] = d

    # Two-stage RRF (K=20): pre-merge FTS sub-lists → 2-way fusion with vec.
    # Stage 1: union FTS sub-lists preserving first-seen (= highest-rank) order.
    fts_seen: dict[str, int] = {}
    for fts_list in [broad_dicts, title_dicts, title_zh_dicts, like_paper_dicts]:
        for d in fts_list:
            uid = d["unified_id"]
            if uid not in fts_seen:
                fts_seen[uid] = len(fts_seen)
    fts_ranked = sorted(fts_seen, key=lambda u: fts_seen[u])

    _dbg(f"[DEBUG:fts] {len(fts_ranked)} unique papers (broad={len(broad_dicts)} title={len(title_dicts)} title_zh={len(title_zh_dicts)} like={len(like_paper_dicts)})")
    _dbg(f"[DEBUG:vec] {len(vec_ranked)} papers from vec_papers KNN")
    if _DEBUG:
        for i, uid in enumerate(vec_ranked[:5]):
            t = all_by_id.get(uid, {}).get("title", "")[:50]
            _dbg(f"  vec #{i+1}: {uid} | {t}")

    # Stage 2: 2-way RRF at K=20 between merged-FTS and vec (equal voting power).
    k = 20
    score_map: dict[str, float] = {}
    for rank, uid in enumerate(fts_ranked):
        score_map[uid] = score_map.get(uid, 0.0) + 1.0 / (k + rank + 1)
    for rank, uid in enumerate(vec_ranked):
        score_map[uid] = score_map.get(uid, 0.0) + 1.0 / (k + rank + 1)

    sorted_ids = sorted(score_map, key=lambda x: score_map[x], reverse=True)
    vec_ranked_set = set(vec_ranked)
    if _DEBUG:
        _dbg(f"[DEBUG:rrf] top-{limit} final ranking:")
        for i, uid in enumerate(sorted_ids[:limit]):
            in_fts = uid in fts_seen
            in_vec = uid in vec_ranked_set
            src = "fts+vec" if in_fts and in_vec else ("vec" if in_vec else "fts")
            t = all_by_id.get(uid, {}).get("title", "")[:45]
            _dbg(f"  #{i+1} [{src}] score={score_map[uid]:.4f} | {t}")
    return [all_by_id[uid] for uid in sorted_ids[:limit] if uid in all_by_id]


def format_hotspot(results: list[dict]) -> str:
    if not results:
        return "[KB:hotspot] 0 条结果"
    lines = [f"[KB:hotspot] {len(results)} 条结果"]
    for i, r in enumerate(results, 1):
        date = (r.get("publishedAt") or r.get("date") or "")[:10]
        lines.append(f"{i}. [{date}] **{r.get('title', '')}**")
        lines.append(f"   来源：{r.get('source', '')}  分类：{r.get('category', '')}")
        if summary := r.get("summary", ""):
            lines.append(f"   {summary[:200]}")
        if url := r.get("url", ""):
            lines.append(f"   链接：{url}")
    return "\n".join(lines)


def format_papers(results: list[dict]) -> str:
    if not results:
        return "[KB:papers] 0 条结果"
    lines = [f"[KB:papers] {len(results)} 条结果"]
    for i, r in enumerate(results, 1):
        title = r.get("title_zh") or r.get("title", "")
        if r.get("title_zh") and r.get("title"):
            title = f"{r['title_zh']}（{r['title']}）"
        lines.append(f"{i}. **{title}**")
        authors_raw = r.get("authors", "")
        try:
            aList = json.loads(authors_raw)
            authors = ", ".join(aList[:3]) if isinstance(aList, list) else str(authors_raw)
        except Exception:
            authors = str(authors_raw)
        date = (r.get("published_date") or "")[:10]
        if authors or date:
            lines.append(f"   作者：{authors}  日期：{date}")
        if domain := r.get("domain", ""):
            lines.append(f"   领域：{domain}")
        tldr = r.get("tldr_zh") or (r.get("summary_zh") or "")[:200] or (r.get("abstract_zh") or "")[:200]
        if tldr:
            lines.append(f"   摘要：{tldr}")
        if pdf := r.get("pdf_url", ""):
            lines.append(f"   PDF：{pdf}")
        if code := r.get("code_url", ""):
            lines.append(f"   代码：{code}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Dr. Pie KB hybrid search")
    parser.add_argument("mode", choices=["hotspot", "papers"])
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--embed-json", dest="embed_json", help="path to query embedding JSON")
    parser.add_argument("--embed-text", dest="embed_text", help="text to embed on-the-fly via embed.py")
    parser.add_argument("--json", action="store_true", help="output raw JSON instead of formatted text")
    parser.add_argument("--debug", action="store_true", help="print per-source breakdown to stderr")
    args = parser.parse_args()

    global _DEBUG
    _DEBUG = args.debug

    query_embedding: list[float] | None = None
    if args.embed_json:
        try:
            with open(args.embed_json) as f:
                query_embedding = json.load(f)
        except Exception:
            pass
    elif args.embed_text:
        query_embedding = generate_query_embedding(args.embed_text)

    if args.mode == "hotspot":
        results = search_hotspot(args.query, args.limit, query_embedding)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            print(format_hotspot(results))
    else:
        results = search_papers(args.query, args.limit, query_embedding)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            print(format_papers(results))


if __name__ == "__main__":
    main()
