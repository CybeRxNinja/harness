"""The model council: distinct-upstream seats that debate, vote, and replace losers.

The panel answers "who should argue about this topic". The council adds the
survival rule: one win/loss verdict per seat per round is recorded in
`.opencode/harness/council/<topic>.json`; a seat that loses twice in a row is
retired and the next build seats a different model on a different upstream.
History is keyed by `role|pin`, so a model that comes back keeps its record.

Votes are attributable: `voter>target=win|loss|tie[:note]`. A seat may never
judge its own answer and only seats may vote (a plain `target=verdict` still
works for back-compat, but carries no voter and cannot prove collusion). Two
seats that award each other a win in two different rounds are a bloc — the
lower-scoring member is retired as "collusion" and replaced automatically,
the same survival rule the loss streak uses.

No model values ever live here — only pins, profiles, votes, and timestamps.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import models as M
from .paths import state_dir

COUNCIL_VERSION = 1
RETIRE_STREAK = -2          # two consecutive losses
RETIRE_LOSSES = 3           # or three losses with no win at all
COLLUSION_ROUNDS = 2       # mutual wins in this many rounds = a bloc
MAX_ROUNDS = 20
MAX_SWAPS = 50
MAX_HISTORY = 100
MAX_COLLUSION = 20
VERDICTS = {"win": "win", "won": "win", "1": "win",
            "loss": "loss", "lost": "loss", "0": "loss",
            "tie": "tie", "neutral": "tie", "draw": "tie"}


def slug(topic: str) -> str:
    """Keep in sync with cli._bundle_slug (the panel bundle uses the same rule)."""
    raw = "".join(ch if (ch.isascii() and ch.isalnum()) else "-"
                  for ch in str(topic).strip().lower())
    while "--" in raw:
        raw = raw.replace("--", "-")
    return raw.strip("-") or "default"


def path(root: str | Path, topic: str = "") -> Path:
    return state_dir(root) / "council" / f"{slug(topic)}.json"


def _load(file: Path) -> dict:
    try:
        data = json.loads(file.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(file: Path, data: dict) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.rename(file)


def _retired_map(retired: list) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for row in retired:
        if not isinstance(row, dict):
            continue
        role, pin = str(row.get("role") or ""), str(row.get("pin") or "")
        if role and pin:
            out.setdefault(role, set()).add(pin)
    return out


def _prior(seats: list[dict]) -> dict:
    keep = ("role", "pin", "requirement", "upstream", "health", "checked_at",
            "fallback_pin")
    return {"panel": [{k: s.get(k) for k in keep}
                      for s in seats
                      if isinstance(s, dict) and s.get("role") and s.get("pin")
                      and isinstance(s.get("requirement"), dict)]}


def _profile(pin: str) -> dict:
    cand = M.classify(str(pin).partition("#")[0])
    return {"tier": cand["tier"], "free": bool(cand["free"]),
            "context": int(cand.get("context") or 0),
            "strengths": list(cand.get("strengths") or []),
            "released": cand.get("released") or "",
            "knowledge": cand.get("knowledge") or ""}


def _clean_stats(row: dict | None) -> dict:
    row = row if isinstance(row, dict) else {}
    return {"wins": int(row.get("wins") or 0),
            "losses": int(row.get("losses") or 0),
            "streak": int(row.get("streak") or 0),
            "score": int(row.get("score") or 0),
            "last_verdict": str(row.get("last_verdict") or ""),
            "updated_at": int(row.get("updated_at") or 0)}


def build(root: str | Path, cfg: dict, topic: str = "", size: int = 3,
          roles: list[str] | None = None) -> tuple[dict, Path]:
    """Build or refresh the council roster with profiles, history and exclusions."""
    root_path = Path(root).resolve()
    topic = str(topic).strip() or "default"
    file = path(root_path, topic)
    old = _load(file)
    prior_seats = [s for s in (old.get("seats") or []) if isinstance(s, dict)]
    retired = [r for r in (old.get("retired") or []) if isinstance(r, dict)]
    history = old.get("history") if isinstance(old.get("history"), dict) else {}
    configured = M.role_routes(cfg)
    wanted = [str(r).strip() for r in (roles or []) if str(r).strip()]
    if not wanted:
        # A role whose only seat was retired is still a role: keep it in the
        # order so build() can seat its replacement, not silently drop the seat.
        wanted = [str(s.get("role")) for s in prior_seats]
        wanted += [str(r.get("role")) for r in retired]
    if not wanted:
        wanted = list(M.panel_cycle(size))
    wanted = [r for r in dict.fromkeys(wanted) if r in configured]
    order = list(M.panel_cycle(size))
    wanted.sort(key=lambda r: order.index(r) if r in order else 99)
    exclude = _retired_map(retired)
    ttl = int((cfg.get("models") or {}).get("health_ttl_s") or 1800)
    # A phase call (roles=[...]) narrows what is re-resolved, not the roster.
    # Seats outside the phase keep their pin and their upstream stays reserved.
    requested = set(wanted)
    carried: list[dict] = []
    for seat in prior_seats:
        role, pin = str(seat.get("role") or ""), str(seat.get("pin") or "")
        if (role in configured and role not in requested and pin
                and pin not in exclude.get(role, set())):
            carried.append(seat)
    reserved = {M.upstream_key(str(s.get("pin") or "").partition("#")[0])
                for s in carried if s.get("pin")}
    real = not os.environ.get("HARNESS_MOCK")
    # A stale ledger entry is not a failure: probe the seats we are about to
    # keep, so a healthy pin does not drift-swap on every rebuild.
    if prior_seats and real:
        stale = [str(s.get("pin") or "") for s in prior_seats
                 if str(s.get("pin") or "")
                 and str(s.get("pin")) not in exclude.get(str(s.get("role")), set())
                 and M.health_state(str(s.get("pin") or ""), ttl)[0] != "ok"]
        if stale:
            try:
                M.check(list(dict.fromkeys(stale)), cfg, root_path)
            except Exception:
                pass  # a failed probe is recorded; never break the rebuild
    roster = M.panel_pool(cfg, size=size, roles=wanted,
                          prior=_prior(prior_seats), exclude=exclude,
                          reserved=reserved)
    swaps_from_panel: list[dict] = [s for s in (roster.get("model_replaced") or [])
                                     if isinstance(s, dict)]
    # Verify the seats actually chosen (distinct upstreams can skip the model a
    # role probe verified). A failed seat is re-seated; each round also retires
    # a dead PROVIDER, so allow enough rounds for a cold roster to converge.
    if real:
        for _round in range(3):
            verify = [str(s.get("pin") or "") for s in roster["panel"]
                      if s.get("pin")
                      and M.health_state(str(s.get("pin")), ttl)[0] != "ok"]
            if not verify:
                break
            try:
                M.check(list(dict.fromkeys(verify)), cfg, root_path)
            except Exception:
                break
            before = [str(s.get("pin") or "") for s in roster["panel"]]
            roster = M.panel_pool(cfg, size=size, roles=wanted,
                                  prior=_prior(roster["panel"]), exclude=exclude,
                                  reserved=reserved)
            swaps_from_panel.extend(
                s for s in (roster.get("model_replaced") or [])
                if isinstance(s, dict))
            if [str(s.get("pin") or "") for s in roster["panel"]] == before:
                break
    def _seat(row: dict) -> dict | None:
        role, pin = str(row.get("role") or ""), str(row.get("pin") or "")
        if not role or not pin:
            return None
        profile = _profile(pin)
        stats = _clean_stats(history.get(f"{role}|{pin}"))
        requirement = row.get("requirement") or {}
        wanted_tier = str(requirement.get("tier") or "")
        note = ""
        if wanted_tier and profile["tier"] != wanted_tier:
            note = f"no verified {wanted_tier} seat — using {profile['tier']}"
        return {
            "role": role, "pin": pin,
            "upstream": row.get("upstream") or M.upstream_key(pin.partition("#")[0]),
            "requirement": requirement,
            "health": row.get("health") or "unverified",
            "checked_at": int(row.get("checked_at") or 0),
            "fallback_pin": row.get("fallback_pin") or "",
            "tier": profile["tier"], "free": profile["free"],
            "context": profile["context"], "strengths": profile["strengths"],
            "released": profile["released"], "knowledge": profile["knowledge"],
            "note": note,
            **stats,
        }

    seats = [s for s in (_seat(row) for row in roster["panel"]) if s]
    seated = {str(s["role"]) for s in seats}
    for row in carried:
        role, pin = str(row.get("role") or ""), str(row.get("pin") or "")
        if role in seated:
            continue
        state, entry = M.health_state(pin, ttl)
        seat = _seat({**row, "health": state,
                      "checked_at": int((entry or {}).get("checked_at") or 0),
                      "upstream": row.get("upstream")
                      or M.upstream_key(pin.partition("#")[0])})
        if seat:
            seats.append(seat)
            seated.add(role)
    cycle = list(M.panel_cycle(size))
    seats.sort(key=lambda s: cycle.index(s["role"]) if s["role"] in cycle else 99)
    if len(history) > MAX_HISTORY:
        kept = sorted(history.items(),
                      key=lambda kv: int((kv[1] or {}).get("updated_at") or 0))
        history = dict(kept[-MAX_HISTORY:])
    swaps = [s for s in (old.get("model_replaced") or []) if isinstance(s, dict)]
    swaps.extend(swaps_from_panel)
    doc = {
        "version": COUNCIL_VERSION,
        "topic": topic,
        "project": str(root_path),
        "created_at": int(old.get("created_at") or time.time()),
        "updated_at": int(time.time()),
        "round": int(old.get("round") or 0),
        "seats": seats,
        "history": history,
        "retired": retired,
        "rounds": [r for r in (old.get("rounds") or []) if isinstance(r, dict)],
        "model_replaced": swaps[-MAX_SWAPS:],
        "collusion": [c for c in (old.get("collusion") or []) if isinstance(c, dict)],
    }
    _write(file, doc)
    return doc, file


def parse_vote(text: str) -> tuple[str, str, str, str]:
    """`[voter>]target=win[:note]` -> (voter, target, verdict, note)."""
    raw = str(text or "").strip()
    if "=" not in raw:
        return "", "", "", ""
    left, rest = raw.split("=", 1)
    verdict, _, note = rest.partition(":")
    voter = ""
    if ">" in left:
        voter, _, left = left.partition(">")
    return (voter.strip(), left.strip(),
            VERDICTS.get(verdict.strip().lower(), ""), note.strip())


def _round_ballots(round_doc: dict) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """(voter -> {target: verdict}, role -> pin) for one attributed round."""
    ballots: dict[str, dict[str, str]] = {}
    pins: dict[str, str] = {}
    for vote in round_doc.get("votes") or []:
        if not isinstance(vote, dict):
            continue
        voter = str(vote.get("voter") or "")
        target = str(vote.get("role") or "")
        if not voter or not target or voter == target:
            continue
        ballots.setdefault(voter, {})[target] = str(vote.get("verdict") or "")
        pins.setdefault(voter, str(vote.get("voter_pin") or ""))
        pins.setdefault(target, str(vote.get("pin") or ""))
    return ballots, pins


def detect_collusion(rounds: list, need: int = COLLUSION_ROUNDS) -> list[dict]:
    """Reciprocal-win blocs, from attributed votes only.

    Two seats collude when each awards the other a win in the same round. One
    shared win can be genuine; `need` rounds of it is a voting pact. The bloc
    is keyed by ROLE+PIN, so a reseated role starts a clean record: evidence
    about the model that was voted out never retires its replacement. Returns
    `[{"pair": [a, b], "pins": {...}, "rounds": [n, ...]}]`.
    """
    found: dict[tuple, dict] = {}
    for round_doc in rounds:
        if not isinstance(round_doc, dict):
            continue
        ballots, pins = _round_ballots(round_doc)
        voters = sorted(ballots)
        for i, a in enumerate(voters):
            for b in voters[i + 1:]:
                if ballots[a].get(b) != "win" or ballots[b].get(a) != "win":
                    continue
                pin_a, pin_b = pins.get(a, ""), pins.get(b, "")
                key = (a, pin_a, b, pin_b)
                entry = found.setdefault(
                    key, {"pair": [a, b], "pins": {a: pin_a, b: pin_b},
                          "rounds": []})
                entry["rounds"].append(int(round_doc.get("round") or 0))
    return [e for e in found.values() if len(e["rounds"]) >= need]


def record(root: str | Path, cfg: dict, topic: str = "",
           votes: list[str] | None = None, size: int = 3,
           roles: list[str] | None = None) -> tuple[dict, Path]:
    """Apply one win/loss/tie per seat, retire repeat losers, rebuild the roster."""
    root_path = Path(root).resolve()
    topic = str(topic).strip() or "default"
    file = path(root_path, topic)
    doc = _load(file)
    if not doc:
        doc, file = build(root_path, cfg, topic, size=size, roles=roles)
    seats = [s for s in (doc.get("seats") or []) if isinstance(s, dict)]
    by_role = {str(s.get("role")): s for s in seats}
    history = doc.get("history") if isinstance(doc.get("history"), dict) else {}
    now = int(time.time())
    applied: list[dict] = []
    warnings: list[str] = []
    for raw in votes or []:
        voter, role, verdict, note = parse_vote(str(raw))
        if not role or not verdict:
            warnings.append(f"ignored vote {raw!r}: use [voter>]role=win|loss|tie[:note]")
            continue
        if voter and voter not in by_role:
            warnings.append(f"ignored vote {raw!r}: {voter} is not a seat")
            continue
        if voter and voter == role:
            warnings.append(f"ignored vote {raw!r}: a seat never judges its own answer")
            continue
        seat = by_role.get(role)
        if seat is None:
            warnings.append(f"ignored vote {raw!r}: no seat for {role}")
            continue
        stats = _clean_stats(seat)
        if verdict == "win":
            stats["wins"] += 1
            stats["streak"] = max(1, stats["streak"] + 1)
            stats["score"] += 1
        elif verdict == "loss":
            stats["losses"] += 1
            stats["streak"] = min(-1, stats["streak"] - 1)
            stats["score"] -= 1
        else:
            stats["streak"] = 0
        stats["last_verdict"] = verdict
        stats["updated_at"] = now
        seat.update(stats)
        history[f"{role}|{seat.get('pin')}"] = {**stats}
        applied.append({"voter": voter,
                        "voter_pin": str((by_role.get(voter) or {}).get("pin") or ""),
                        "role": role, "pin": str(seat.get("pin") or ""),
                        "verdict": verdict, "note": note, "at": now})
    if not applied:
        doc["warnings"] = warnings
        _write(file, doc)
        return doc, file

    retired = [r for r in (doc.get("retired") or []) if isinstance(r, dict)]
    retired_before = len(retired)
    keep: list[dict] = []
    for seat in seats:
        stats = _clean_stats(seat)
        reason = ""
        if stats["streak"] <= RETIRE_STREAK:
            reason = f"lost {abs(stats['streak'])} in a row"
        elif stats["losses"] >= RETIRE_LOSSES and stats["wins"] == 0:
            reason = f"{stats['losses']} losses, no win"
        if reason:
            already = any(r.get("role") == seat.get("role")
                          and r.get("pin") == seat.get("pin") for r in retired)
            if not already:
                retired.append({"role": seat.get("role"), "pin": seat.get("pin"),
                                "reason": reason, "at": now})
        else:
            keep.append(seat)
    doc["seats"] = keep
    doc["retired"] = retired
    doc["history"] = history
    doc["round"] = int(doc.get("round") or 0) + 1
    rounds = [r for r in (doc.get("rounds") or []) if isinstance(r, dict)]
    rounds.append({"round": doc["round"], "votes": applied, "at": now})
    doc["rounds"] = rounds[-MAX_ROUNDS:]
    # Collusion is a survival offense too: seats that keep awarding each other
    # a win are one voice, not two. Retire the lower scorer to break the bloc.
    blocs = detect_collusion(doc["rounds"])
    if blocs:
        def _bloc_key(c: dict) -> tuple:
            roles = tuple(str(p) for p in (c.get("pair") or [])[:2])
            pins = tuple(str((c.get("pins") or {}).get(r) or "") for r in roles)
            return roles + pins

        merged = {_bloc_key(c): c for c in (doc.get("collusion") or [])
                  if isinstance(c, dict)}
        seated = {str(s.get("role")): s for s in keep}
        current_pin = {r: str(s.get("pin") or "") for r, s in seated.items()}
        for entry in blocs:
            pair = [str(p) for p in entry.get("pair") or []][:2]
            pins = entry.get("pins") or {}
            key = _bloc_key({**entry, "pins": pins})
            merged[key] = {**entry, "pair": pair,
                           **{k: v for k, v in merged.get(key, {}).items()
                              if k in ("retired", "at")}}
            # Old evidence may describe pins this roster no longer runs, and a
            # pattern only retires the seats that just cast those votes.
            if any(str(pins.get(r) or "") != current_pin.get(r) for r in pair):
                continue
            if int((entry.get("rounds") or [0])[-1]) != doc["round"]:
                continue
            present = [seated[r] for r in pair if r in seated]
            if len(present) < 2:
                continue
            victim = sorted(present, key=lambda s: (int(s.get("score") or 0),
                                                    int(s.get("wins") or 0),
                                                    str(s.get("role"))))[0]
            if any(r.get("role") == victim.get("role")
                   and r.get("pin") == victim.get("pin") for r in retired):
                continue
            partner = next(r for r in pair if r != str(victim.get("role")))
            reason = (f"collusion with {partner} in "
                      f"{len(entry.get('rounds') or [])} rounds")
            retired.append({"role": victim.get("role"), "pin": victim.get("pin"),
                            "reason": reason, "at": now})
            keep.remove(victim)
            merged[key]["retired"] = str(victim.get("pin") or "")
            merged[key]["at"] = now
        doc["collusion"] = list(merged.values())[-MAX_COLLUSION:]
    doc["seats"] = keep
    doc["retired"] = retired
    _write(file, doc)

    # A retirement is a full-roster repair: a phase-scoped call must not leave
    # the role that just lost its seat out of the topic file.
    rebuild_roles = None if len(retired) > retired_before else roles
    rebuilt, file = build(root_path, cfg, topic, size=size, roles=rebuild_roles)
    rebuilt["warnings"] = warnings
    new_by_role = {str(s.get("role")): s for s in rebuilt.get("seats") or []}
    swaps = [s for s in (rebuilt.get("model_replaced") or []) if isinstance(s, dict)]
    for old_seat in seats:
        role, old_pin = str(old_seat.get("role") or ""), str(old_seat.get("pin") or "")
        if any(r.get("role") == role and r.get("pin") == old_pin for r in retired):
            new_pin = str((new_by_role.get(role) or {}).get("pin") or "")
            if new_pin and new_pin != old_pin:
                why = next((str(r.get("reason") or "") for r in retired
                            if r.get("role") == role and r.get("pin") == old_pin), "")
                event = {"role": role, "old": old_pin, "new": new_pin,
                         "reason": ("retired by collusion" if "collusion" in why
                                    else "retired by council vote"), "at": now}
                if event not in swaps:
                    swaps.append(event)
    rebuilt["model_replaced"] = swaps[-MAX_SWAPS:]
    _write(file, rebuilt)
    return rebuilt, file


__all__ = ["build", "record", "parse_vote", "detect_collusion", "path", "slug"]
