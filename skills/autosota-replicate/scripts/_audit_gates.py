#!/usr/bin/env python3
"""Eval-integrity gates for /autosota-replicate.

Generic, domain-agnostic. The orchestrator owns verdict assignment; subagents
only produce evidence. Every gate keys on structured signals from a YAML
frontmatter at the top of ACCEPTANCE_CRITERIA.md (schema_version: 1).

Public API:
  parse_frontmatter(ac_md_text) -> dict | None
  run_gates(our_results, autosota, frontmatter) -> dict
  fold_verdict(initial_verdict, gates) -> (final_verdict, needs_user)

Gate statuses:
  pass         — evaluated and OK
  fail         — evaluated and violated
  unknown      — required signal missing; cannot decide
  n/a          — gate does not apply (e.g. no guards declared)

Verdict folding (applied in order):
  1. Any hard G_ACCEPTANCE_GUARDS fail -> verdict forced to 'below'
  2. G_SIGNIFICANCE fail on would-be 'beat' -> downgrade 'match'
  3. G_RESOURCE_PARITY fail on would-be 'beat' -> downgrade 'match'
  4. G_EVAL_HYGIENE fail on would-be 'beat' -> downgrade 'match'
  5. G_SATURATION fail on would-be 'beat' -> downgrade 'match'
  6. G_HELDOUT_MERGE on would-be 'beat':
       within-noise independent gain -> downgrade 'match';
       mismatch (self-report optimistic vs independent) or eval tamper
       -> 'pending_gate' (needs_user=true).
  7. If the would-be verdict is 'beat' and any gate is 'unknown'
     -> verdict becomes 'pending_gate' (needs_user=true).

G_HELDOUT_MERGE is opt-in: it is 'n/a' unless the plan frontmatter declares
`heldout: { required: true, split: <id> }`. When required, the gate trusts the
`our_results.heldout` block, which is written ONLY by the orchestrator's
independent runner (scripts/heldout_eval.py), never by the executor subagent.
This is the one gate that does not trust an agent-self-reported number.
"""
from __future__ import annotations

import math
import re
from typing import Any

# Fallback mismatch tolerance when a plan sets neither mismatch_tolerance nor
# min_detectable_delta. A tiny epsilon (not exact 0.0) so pure floating-point dust
# doesn't trip a mismatch, while any real optimistic divergence still does.
_MISMATCH_TOL_EPSILON = 1e-6


# -------------------- YAML-ish frontmatter parser (no external deps) --------

_SCALAR_LINE = re.compile(r"^\s*([A-Za-z_][\w]*)\s*:\s*(.*?)\s*$")


def _coerce(v: str) -> Any:
    s = v.strip()
    if s == "" or s in {"~", "null", "None"}:
        return None
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    # inline list/obj: pass through as raw (we don't need deep nesting)
    if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
        # crude parse: try literal_eval-like for very simple forms
        inner = s[1:-1].strip()
        if not inner:
            return [] if s.startswith("[") else {}
        if s.startswith("["):
            return [_coerce(p) for p in _split_flat(inner)]
        # flat map {k: v, k: v}
        out: dict = {}
        for pair in _split_flat(inner):
            if ":" in pair:
                k, _, val = pair.partition(":")
                out[k.strip()] = _coerce(val)
        return out
    # quoted string
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    # numeric
    try:
        if "." in s or "e" in s or "E" in s:
            return float(s)
        return int(s)
    except ValueError:
        return s  # leave as string


def _split_flat(s: str) -> list[str]:
    """Split on commas not inside nested brackets."""
    out, depth, buf = [], 0, []
    for ch in s:
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return [p for p in out if p]


def parse_frontmatter(text: str) -> dict | None:
    """Extract the first YAML block delimited by --- at the top.
    Supports a flat+indented subset sufficient for our schema:
      key: scalar
      key:
        subkey: scalar
      key:
        - item
        - { name: x, op: ">=", threshold: 0.5 }
    No advanced YAML (anchors, multi-doc, explicit types).
    Returns dict or None if no frontmatter / parse failure.
    """
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return None
    body = m.group(1)
    return _parse_block(body.splitlines())


def _parse_block(lines: list[str]) -> dict:
    """Indent-based parse. 2-space indent assumed; tabs rejected."""
    out: dict = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent != 0:
            i += 1
            continue
        sm = _SCALAR_LINE.match(line)
        if not sm:
            i += 1
            continue
        key, val = sm.group(1), sm.group(2)
        if val.strip():
            out[key] = _coerce(val)
            i += 1
        else:
            # block value: collect child lines with indent > 0
            child_lines = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if not nxt.strip():
                    child_lines.append(nxt)
                    j += 1
                    continue
                n_indent = len(nxt) - len(nxt.lstrip(" "))
                if n_indent == 0:
                    break
                child_lines.append(nxt[2:] if nxt.startswith("  ") else nxt.lstrip(" "))
                j += 1
            # Detect list vs mapping
            stripped_children = [c for c in child_lines if c.strip()]
            if stripped_children and all(
                c.lstrip().startswith("- ") or c.lstrip().startswith("-\t") or c.lstrip() == "-"
                for c in stripped_children
            ):
                items = []
                for c in stripped_children:
                    item_body = c.lstrip()[2:].strip()
                    if item_body.startswith("{"):
                        items.append(_coerce(item_body))
                    elif ":" in item_body and not item_body.startswith("["):
                        # inline map on a list item
                        items.append(_coerce("{" + item_body + "}"))
                    else:
                        items.append(_coerce(item_body))
                out[key] = items
            else:
                out[key] = _parse_block(child_lines)
            i = j
    return out


# -------------------- Gate helpers --------------------


def _get(d: dict, path: str, default=None) -> Any:
    cur: Any = d
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _op_check(observed: float, op: str, threshold: float) -> bool:
    if op == ">=":
        return observed >= threshold
    if op == "<=":
        return observed <= threshold
    if op == ">":
        return observed > threshold
    if op == "<":
        return observed < threshold
    if op == "==":
        return observed == threshold
    raise ValueError(f"unknown op: {op}")


def _metric_value(our: dict, metric: str, split: str | None) -> float | None:
    """Look up metric value, optionally from a specific split."""
    if split and split != "any":
        split_scores = our.get("split_scores") or {}
        if split in split_scores:
            v = split_scores[split].get(metric) if isinstance(split_scores[split], dict) else None
            if v is not None:
                try:
                    return float(v) if not isinstance(v, dict) else float(v.get("value"))
                except (TypeError, ValueError):
                    return None
    metrics = our.get("metrics") or {}
    if metric in metrics:
        v = metrics[metric]
        if isinstance(v, dict):
            try:
                return float(v.get("value"))
            except (TypeError, ValueError):
                return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return None


# -------------------- Gates (generic, no paper-specific logic) --------------


def g_acceptance_guards(our: dict, fm: dict) -> dict:
    """Every hard guard in frontmatter must pass; soft guards only warn."""
    guards = fm.get("guards") or []
    if not guards:
        return {"gate": "G_ACCEPTANCE_GUARDS", "status": "n/a", "checked": []}
    checked = []
    any_hard_fail = False
    any_unknown = False
    for g in guards:
        if not isinstance(g, dict):
            continue
        name = g.get("name", "?")
        metric = g.get("metric")
        split = g.get("split")
        op = g.get("op", ">=")
        thr = g.get("threshold")
        sev = (g.get("severity") or "soft").lower()
        obs = _metric_value(our, metric, split) if metric else None
        if obs is None or thr is None:
            status = "unknown"
            any_unknown = True
        elif _op_check(obs, op, float(thr)):
            status = "pass"
        else:
            status = "fail"
            if sev == "hard":
                any_hard_fail = True
        checked.append({
            "name": name, "metric": metric, "split": split,
            "op": op, "threshold": thr, "observed": obs,
            "severity": sev, "status": status,
        })
    overall = "fail" if any_hard_fail else ("unknown" if any_unknown else "pass")
    return {"gate": "G_ACCEPTANCE_GUARDS", "status": overall, "checked": checked,
            "hard_fail": any_hard_fail}


def g_significance(our: dict, autosota: dict, fm: dict) -> dict:
    """Primary delta must exceed max(ci_half_width, min_detectable_delta);
    require >= min_seeds seeds or explicit CI."""
    pm = fm.get("primary_metric") or {}
    primary_name = pm.get("name")
    min_detect = pm.get("min_detectable_delta")
    stats = fm.get("statistics") or {}
    min_seeds = stats.get("min_seeds", 1)

    metrics = our.get("metrics") or {}
    entry = metrics.get(primary_name) if primary_name else None
    if not isinstance(entry, dict):
        return {"gate": "G_SIGNIFICANCE", "status": "unknown",
                "reason": "primary metric entry missing or scalar (need CI/n_seeds)"}
    value = entry.get("value")
    ci_lower = entry.get("ci_lower")
    ci_upper = entry.get("ci_upper")
    n_seeds = entry.get("n_seeds")
    ci_half = None
    if ci_lower is not None and ci_upper is not None and value is not None:
        try:
            ci_half = (float(ci_upper) - float(ci_lower)) / 2.0
        except (TypeError, ValueError):
            ci_half = None
    noise_floor = 0.0
    if ci_half is not None:
        noise_floor = max(noise_floor, ci_half)
    if min_detect is not None:
        try:
            noise_floor = max(noise_floor, float(min_detect))
        except (TypeError, ValueError):
            pass

    autosota_value = autosota.get("autosota_best_value")
    if autosota_value is None:
        # Try parsing autosota_best string for numeric
        raw = autosota.get("autosota_best")
        if isinstance(raw, (int, float)):
            autosota_value = float(raw)
        elif isinstance(raw, str):
            m = re.match(r"\s*([+\-]?\d+(?:\.\d+)?)", raw)
            if m:
                try:
                    autosota_value = float(m.group(1))
                except ValueError:
                    pass

    if value is None or autosota_value is None:
        return {"gate": "G_SIGNIFICANCE", "status": "unknown",
                "reason": "cannot compare: value or autosota_value missing",
                "value": value, "autosota_value": autosota_value,
                "n_seeds": n_seeds, "ci_half_width": ci_half}
    delta = abs(value - autosota_value)
    have_variance_signal = (ci_half is not None) or (n_seeds is not None and n_seeds >= min_seeds)
    if not have_variance_signal:
        return {"gate": "G_SIGNIFICANCE", "status": "unknown",
                "reason": f"need >={min_seeds} seeds or explicit CI",
                "n_seeds": n_seeds, "ci_half_width": ci_half,
                "delta": delta, "noise_floor": noise_floor}
    status = "pass" if delta > noise_floor else "fail"
    return {"gate": "G_SIGNIFICANCE", "status": status,
            "delta": delta, "noise_floor": noise_floor,
            "n_seeds": n_seeds, "ci_half_width": ci_half}


def g_resource_parity(our: dict, autosota: dict, fm: dict) -> dict:
    """Our compute/sweep/ensemble <= reference * parity_ratio.
    Reference side lives in frontmatter resource_budget/sweep_budget
    (paper-to-plan fills these a priori from paper / AutoSOTA);
    our side comes from our_results.resources.
    """
    res = our.get("resources") or {}
    rb = fm.get("resource_budget") or {}
    sb = fm.get("sweep_budget") or {}
    ratio = 1.25
    try:
        ratio = float(sb.get("parity_ratio_vs_reference", 1.25))
    except (TypeError, ValueError):
        pass
    axes = {}
    any_fail = False
    any_unknown = False
    for axis, ref_key, use_ratio in [
        ("gpu_hours", "gpu_hours", ratio),
        ("sweep_trials", "max_trials", ratio),
        ("ensemble_size", "max_ensemble", ratio),
    ]:
        ours_v = res.get(axis)
        ref_v = (rb if axis == "gpu_hours" else sb).get(ref_key)
        if ours_v is None or ref_v is None:
            axes[axis] = {"ours": ours_v, "ref": ref_v, "status": "unknown"}
            any_unknown = True
            continue
        try:
            ours_f, ref_f = float(ours_v), float(ref_v)
        except (TypeError, ValueError):
            axes[axis] = {"ours": ours_v, "ref": ref_v, "status": "unknown"}
            any_unknown = True
            continue
        if ref_f <= 0:
            axes[axis] = {"ours": ours_f, "ref": ref_f, "status": "unknown"}
            any_unknown = True
            continue
        r = ours_f / ref_f
        ok = r <= ratio
        axes[axis] = {"ours": ours_f, "ref": ref_f, "ratio": round(r, 3),
                      "threshold": ratio, "status": "pass" if ok else "fail"}
        if not ok:
            any_fail = True
    overall = "fail" if any_fail else ("unknown" if any_unknown else "pass")
    return {"gate": "G_RESOURCE_PARITY", "status": overall,
            "parity_ratio": ratio, "axes": axes}


def g_eval_hygiene(our: dict, fm: dict) -> dict:
    """Selection must be on dev_split (or a priori); all eval_splits reported."""
    eval_splits = fm.get("eval_splits") or []
    dev_split = fm.get("dev_split")
    selection_split = our.get("selection_split")
    split_scores = our.get("split_scores") or {}

    if not eval_splits:
        return {"gate": "G_EVAL_HYGIENE", "status": "n/a",
                "reason": "no eval_splits declared"}

    missing = [s for s in eval_splits if s not in split_scores]
    leak = False
    if selection_split and selection_split in eval_splits:
        # Selected on a split that also enters the report -> leak unless dev_split
        # is a separate held-out split
        if dev_split is None or selection_split != dev_split or dev_split in eval_splits:
            leak = True
    # If selection_split is missing and there was a sweep, we can't verify
    sweep_trials = (our.get("resources") or {}).get("sweep_trials")
    unknown_selection = selection_split is None and sweep_trials and sweep_trials > 3

    status = "pass"
    if missing:
        status = "fail"
    elif leak:
        status = "fail"
    elif unknown_selection:
        status = "unknown"
    return {"gate": "G_EVAL_HYGIENE", "status": status,
            "eval_splits": eval_splits, "dev_split": dev_split,
            "selection_split": selection_split,
            "missing_splits": missing, "selection_leak": leak,
            "unknown_selection": unknown_selection}


def g_saturation(our: dict, fm: dict) -> dict:
    """If primary metric ≥ saturation_bound, require a corroborating split."""
    pm = fm.get("primary_metric") or {}
    bound = pm.get("saturation_bound")
    name = pm.get("name")
    if bound is None or name is None:
        return {"gate": "G_SATURATION", "status": "n/a"}
    metrics = our.get("metrics") or {}
    entry = metrics.get(name)
    value = None
    if isinstance(entry, dict):
        try:
            value = float(entry.get("value"))
        except (TypeError, ValueError):
            value = None
    elif entry is not None:
        try:
            value = float(entry)
        except (TypeError, ValueError):
            value = None
    if value is None:
        return {"gate": "G_SATURATION", "status": "unknown",
                "reason": "primary metric value missing"}
    try:
        bf = float(bound)
    except (TypeError, ValueError):
        return {"gate": "G_SATURATION", "status": "unknown",
                "reason": "saturation_bound not numeric"}
    if value < bf:
        return {"gate": "G_SATURATION", "status": "pass",
                "value": value, "bound": bf}
    # Saturated: look for corroboration on another split
    split_scores = our.get("split_scores") or {}
    corroborating = None
    for sid, scores in split_scores.items():
        if not isinstance(scores, dict):
            continue
        v = scores.get(name)
        if v is None:
            continue
        try:
            vf = float(v) if not isinstance(v, dict) else float(v.get("value"))
        except (TypeError, ValueError):
            continue
        # Corroboration = not also saturated and still improved-direction-consistent
        if vf < bf:
            corroborating = {"split": sid, "value": vf}
            break
    status = "pass" if corroborating else "fail"
    return {"gate": "G_SATURATION", "status": status, "value": value,
            "bound": bf, "corroborating_split": corroborating}


def g_heldout_merge(our: dict, autosota: dict, fm: dict) -> dict:
    """Independent held-out verification gate (opt-in).

    Unlike every other gate, this one does NOT trust a subagent-reported number.
    It reads `our_results.heldout`, which is written only by the orchestrator's
    independent runner (scripts/heldout_eval.py) after re-evaluating the
    candidate commit in a fresh worktree against a LOCKED eval harness + held-out
    data the executor cannot write. The gate then:
      - confirms the result is from the independent runner (produced_by);
      - checks eval-harness tamper-evidence (runner-stamped tamper_ok vs
        HELDOUT_LOCK);
      - flags a `mismatch` when the agent's self-reported report_split score is
        optimistic relative to the independent held-out score beyond tolerance
        (the overfitting / gaming signal);
      - confirms the *independent* gain over the reference clears the noise floor.

    Statuses: n/a (not required) | unknown (no independent result yet) |
              fail (within_noise | mismatch | tamper) | pass.
    """
    ho_cfg = fm.get("heldout") or {}
    required = bool(ho_cfg.get("required"))
    pm = fm.get("primary_metric") or {}
    primary = pm.get("name")
    if not required:
        return {"gate": "G_HELDOUT_MERGE", "status": "n/a",
                "reason": "held-out verification not required by plan"}
    if not primary:
        return {"gate": "G_HELDOUT_MERGE", "status": "n/a",
                "reason": "no primary_metric.name in frontmatter"}

    ho = our.get("heldout") or {}
    produced_by = ho.get("produced_by")
    ho_metrics = ho.get("metrics") or {}
    indep_raw = ho_metrics.get(primary)
    if isinstance(indep_raw, dict):
        indep_raw = indep_raw.get("value")
    if produced_by != "independent_runner" or indep_raw is None:
        return {"gate": "G_HELDOUT_MERGE", "status": "unknown",
                "reason": "no independent held-out result "
                          "(produced_by != independent_runner or metric missing)",
                "produced_by": produced_by}
    try:
        indep_val = float(indep_raw)
    except (TypeError, ValueError):
        return {"gate": "G_HELDOUT_MERGE", "status": "unknown",
                "reason": "held-out metric not numeric"}

    # Tamper-evidence: the runner compares the eval files it actually used against
    # HELDOUT_LOCK and stamps tamper_ok. False == eval/ was modified or restore failed.
    if ho.get("tamper_ok") is False:
        return {"gate": "G_HELDOUT_MERGE", "status": "fail", "tamper": True,
                "heldout_value": indep_val,
                "reason": "eval-harness sha != HELDOUT_LOCK (eval/ tampered or restore failed)"}

    direction = (pm.get("direction") or "maximize").lower()
    report_split = fm.get("report_split")
    self_val = _metric_value(our, primary, report_split)

    # Mismatch: self-reported report_split score optimistic vs independent held-out.
    # Tolerance falls back: explicit mismatch_tolerance -> primary min_detectable_delta
    # -> a tiny epsilon. The epsilon default ensures that a plan which sets neither
    # does NOT silently skip mismatch detection (any real optimistic self-report
    # flags -> pending_gate) while not tripping on pure floating-point dust.
    tol = ho_cfg.get("mismatch_tolerance")
    tol_source = "mismatch_tolerance"
    if tol is None:
        tol = pm.get("min_detectable_delta")
        tol_source = "min_detectable_delta"
    if tol is None:
        tol = _MISMATCH_TOL_EPSILON
        tol_source = f"default-epsilon-{_MISMATCH_TOL_EPSILON} (no mismatch_tolerance/min_detectable_delta configured)"
    mismatch = False
    if self_val is not None:
        try:
            tol_f = float(tol)
            gap = (self_val - indep_val) if direction == "maximize" else (indep_val - self_val)
            mismatch = gap > tol_f
        except (TypeError, ValueError):
            pass
    if mismatch:
        return {"gate": "G_HELDOUT_MERGE", "status": "fail", "mismatch": True,
                "self_report": self_val, "heldout_value": indep_val,
                "tolerance": tol, "tolerance_source": tol_source, "direction": direction,
                "reason": "self-reported report_split better than independent "
                          "held-out beyond tolerance (overfitting signal)"}

    # Independent gain over the reference must clear the noise floor.
    noise = 0.0
    md = pm.get("min_detectable_delta")
    if md is not None:
        try:
            noise = max(noise, float(md))
        except (TypeError, ValueError):
            pass
    ref_val = autosota.get("autosota_best_value")
    if ref_val is None:
        return {"gate": "G_HELDOUT_MERGE", "status": "unknown",
                "reason": "reference value missing; cannot verify held-out gain",
                "heldout_value": indep_val, "self_report": self_val}
    try:
        ref_f = float(ref_val)
    except (TypeError, ValueError):
        return {"gate": "G_HELDOUT_MERGE", "status": "unknown",
                "reason": "reference value not numeric",
                "heldout_value": indep_val, "self_report": self_val}
    improved = (indep_val - ref_f) > noise if direction == "maximize" else (ref_f - indep_val) > noise
    return {"gate": "G_HELDOUT_MERGE", "status": "pass" if improved else "fail",
            "heldout_value": indep_val, "self_report": self_val,
            "reference": ref_f, "noise_floor": noise, "direction": direction,
            "within_noise": (not improved)}


# -------------------- Orchestrator --------------------


def run_gates(our: dict, autosota: dict, frontmatter: dict | None) -> dict:
    """Run all gates; return audit block for embedding into COMPARISON.json."""
    fm = frontmatter or {}
    audit = {
        "schema_version": fm.get("schema_version"),
        "decided_by": "orchestrator",
        "gates": {
            "acceptance_guards": g_acceptance_guards(our, fm),
            "significance":      g_significance(our, autosota, fm),
            "resource_parity":   g_resource_parity(our, autosota, fm),
            "eval_hygiene":      g_eval_hygiene(our, fm),
            "saturation":        g_saturation(our, fm),
            "heldout_merge":     g_heldout_merge(our, autosota, fm),
        },
    }
    return audit


def fold_verdict(initial_verdict: str, audit: dict) -> tuple[str, bool, list[str]]:
    """Apply verdict-folding rules. Returns (final_verdict, needs_user, reasons).

    Contract:
      - initial_verdict in {'beat','match','below','skipped'}
      - audit is the dict produced by run_gates()
      - returns:
          final_verdict in {'beat','match','below','skipped','pending_gate'}
          needs_user: True if pending_gate (human review required)
          reasons: list of short strings explaining downgrades / forcings
    """
    gates = audit.get("gates", {}) if isinstance(audit, dict) else {}
    reasons: list[str] = []
    v = initial_verdict

    # Rule 1: hard acceptance-guard fail forces 'below'
    ag = gates.get("acceptance_guards", {})
    if ag.get("hard_fail"):
        reasons.append("hard acceptance guard(s) failed -> verdict forced to 'below'")
        return "below", False, reasons

    if v != "beat":
        # Downgrades only affect would-be beats; non-beat carries through
        return v, False, reasons

    # For 'beat' candidates, check each gate
    sig = gates.get("significance", {})
    if sig.get("status") == "fail":
        reasons.append("G_SIGNIFICANCE: delta within noise -> downgraded to 'match'")
        v = "match"

    rp = gates.get("resource_parity", {})
    if rp.get("status") == "fail":
        reasons.append("G_RESOURCE_PARITY: exceeds parity ratio -> downgraded to 'match'")
        v = "match"

    eh = gates.get("eval_hygiene", {})
    if eh.get("status") == "fail":
        reasons.append("G_EVAL_HYGIENE: selection-on-test or missing splits -> downgraded to 'match'")
        v = "match"

    sat = gates.get("saturation", {})
    if sat.get("status") == "fail":
        reasons.append("G_SATURATION: saturated without corroborating split -> downgraded to 'match'")
        v = "match"

    # Held-out merge gate: a self-report/independent mismatch or eval tamper is a
    # trust violation, not a mere downgrade -> escalate to pending_gate for human
    # review. A within-noise independent gain is a normal downgrade to 'match'.
    ho = gates.get("heldout_merge", {})
    if ho.get("status") == "fail":
        if ho.get("mismatch") or ho.get("tamper"):
            why = "self-report vs independent mismatch" if ho.get("mismatch") else "eval-harness tamper"
            reasons.append(
                f"G_HELDOUT_MERGE: {why} -> pending_gate (needs_user); cannot trust the reported BEAT"
            )
            return "pending_gate", True, reasons
        reasons.append("G_HELDOUT_MERGE: independent held-out gain within noise -> downgraded to 'match'")
        v = "match"

    # If we ended up with 'beat' but anything is 'unknown', escalate to pending_gate
    if v == "beat":
        unknowns = [g for g, r in gates.items() if isinstance(r, dict) and r.get("status") == "unknown"]
        if unknowns:
            reasons.append(
                f"gate(s) with unknown signal on would-be BEAT: {unknowns} -> pending_gate (needs_user)"
            )
            return "pending_gate", True, reasons

    return v, False, reasons


# -------------------- Self-test --------------------


def _selftest() -> int:
    """Synthetic fixtures: pass-through, hard-guard fail, sig-noise downgrade,
    parity-inflation downgrade, saturation without corroboration, unknown->pending."""
    import json, sys

    def fm_basic(**overrides):
        base = {
            "schema_version": 1,
            "primary_metric": {"name": "score", "direction": "maximize",
                               "min_detectable_delta": 0.005,
                               "saturation_bound": 0.99},
            "eval_splits": ["test"],
            "dev_split": "dev",
            "report_split": "test",
            "guards": [],
            "resource_budget": {"gpu_hours": 10.0},
            "sweep_budget": {"max_trials": 20, "max_ensemble": 5,
                             "parity_ratio_vs_reference": 1.25},
            "statistics": {"min_seeds": 3, "ci_method": "bootstrap", "ci_level": 0.95},
        }
        base.update(overrides or {})
        return base

    def run_case(label, our, autosota, fm, initial):
        audit = run_gates(our, autosota, fm)
        final, needs_user, reasons = fold_verdict(initial, audit)
        return {"label": label, "initial": initial, "final": final,
                "needs_user": needs_user, "reasons": reasons,
                "audit": audit}

    cases = []

    # Case 1: clean pass-through
    cases.append(run_case(
        "clean_beat",
        {"metrics": {"score": {"value": 0.85, "ci_lower": 0.84, "ci_upper": 0.86, "n_seeds": 3}},
         "split_scores": {"test": {"score": 0.85}},
         "selection_split": "dev",
         "resources": {"gpu_hours": 8.0, "sweep_trials": 15, "ensemble_size": 3}},
        {"autosota_best_value": 0.80},
        fm_basic(),
        "beat",
    ))

    # Case 2: hard guard fail -> forced below
    cases.append(run_case(
        "hard_guard_fail",
        {"metrics": {"score": {"value": 0.99, "ci_lower": 0.99, "ci_upper": 0.99, "n_seeds": 3},
                     "secondary": {"value": 0.5}},
         "split_scores": {"test": {"score": 0.99}},
         "selection_split": "dev",
         "resources": {"gpu_hours": 8.0, "sweep_trials": 15, "ensemble_size": 3}},
        {"autosota_best_value": 0.80},
        fm_basic(guards=[{"name": "sec", "metric": "secondary", "op": ">=",
                          "threshold": 0.8, "severity": "hard"}]),
        "beat",
    ))

    # Case 3: within-noise delta -> match
    cases.append(run_case(
        "within_noise",
        {"metrics": {"score": {"value": 0.802, "ci_lower": 0.79, "ci_upper": 0.815, "n_seeds": 3}},
         "split_scores": {"test": {"score": 0.802}},
         "selection_split": "dev",
         "resources": {"gpu_hours": 8.0, "sweep_trials": 15, "ensemble_size": 3}},
        {"autosota_best_value": 0.80},
        fm_basic(),
        "beat",
    ))

    # Case 4: compute inflated -> match
    cases.append(run_case(
        "compute_inflated",
        {"metrics": {"score": {"value": 0.85, "ci_lower": 0.84, "ci_upper": 0.86, "n_seeds": 3}},
         "split_scores": {"test": {"score": 0.85}},
         "selection_split": "dev",
         "resources": {"gpu_hours": 20.0, "sweep_trials": 15, "ensemble_size": 3}},
        {"autosota_best_value": 0.80},
        fm_basic(),
        "beat",
    ))

    # Case 5: saturated without corroboration -> match
    cases.append(run_case(
        "saturation_no_corrob",
        {"metrics": {"score": {"value": 1.0, "ci_lower": 1.0, "ci_upper": 1.0, "n_seeds": 3}},
         "split_scores": {"test": {"score": 1.0}},
         "selection_split": "dev",
         "resources": {"gpu_hours": 8.0, "sweep_trials": 15, "ensemble_size": 3}},
        {"autosota_best_value": 0.80},
        fm_basic(),
        "beat",
    ))

    # Case 6: unknown significance (no CI, no n_seeds) -> pending_gate
    cases.append(run_case(
        "unknown_signal_pending",
        {"metrics": {"score": {"value": 0.85}},  # no CI, no n_seeds
         "split_scores": {"test": {"score": 0.85}},
         "selection_split": "dev",
         "resources": {"gpu_hours": 8.0, "sweep_trials": 15, "ensemble_size": 3}},
        {"autosota_best_value": 0.80},
        fm_basic(),
        "beat",
    ))

    # ---- G_HELDOUT_MERGE fixtures (opt-in gate; require heldout.required) ----
    def base_our(**over):
        o = {
            "metrics": {"score": {"value": 0.85, "ci_lower": 0.84, "ci_upper": 0.86, "n_seeds": 3}},
            "split_scores": {"test": {"score": 0.85}},
            "selection_split": "dev",
            "resources": {"gpu_hours": 8.0, "sweep_trials": 15, "ensemble_size": 3},
        }
        o.update(over)
        return o

    # Case 7: independent held-out agrees with self-report and beats reference -> beat
    cases.append(run_case(
        "heldout_clean_beat",
        base_our(heldout={"produced_by": "independent_runner", "split": "test",
                          "metrics": {"score": 0.85}, "tamper_ok": True}),
        {"autosota_best_value": 0.80},
        fm_basic(heldout={"required": True, "split": "test"}),
        "beat",
    ))

    # Case 8 (THE acceptance test): self-report 0.85 but independent held-out 0.805
    # -> mismatch -> pending_gate. An overfit/gamed BEAT must NOT pass.
    cases.append(run_case(
        "heldout_overfit_mismatch",
        base_our(heldout={"produced_by": "independent_runner", "split": "test",
                          "metrics": {"score": 0.805}, "tamper_ok": True}),
        {"autosota_best_value": 0.80},
        fm_basic(heldout={"required": True, "split": "test"}),
        "beat",
    ))

    # Case 9: independent held-out within noise of reference -> downgrade to match
    # (mismatch_tolerance widened so the within-noise path is exercised in isolation)
    cases.append(run_case(
        "heldout_within_noise",
        base_our(
            metrics={"score": {"value": 0.84, "ci_lower": 0.835, "ci_upper": 0.845, "n_seeds": 3}},
            split_scores={"test": {"score": 0.84}},
            heldout={"produced_by": "independent_runner", "split": "test",
                     "metrics": {"score": 0.802}, "tamper_ok": True}),
        {"autosota_best_value": 0.80},
        fm_basic(heldout={"required": True, "split": "test", "mismatch_tolerance": 0.1}),
        "beat",
    ))

    # Case 10: held-out required but no independent result produced -> pending_gate
    cases.append(run_case(
        "heldout_missing_pending",
        base_our(),  # no heldout block
        {"autosota_best_value": 0.80},
        fm_basic(heldout={"required": True, "split": "test"}),
        "beat",
    ))

    # Case 11: eval-harness tamper (sha != HELDOUT_LOCK) -> pending_gate
    cases.append(run_case(
        "heldout_tamper",
        base_our(heldout={"produced_by": "independent_runner", "split": "test",
                          "metrics": {"score": 0.85}, "tamper_ok": False}),
        {"autosota_best_value": 0.80},
        fm_basic(heldout={"required": True, "split": "test"}),
        "beat",
    ))

    # Case 12: plan sets NEITHER mismatch_tolerance NOR min_detectable_delta ->
    # conservative strict-0.0 tolerance must still catch an optimistic self-report
    # (no silent skip). self=0.85 vs independent=0.80 -> mismatch -> pending_gate.
    cases.append(run_case(
        "heldout_degenerate_tol",
        base_our(heldout={"produced_by": "independent_runner", "split": "test",
                          "metrics": {"score": 0.80}, "tamper_ok": True}),
        {"autosota_best_value": 0.80},
        fm_basic(primary_metric={"name": "score", "direction": "maximize",
                                 "saturation_bound": 0.99},  # no min_detectable_delta
                 heldout={"required": True, "split": "test"}),  # no mismatch_tolerance
        "beat",
    ))

    expected_finals = {
        "clean_beat": "beat",
        "hard_guard_fail": "below",
        "within_noise": "match",
        "compute_inflated": "match",
        "saturation_no_corrob": "match",
        "unknown_signal_pending": "pending_gate",
        "heldout_clean_beat": "beat",
        "heldout_overfit_mismatch": "pending_gate",
        "heldout_within_noise": "match",
        "heldout_missing_pending": "pending_gate",
        "heldout_tamper": "pending_gate",
        "heldout_degenerate_tol": "pending_gate",
    }

    ok = True
    for c in cases:
        exp = expected_finals[c["label"]]
        tag = "OK" if c["final"] == exp else "FAIL"
        if c["final"] != exp:
            ok = False
        print(f"[{tag}] {c['label']:28s} initial={c['initial']:<12} "
              f"final={c['final']:<14} expected={exp}  reasons={c['reasons']}",
              file=sys.stderr)
    print(json.dumps(cases, indent=2, ensure_ascii=False, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] == "--self-test":
        _sys.exit(_selftest())
    _sys.exit(2)  # must be imported as module in normal use
