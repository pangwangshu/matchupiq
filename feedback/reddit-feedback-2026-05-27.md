# Reddit Feedback Log - 2026-05-27

Source thread:
- https://www.reddit.com/r/WorldCup2026Tickets/comments/1topemd/i_wrote_a_small_tool_to_predict_most_likely/

## Why This Log Exists

Capture high-signal user feedback from the initial public launch so future model updates are grounded in real user trust and usability concerns.

## High-Value Feedback (Verbatim Themes)

1. Group F outputs underrepresent Sweden:
   - Users observed repeated Netherlands/Japan top-two outcomes with Sweden appearing too rarely.
2. Group D outputs underrepresent Turkey:
   - Users called out USA/Australia appearing as group winners while Turkey was absent in top outcomes.
3. Australia-first outcomes felt implausible to some users:
   - Some comments said Australia topping Group D looked unrealistic.
4. Perceived contradiction in knockout routes:
   - Complaint that Austria appears in Round of 16 paths where users expected Spain certainty.
5. Confidence/trust perception:
   - "No way", "kinda ass", "ChatGPT can make better predictions" style reactions indicate low trust in model realism.

## Validation Notes

1. Feedback about Turkey/Sweden underrepresentation is valid against current local outputs.
2. Austria-in-R16 is not necessarily a bug by itself; it can occur through low-probability upset paths after group stage.
3. The trust issue is partly presentation:
   - outputs are shown as "Top 10 predicted matchups" without uncertainty/context cues.

## Root-Cause Buckets

1. Data freshness risk:
   - ranking snapshot can lag current FIFA updates.
2. Model calibration risk:
   - ranking-driven formulas can produce overconfident favorite-heavy outcomes.
3. Search truncation risk:
   - beam limits can suppress tail scenarios for plausible underdogs.
4. Bracket mapping simplification risk:
   - third-place assignment logic is deterministic per world scenario rather than fully combinatorial.
5. Product communication gap:
   - users are not shown model limitations or confidence boundaries.

## Prioritized Actions

1. P0: Keep ranking data fresh and expose ranking date in UI/API.
2. P0: Add transparent confidence/limitations messaging to outputs.
3. P1: Revisit probability calibration and upset frequency.
4. P1: Revisit beam/truncation defaults and compare sensitivity.
5. P1: Implement full third-place allocation matrix handling.

## Success Criteria For Future Iterations

1. Group-level outcomes include realistic probability mass for debated teams (e.g., Turkey, Sweden) unless evidence strongly dictates otherwise.
2. Users can see why predictions were produced (data date + methodology summary).
3. Public feedback shifts from "implausible" to "debatable but reasonable."

