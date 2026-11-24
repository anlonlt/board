# What awaits Anlon

- [app] On the bench, plug it in, then run the pump hard: does the sensor turn itself down (What the box saw, "flow"),
  and is it still strong enough? Default: keep rule G5 (half after a blocked line). — `docs/PLAN.md` § C1 item 5 · deadline 2026-11-22
- [app] After a dry pot (too dry / fine / too wet) its tip is offered as "A tip for this pot"; the rule
  said only after watering. Keep it, or none? Default: keep (a wilted leaf has no other door). — `CardFlow.tipLabel` · deadline 2026-11-30
- [app] F4 on the box: calibrate once; does the pump stop under the first leak (Reports → What the box saw: "first leak at"), and do the stars
  feel fair at the widest level, moving after each day? Default: as built. — `docs/box/2026-11-12-F4-findings.md` · deadline 2026-11-30
- [app] Looks, from pictures: the bed at the end (bed_1/2/3), the drops (drop_1/2/3), the close view of one leaf
  (zoom_1/2/3), all in docs/screens. Default: columns, waves, bar. — `BedLook`, `DropLook`, `ZoomLook` · deadline 2026-11-30
- [app] A plant's branches (faster, alone, both) add 72 week-2 cards: they hold week 3 back (day 27 not 21 at 12 min).
  Count them toward the gate, or not? Default: they count. — `docs/PLANTS.md`, q-app-branches-gate · deadline 2026-11-30
- [pump] N6b samples (samples/pump/2026-11-14-n6b/, board N6b-nozzle-fixes): low without its hiss; mid 220/247 with
  the filter (less rattle, a little less flow): keep it? high 330's start muted or not? Default: the filter on, high as it is. — `docs/PUMP.md` item 10 · deadline 2026-11-28
- [pump] Samples (render-samples.sh, 1-reference/ b-now vs a-before): does the pump now start softly (ramp,
  settle 0.2 s before the valve)? ±35 ml right? Default: keep it all. — `docs/PUMP.md`, "N2: a soft start" · deadline 2026-11-30
- [pump] "slow" night mode: the rounded candidate (e-rounded, now with the ramp) against b-now: is it quiet?
  Default: keep b-now. — `Mode.SLOW_NIGHT_ROUNDED`, `docs/PUMP.md` "N2: notes on the N2 samples" · deadline 2026-11-30
- [pump] Stronger jets or clearer readings? N1 can follow the tank as it empties (a stronger jet above half),
  but then readings above 4 l drift less. Default: off (clearer readings). — `Pump.N1_FOLLOWS_TANK`, `docs/PUMP.md` "H1" · deadline 2026-11-30
- [pump] T40 (Test bench → Device lab, 10 min): run one valve five ways on purpose and rank them; your ranks decide which
  lines show. Default: only flow and pressure show. — `docs/TESTING.md` T40 · deadline 2026-11-30
- [bench] On the bench the "same bed" and twin-bed cards hold their valve with a pulse (from your next try,
  once the delay is measured). Keep it, or steady? Default: pulse. — step `s1-held-pulse`, board Q4-bench · deadline 2026-11-30
- [bench] E31: the box waiting alone 0.5 s longer (1.5 s) before you pour lets the gauge read you at levels
  16–20 (23 of 24 pours against 18). Wait the extra 0.5 s a card? Default: yes, only at level 16 and over. — board E31-window · deadline 2026-11-30
- [look] The greenhouse at night (docs/screens/scene_*): drops as rain, the held valve as the moon, a bed as snow and its
  result as drifts, and the rest (LOOK.md § 9). Which stay at night? Default: your fallbacks, the needle and the columns. — `NightLooks`, q-look-aids-at-night · deadline 2026-11-30
- [look] On the tablet: is it smooth (no stutter while pouring), do presses and the slider feel springy, do the birds
  answer (lift the head, tails on the lock, tap one)? Default: as built. — `docs/LOOK.md` § 8 · deadline 2026-11-23
- [seeds] S38c: imported seeds' doubt marks: 44 % of seeds marked, a third on a bad one. Fewer (only where the
  second reading sees another seed: 21 % marked, 42 % on bad ones, a third fewer caught)? Default: as now. — board post `S38c-doubt-marks`, `docs/SCORE.md` § S38c · deadline 2026-11-30
- [seeds] Plant over a record: add a bed again (the new build reads its rows), My beds → the bed → "Plant over it" on a row,
  at the bench: watch, then plant your row in its gap. Your row as planted, the marks right? Default: as built. — `docs/RECORDS.md` · deadline 2026-11-30
- [seeds] Layers (T38): after a row over a bed's piece, "Add one above/below". Your layers read as planted? Do 1, 2 … All
  show each one alone, the box's rows gone? Default: as built. — `docs/RECORDS.md` § Layers · deadline 2026-11-30
- [seeds] In the south bed two rows carry the same plant together, one tall, one short. You are judged
  against the one nearer yours. Where the app draws one row, it draws the tall one: is that the one
  you would plant along? Default: yes. — `docs/RECORDS.md` § S10 · deadline 2026-11-30
- [seeds] Fast mode on the chip: never timed (13 Nov: 4 of 4 rows failed, the build lacked its table). Asked again
  only on a build whose list carries it. Default: none; seeds stay on the slow path (27.7 s per 30 s clip). — `docs/SEEDS.md` § M2c · deadline 2026-11-30
- [mix] The soil mix: the app's recipe with the bed's acidity, the trained model stays stored (end the model train)?
  Default: yes. — board `MX5-soil-mix-question`, `docs/SEEDS.md` § MX5.3 · deadline 2026-11-30
- [mix] W7 on the tablet: write an inner layer (write.inner) and tap Show: are the layers under yours right, in depth
  and weight? Default: step 5's layer as is. — `docs/STEP6.md` § W7 · deadline 2026-11-30
- [seeds] Row editor after EX7 (T21 again, its new steps): touched rows locked, edge slides with the auto zoom, the cut
  under the finger, labels above the keyboard. Anything off? Default: as built. — board `EX7-gesture-map-built` · deadline 2026-11-30
- [seeds] Labels on your tablet (T41): add a bed again, open it (the catalog, else the web → Paste), then Plant over it. Does
  each label sit on its row? Default: as built. — `docs/TESTING.md` T41 · deadline 2026-11-30
- [craft] T46 (Test bench → Device lab → "Your craft", ~12 min): 13 ways at 3 beds, then share the report? Default:
  the recogniser stays untested on your hands. — `docs/TESTING.md` T46, `docs/CRAFT.md` § Part X2 · deadline 2026-11-30
- [roots] Catalog: on the laptop, re-judge MB1's rejected roots with R2a (the shop refuses the cloud): `python3 tools/bench/catalog_check.py rejudge ~/cat-k3`. Default: they stay
  rejected (618 roots train). — `docs/ROOTS.md` § R3 · deadline 2026-11-30
- [roots] R3b (R2a fine-tuned with the catalog, rented; the old test decides): about $2.5 (at most $4); the bigger one only if earned,
  ~$3.5 more; small last. Go? Default: wait for your go. — `docs/prompts/R3b-train-with-catalog.md` · deadline 2026-11-30
- [mix] Over a dry (or sandy) base, water the seventh bed at 7:4 (the soft ratio, 31 ml below the mark) or
  at 9:5 (18 ml above the mark)? See both: `key.dry.base` (sessions: 7:4; the bench may ask 9:5). Default: 7:4. — `BaseSeventh`, step s3-dry-seventh · deadline 2026-11-30
- [course] Plot cards on imported beds: build 4-row plots only where the reader is sure (A: 94.1 % right, 22 of 44 beds
  give a card, on clean scans), or keep the hold (B)? Default: B. — board `PQ2-plots-on-beds` · deadline 2026-11-30
- [course] The 70 write cards count at the gate: at 12 min a day the simulated average gardener reaches part 3 later or not by day 60.
  Keep them at the gate, or leave them out (as name cards are capped)? Default: keep. — `docs/STEP6.md` W6, board T4-s1-plant-gate · deadline 2026-11-30
- [course] PQ1's find-the-row rungs: grade each bed against the row (default) or any plant, a jump marked?
  And planted only in spare time (nothing held back; none at 12 min/day) or like any card from week 3? Default: as built. — board PQ1-find-the-row · deadline 2026-11-30
- [border] B29: which edge should the bed cards use, by eye on the north, south and east beds (store `features/b29/`): (a) 3 cm
  below, (b) 6 cm below, (c) 3 cm moved to the stone, (d) style writer, or none? Default: (c). — board B29-eye-three-edges · deadline 2026-11-30
