"""
seed.py
-------
Seeds nine read-only section templates, one per Harmony operation, from
the "Stoping Bonus Policy Full Comparison" review (Aug 2026). These are
STARTING POINTS, not verified payroll rates — the comparison document
explicitly excludes annexure/rate tables, so every Rand rate here is a
round, clearly-editable placeholder except where the source document
states an actual figure in its body text (those are called out in each
parameter's `notes`). Duplicate a template into your own scheme before
changing anything — templates themselves are protected from editing in
the UI.

Idempotent: safe to call on every app startup. Detects "already seeded"
by template_key rather than by row count, so adding a 10th template later
won't require a data wipe.
"""

from __future__ import annotations

from . import schemes as store
from .db import get_conn


def _param(name, basis, value, linked_metric=None, notes=None, gate_metric=None):
    return {"name": name, "enabled": True, "basis": basis, "value": value,
            "linked_metric": linked_metric, "notes": notes, "gate_metric": gate_metric}


TEMPLATES = [
    {
        "key": "doornkop",
        "name": "Doornkop — Stope Team & Miner Bonus",
        "section_label": "Doornkop",
        "note": "RE_202410 / REV01, effective Nov 2024. Base bonus driven by m² achieved; "
                "safety/width/trigger components layered on top.",
        "base_cfg": {"basis": "sqm", "threshold": 300, "threshold_bonus": 100, "periods": 1, "use_bands": 1},
        "bands": [{"crew_count": 10, "payout_pct": 100, "sort_order": 0}],
        "parameters": [
            _param("Safety Bonus", "pct_base", 50,
                   notes="Policy 4.2.1.1: 50% of base portion. 4.2.1.2: one safety incident or one loss-of-"
                         "life incident on a crew forfeits its whole 50% Safety Bonus. 4.2.1.3: a Section 54 "
                         "incident is a smaller -25% of the Safety portion instead (not modelled as a separate "
                         "gate here — apply that case by editing this parameter's Value to 25 for the period). "
                         "Qualifying Crews is set to auto-subtract Safety Incidents below: each recorded "
                         "incident is treated as one crew losing its Safety Bonus outright, per 4.2.1.2.",
                   gate_metric="safety_incidents"),
            _param("Physical Condition Bonus", "pct_base", 50,
                   notes="Policy 4.5.1: 50% of base portion, earned when the average physical condition "
                         "percentage of crew workplaces (audited monthly by the Safety Department) is 95% "
                         "and above. That audit percentage isn't one of this tool's reference metrics — set "
                         "this parameter's Value to 0 for a period where the 95% threshold wasn't met."),
            _param("Sweepings Penalty", "pct_base", -25,
                   notes="Policy 4.7.1.1: panel not swept to within 9 m of the face at mid-month measuring: "
                         "-25%. 4.7.1.2: still not to standard (4.5 m) by month-end: a further -25% (-50% "
                         "combined). 4.7.1.4: this penalty applies to the base bonus before the other bonus "
                         "metrics (Condition + Stope Width + Trigger) are calculated on it."),
            _param("Stoping Width Bonus", "pct_base", 50,
                   notes="Policy 4.4.1: 50% of base portion. 4.4.2: qualifies at stoping ≤120 cm, "
                         "ledging ≤150 cm, wide raise ≤150 cm."),
            _param("Trigger Bonus (Difficult Conditions)", "pct_base", 50,
                   notes="Policy 4.6: 50% of the crew's production bonus (worked example sums Base + Stoping "
                         "Width + Safety, not Physical Condition) once a crew exceeds a m² threshold: 350 m² "
                         "stoping, 290 m² ledging, or 340 m² wide raise (170 m² before the policy's x2 wide-"
                         "raise factor). This tool applies the % to the whole Base Bonus rather than that "
                         "exact three-way subtotal, and doesn't yet model 4.6's separate 50%-of-Safety "
                         "\"Safety Trigger Bonus\" line from the worked example in policy §12 — ask to have "
                         "that modelled exactly if the difference matters for your scenario."),
            _param("Quality Drilling (Driller)", "rand_per_unit", 80, "quality_blast_count",
                   notes="Policy 4.10.4.1: R80 per quality blast shift for the driller (4.10.4.2: R20 for a "
                         "non-driller, not modelled as a separate parameter here)."),
            _param("AWOP Penalty", "pct_total", -50,
                   notes="Policy 4.9.1: 1 AWOP = -50% of total bonus. 2 or more AWOP = -100% (edit the value "
                         "to -100 to model that case)."),
        ],
    },
    {
        "key": "joel",
        "name": "Joel — Stoping Bonus (Crew & Miner)",
        "section_label": "Joel",
        "note": "JC_202505_STOPE_REV02, read with Apr/Mar 2025 clarifications. Minimum crew size 12.",
        "base_cfg": {"basis": "sqm", "threshold": 300, "threshold_bonus": 100, "periods": 1, "use_bands": 1},
        "bands": [{"crew_count": 10, "payout_pct": 100, "sort_order": 0}],
        "parameters": [
            _param("Safety Bonus", "pct_base", 25,
                   notes="+25% add-on to base. One LTI forfeits the whole safety portion; PIVOT must be >90%."),
            _param("Sweepings Penalty", "pct_base", -25,
                   notes=">12 m during routine/ad-hoc assessment: -25% per failed visit/cycle (ORM-monitored)."),
            _param("Stoping Width Factor", "pct_base", 0,
                   notes="Real policy uses a corrected factor table (1.65 at <100cm down to 1.00 at ≥120cm), "
                         "not a flat %. Left at 0 — set a representative % here, or model width as its own metric."),
            _param("AWOP Penalty", "pct_total", -50,
                   notes="1 AWOP = -50%. 2+ AWOP = -100%."),
        ],
    },
    {
        "key": "kusasalethu",
        "name": "Kusasalethu — Stoping Incentive Scheme",
        "section_label": "Kusasalethu",
        "note": "RE_202601_STP, Jan 2026. Entry level 50 m² for all reef types.",
        "base_cfg": {"basis": "sqm", "threshold": 50, "threshold_bonus": 100, "periods": 1, "use_bands": 1},
        "bands": [{"crew_count": 10, "payout_pct": 100, "sort_order": 0}],
        "parameters": [
            _param("Safety Bonus", "pct_base", 25, notes="+25% add-on to base for zero accidents."),
            _param("Sweepings Bonus", "pct_base", 50,
                   notes="Unusual among these mines: sweepings passed to standard EARNS +50% of base "
                         "(not a penalty). Backfill on sweepings can separately attract a -50% total-bonus penalty."),
            _param("Quality Drilling (RDO)", "rand_per_unit", 40, "quality_blast_count",
                   notes="R40 per qualifying RDO blasting shift (~0.9 m advance)."),
            _param("AWOP Penalty", "pct_total", -50, notes="1 AWOP = -50%. 2+ AWOP = -100%."),
        ],
    },
    {
        "key": "masimong",
        "name": "Masimong — Stoping Cat 4-8 & Miner Bonus",
        "section_label": "Masimong",
        "note": "FM_202508_STP_REV01, Aug 2025. Category-based production caps apply (e.g. Ledging/Basal 1,010 m², Drive 850 m²).",
        "base_cfg": {"basis": "sqm", "threshold": 300, "threshold_bonus": 100, "periods": 1, "use_bands": 1},
        "bands": [{"crew_count": 10, "payout_pct": 100, "sort_order": 0}],
        "parameters": [
            _param("Safety Bonus", "pct_base", 25,
                   notes="+25% add-on. LOL/1 LTI removes it entirely; 1 dressing -50%, 2 dressings -100%."),
            _param("Sweepings Penalty", "pct_base", -25,
                   notes=">9 m any time: -25% total. >7 m on measuring day: -50% (consider a separate "
                         "pct_total parameter for that harsher case)."),
            _param("Quality Drilling (RDO)", "rand_per_unit", 80, "quality_blast_count",
                   notes="R80 per quality blast."),
            _param("Safety Rep Bonus", "fixed", 500,
                   notes="R500 fixed for full shifts (Alternative Safety Rep R250). Per Nov 2022 GM memo."),
            _param("AWOP Penalty", "pct_total", -50, notes="1 AWOP = -50%. 2+ AWOP = -100%."),
        ],
    },
    {
        "key": "moab",
        "name": "MOAB (Khotsong) — Stope Team Bonus",
        "section_label": "MOAB Khotsong",
        "note": "MA_202507_STPTEAM_REV04, Jul 2025. Most extensive set of special mining-condition factors "
                "of the compared mines (channel width, pillar, winze/down-dip, below-level).",
        "base_cfg": {"basis": "sqm", "threshold": 300, "threshold_bonus": 100, "periods": 1, "use_bands": 1},
        "bands": [{"crew_count": 10, "payout_pct": 100, "sort_order": 0}],
        "parameters": [
            _param("Safety Bonus", "pct_base", 100,
                   notes="Safety = 100% of Breaking Bonus — the highest safety weighting of all compared mines. "
                         "Golden Control/DMPR stop: first forfeits 50% of safety, second forfeits 100%."),
            _param("Sweepings Bonus", "pct_base", 0,
                   notes="Separate bonus for sweepings within standard (~4.6 m Middle Mine / 5.0 m Top Mine). "
                         "Set a % once your local standard's rate is confirmed."),
            _param("AWOP Penalty", "pct_total", -50,
                   notes="50% per AWOP shift on total bonus; an AWOP also forfeits a separate top-up bonus (not modelled)."),
        ],
    },
    {
        "key": "mponeng",
        "name": "Mponeng — Stoping Incentive",
        "section_label": "Mponeng",
        "note": "WA_202604_STP, Apr 2026. Physical-condition gate can cut the whole bonus (see notes).",
        "base_cfg": {"basis": "sqm", "threshold": 300, "threshold_bonus": 100, "periods": 1, "use_bands": 1},
        "bands": [{"crew_count": 10, "payout_pct": 100, "sort_order": 0}],
        "parameters": [
            _param("Safety Bonus", "pct_base", 25,
                   notes="+25% safety add-on. LTI removes it for the crew; a fatality forfeits the whole "
                         "section's Safety Bonus for the month."),
            _param("Sweepings Bonus", "pct_base", 25, notes="Sweepings to standard = +25% add-on to base."),
            _param("Quality / Condition Bonus", "pct_base", 30,
                   notes="30% quality add-on: 10% each for stoping width within 10 cm of allocation, "
                         "≥85% support-standard compliance, and lock-up tonnes within 10%."),
            _param("AWOP Penalty", "pct_total", -50,
                   notes="1 AWOP = -50%. 2+ AWOP = -100%. Code 18 is treated as AWOP."),
        ],
    },
    {
        "key": "phakisa",
        "name": "Phakisa — Stoping Cat 4-8 Bonus",
        "section_label": "Phakisa",
        "note": "JJ_202608_STPTEAM_REV04, 06 Aug 2026. Netting add-on of 6.6% is distinctive to this scheme.",
        "base_cfg": {"basis": "sqm", "threshold": 300, "threshold_bonus": 100, "periods": 1, "use_bands": 1},
        "bands": [{"crew_count": 10, "payout_pct": 100, "sort_order": 0}],
        "parameters": [
            _param("Safety Bonus", "pct_base", 10,
                   notes="+10% of qualifying bonus — notably lower than the other compared mines."),
            _param("Sweepings Penalty", "pct_base", -25,
                   notes="Mid-month failure: -25% TOTAL bonus. Measuring-day failure: -50% TOTAL "
                         "(consider a pct_total parameter for the harsher case)."),
            _param("Stoping Width Bonus", "pct_base", 15,
                   notes="Every 10 cm reduction from authorised target: +15% BASE. ≥20 cm reduction: +25% BASE."),
            _param("Quality Drilling", "rand_per_unit", 100, "quality_blast_count",
                   notes="R100 per qualifying quality blast, capped by measuring shifts (separate scheme)."),
            _param("Netting Bonus", "pct_base", 6.6,
                   notes="Permanent netting, when pre-planned and approved: +6.6% of qualifying bonus after Safety."),
            _param("AWOP Penalty", "pct_total", -50,
                   notes="1 AWOP = -50%. 2+ AWOP = -100%, calculated on total bonus including driller bonus."),
        ],
    },
    {
        "key": "target",
        "name": "Target — NRM Stoping Bonus Incentive",
        "section_label": "Target",
        "note": "PA_202605_NRMSTP_REV10, review 14 May 2026. Fixed-Rand-per-parameter structure rather than "
                "%-based add-ons. Flagged inconsistency: intro states minimum 150/180 m², but §6.1.1 states "
                "225 m²/crew — not resolved here, worth confirming with the Bonus Department.",
        "base_cfg": {"basis": "efficiency", "threshold": 16, "threshold_bonus": 100, "periods": 1, "use_bands": 1},
        "bands": [{"crew_count": 10, "payout_pct": 100, "sort_order": 0}],
        "parameters": [
            _param("Miner Safety Bonus", "fixed", 2500,
                   notes="Fixed amount, payable only if no dressings/LTIs recorded for the crew."),
            _param("Team Leader Safety Bonus", "fixed", 750, notes="Fixed amount, same qualifying condition."),
            _param("RDO/Winch Safety Bonus", "fixed", 500, notes="Fixed amount, same qualifying condition."),
            _param("Team Member Safety Bonus", "fixed", 500, notes="Fixed amount, same qualifying condition."),
            _param("Sweepings Parameter", "fixed", 2500,
                   notes="Tiered on a R2,500 base: <9.0 m = 100%, 9.1–12.0 m = 50%, >12.0 m = 0%. "
                         "Set this value to the tier actually earned."),
            _param("Stoping Width Parameter", "fixed", 2500,
                   notes="R2,500 within the planned ±5% band, 0 above the maximum. Gold planned width "
                         "≥2.3 m can apply a 1.5 factor to workplace SQM (not modelled here)."),
            _param("Quality Drilling (RDO)", "rand_per_unit", 100, "quality_blast_count",
                   notes="R100 per qualifying quality-drilling shift."),
            _param("AWOP Penalty", "pct_total", -50, notes="1 AWOP = -50%. 2+ AWOP = -100%."),
        ],
    },
    {
        "key": "tshepong",
        "name": "Tshepong — Stoping Cat 4-8 Bonus",
        "section_label": "Tshepong",
        "note": "JB_202603, Mar 2026. Netting add-on (20%) and miner cap (R150,000) are the highest among compared mines.",
        "base_cfg": {"basis": "sqm", "threshold": 300, "threshold_bonus": 100, "periods": 1, "use_bands": 1},
        "bands": [{"crew_count": 10, "payout_pct": 100, "sort_order": 0}],
        "parameters": [
            _param("Safety Bonus", "pct_base", 25,
                   notes="~+25% safety enhancement to base/qualifying bonus when no safety event applies."),
            _param("Sweepings Penalty", "pct_total", -50,
                   notes="Measuring-day sweepings failure = -50% TOTAL bonus."),
            _param("Netting Bonus", "pct_base", 20,
                   notes="Permanent netting: +20% of qualifying bonus after Safety, for authorised crews "
                         "(dip ≥45° also gets +20% to m², not modelled separately here)."),
            _param("Quality Drilling", "rand_per_unit", 100, "quality_blast_count",
                   notes="R100 per qualifying quality blast (separate scheme)."),
            _param("AWOP Penalty", "pct_total", -50,
                   notes="1 AWOP = -50%. 2+ AWOP = -100% (standard scheme rule, includes Driller bonus)."),
        ],
    },
]


def ensure_templates() -> None:
    """Refresh every template's content on each app startup.

    Templates are read-only reference data — nothing in the UI can edit them,
    and cloning copies a scheme's data at clone time rather than keeping a
    live link back to its template — so there's no user data to lose by
    dropping and recreating them whenever this module's TEMPLATES definition
    changes. This trades a stable template `id` (not needed by anything) for
    a guarantee that editing TEMPLATES here always takes effect on restart,
    rather than only affecting brand-new databases.
    """
    conn = get_conn()
    try:
        existing_ids_by_key = {
            r["template_key"]: r["id"]
            for r in conn.execute("SELECT id, template_key FROM schemes WHERE is_template = 1").fetchall()
        }
    finally:
        conn.close()

    for tpl in TEMPLATES:
        stale_id = existing_ids_by_key.get(tpl["key"])
        if stale_id is not None:
            store.delete_scheme(stale_id)
        created = store.create_scheme(
            name=tpl["name"],
            description=tpl["note"],
            section_label=tpl["section_label"],
            is_template=True,
            template_key=tpl["key"],
            template_note=tpl["note"],
        )
        scheme_id = created["id"]
        store.update_base_cfg(scheme_id, tpl["base_cfg"])
        for b in tpl["bands"]:
            store.create_band(scheme_id, b)
        for p in tpl["parameters"]:
            store.create_parameter(scheme_id, p)
