# What awaits Anlon

- [app] On the speaker, calibrate, then sing loud and close: does the phone turn itself down (What the app heard, "volume"),
  and is it still loud enough? Default: keep G5's rule (half after a clipped take). — `docs/WORKPLAN.md` § C1 item 5 · deadline 2026-11-22
- [app] After a heard card (too high / in tune / too low) its word is offered as "A word for what you just heard"; your rule
  said only after singing. Keep it, or none? Default: keep (sharp/flat has no other door). — `CardFlow.wordLabel` · deadline 2026-11-30
- [app] F4 on the phone: calibrate once; do the cards stop under your first break (Reports → What the app heard: "first break at"), and do the stars
  feel fair at the widest level, moving after each session? Default: as built. — `docs/phone/2026-11-12-F4-findings.md` · deadline 2026-11-30
- [app] Looks, from pictures: the lane at the end (review_1/2/3), the beats (beats_1/2/3), the close view of a held note
  (zoom_1/2/3), all in docs/screens. Default: columns, waves, bar. — `ReviewLook`, `BeatLook`, `ZoomLook` · deadline 2026-11-30
- [app] A line's branches (faster, alone, both) add 72 turn-2 cards: they hold turn 3 back (day 27 not 21 at 12 min).
  Count them toward the gate, or not? Default: they count. — `docs/CURRICULUM.md`, q-app-branches-gate · deadline 2026-11-30
- [voice] V6b samples (samples/neural-voice/2026-11-14-v6b/, board V6b-vocoder-bank-fixes): oo without its "ch"; ah 220/247 with
  the comb (less buzz, a little less breath): keep it? ee 330's start silenced or not? Default: the comb on, ee as it is. — `docs/VOICE.md` item 10 · deadline 2026-11-28
- [voice] Samples (render-samples.sh, 1-reference/ b-now vs a-before): does the voice now start like a person (breath,
  scoop, jitter, vibrato, settling 0.2 s before your turn)? ±35 c right? Default: keep it all. — `docs/VOICE.md`, "V2: a natural solo" · deadline 2026-11-30
- [voice] "oo" male: the rounded candidate (e-rounded, now with the glottal slope) against b-now: does it sound like "moo"?
  Default: keep b-now. — `Timbre.OO_MALE_ROUNDED`, `docs/VOICE.md` "V2: the owner's ears on the V2 samples" · deadline 2026-11-30
- [voice] Fuller voices or clearer beats? F1 can follow the note as a singer opens the vowel (a fuller "oo"/"ee" above E4),
  but then thirds above G4 beat less. Default: off (clearer beats). — `Timbre.Voice.F1_FOLLOWS_NOTE`, `docs/VOICE.md` "H1" · deadline 2026-11-30
- [voice] T40 (Test bench → Device lab, 10 min): sing one note five ways on purpose and rank them; your ranks decide which
  tone lines speak. Default: only breath and the vowel speak. — `docs/TESTING.md` T40 · deadline 2026-11-30
- [speaker] On the speaker the "same note" and octave cards sing their held note with vibrato (from your second take,
  once the delay is measured). Keep it, or a still note? Default: vibrato. — step `s1-held-vibrato`, board Z4-speaker · deadline 2026-11-30
- [speaker] E11: the phone holding its note alone 0.5 s longer (1.5 s) before you sing lets the grade read you at volumes
  16–20 (23 of 24 notes against 18). Wait the extra 0.5 s a card? Default: yes, only at volume 16 and over. — board E11-window · deadline 2026-11-30
- [look] The aids in the night (docs/screens/scene_*): beats as the aurora, the held note as the moon, a line as snow and its
  result as drifts, and the rest (LOOK.md § 9). Which stay in the night? Default: your fallbacks, the needle and the columns. — `SkyLooks`, q-look-aids-in-the-night · deadline 2026-11-30
- [look] On the phone: is it smooth (no stutter while singing), do presses and the slider feel springy, do the wolves
  answer your voice (howl, sharp lifts the head, tails on the lock, tap one)? Default: as built. — `docs/LOOK.md` § 8 · deadline 2026-11-23
- [songs] S18c: imported songs' doubt marks: 44 % of notes marked, a third on a bad note. Fewer (only where the
  second reading hears another note louder: 21 % marked, 42 % on bad notes, a third fewer caught)? Default: as now. — board post `S18c-doubt-marks`, `docs/SCOREBOARD.md` § S18c · deadline 2026-11-30
- [songs] Sing over a record: add a song again (the new build reads its pulse), My songs → the song → "Sing over it" on a piece,
  on the speaker: listen, then sing your part in its silence. Your line as you sang it, the marks right? Default: as built. — `docs/JUDGE-A-RECORDING.md` · deadline 2026-11-30
- [songs] Layering (T38): after a take over a song's piece, "Add one above/below". Your harmonies read as sung? Do 1, 2 … All
  play each one alone, the phone's notes gone? Default: as built. — `docs/JUDGE-A-RECORDING.md` § Layering · deadline 2026-11-30
- [songs] In Photograph's chorus two voices sing the tune together, one high, one an octave lower. You are judged
  against the one nearer your note. Where the app draws the tune alone, it draws the high one: is that the tune you
  would sing along to? Default: yes. — `docs/JUDGE-A-RECORDING.md` § S10 · deadline 2026-11-30
- [songs] LiteRT on the GPU: never timed (13 Nov: 4 of 4 LiteRT rows failed, the APK lacked its .tflite). Asked again
  only on a build whose assets list carries it. Default: none; songs stay on the CPU (27.7 s per 30 s clip). — `docs/SONGS.md` § M2c · deadline 2026-11-30
- [harmony] The harmony AI: the app's line with the record's key, the trained model stays stored (end the model train)?
  Default: yes. — board `HL5-harmony-ai-question`, `docs/SONGS.md` § HL5.3 · deadline 2026-11-30
- [harmony] W7 on the phone: write an inner voice (write.inner) and tap Hear: are the chords under your line right, in tone
  and loudness? Default: step 5's chord sound as is. — `docs/STEP6.md` § W7 · deadline 2026-11-30
- [songs] Note editor after ED7 (T21 again, its new steps): touched notes locked, edge slides with the auto zoom, the cut
  under the finger, lyrics above the keyboard. Anything off? Default: as built. — board `ED7-gesture-map-built` · deadline 2026-11-30
- [songs] Words on your phone (T41): add a song again, open it (LRCLIB, else Genius → Paste), then Sing over it. Does
  each word sit on its note? Default: as built. — `docs/TESTING.md` T41 · deadline 2026-11-30
- [technique] T46 (Test bench → Device lab → "Your technique", ~12 min): 13 ways at 3 notes, then share the report? Default:
  the recogniser stays untested on your voice. — `docs/TESTING.md` T46, `docs/TECHNIQUE.md` § Part X2 · deadline 2026-11-30
- [chords] Billboard: on the Chromebook, re-judge MB1's rejected songs with K2a (YouTube refuses the cloud): `python3 pipeline/bench/billboard_check.py rejudge ~/bb-k3`. Default: they stay
  rejected (618 songs train). — `docs/CHORDS.md` § K3 · deadline 2026-11-30
- [chords] K3b (K2a fine-tuned with Billboard on Modal, the Beatles test decides): about $2.5 (at most $4); the bigger network only if earned,
  ~$3.5 more; int8 last. Go? Default: wait for your go. — `docs/prompts/K3b-train-with-billboard.md` · deadline 2026-11-30
- [harmony] Over a Mixolydian (or blues) drone, sing the ♭7 at 7:4 (the harmonic seventh, 31 c below the piano) or
  at 9:5 (18 c above the piano)? Hear both: `key.mixolydian.drone` (sessions: 7:4; the bench may ask 9:5). Default: 7:4. — `DroneSeventh`, step s3-mixolydian-seventh · deadline 2026-11-30
- [curriculum] Pad cards on his imported songs: build 4-beat pads only where the reader is sure (A: 94.1 % right, 22 of 44 songs
  give a card, on clean renders), or keep the hold (B)? Default: B. — board `PA2-pads-on-songs` · deadline 2026-11-30
- [curriculum] The 70 write cards count at the gate: at 12 min a day the simulated average singer reaches part 3 later or not by day 60.
  Keep them at the gate, or leave them out (as name cards are capped)? Default: keep. — `docs/STEP6.md` W6, board T4-s1-write-gate · deadline 2026-11-30
- [curriculum] PA1's find-the-line rungs: grade each chord against the line (default) or any chord tone, a leap marked?
  And sung only in spare time (nothing held back; none at 12 min/day) or like any card from turn 3? Default: as built. — board PA1-find-the-line · deadline 2026-11-30
- [backing] B9: which line should the song cards use, by ear on Photograph, Royals, Happy (store `features/b9/`): (a) 3rd
  below, (b) 6th below, (c) 3rd moved to the chord, (d) style writer, or none? Default: (c). — board B9-ear-three-lines · deadline 2026-11-30
