---
name: measure-this
description: Use when the seller wants garment measurements read from photos where they laid a yardstick or ruler on the item, instead of typing the measurements by hand.
triggers:
  - "read the measurements"
  - "measure this"
  - "read the ruler"
---

# Measure This - Read Garment Measurements From Ruler Photos

## Purpose
Read pit to pit, length, waist, rise, inseam and the other flat measurements from photos in which the seller laid a yardstick on the garment, and hand them back as the `measurements` line that `list-this` takes (see the `measurements` parameter in `skills/list-this/SKILL.md`). A value is offered only when two independent reads of an enlarged crop agree within 1/4 inch. Everything else goes back to the seller to measure by hand.

Studio's bulk upload and Regenerate review windows run this skill from **Read from photos** (`vendoo_studio/services/measure_read.py`): the script cuts the crops, and the seller's photo model does steps 2-3 twice, independently, from the text of this file. The result is a suggestion for the seller to confirm. Never write a measurement into a listing or draft without the seller confirming it, and never put a reading's uncertainty into listing copy: an unsure measurement is left out and named to the seller instead.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| photos | array | Yes | The item's ruler photos (other photos are ignored). |
| garment | string | Yes | `top`, `pants` (also shorts) or `skirt`: which measurements to read. |

## Photo protocol
What the seller does, and what the skill assumes:

- **A rigid yardstick, flat on a flat-laid garment.** Soft tapes sag and curve; they are not supported.
- **Zero end on the far edge.** The yardstick's zero end sits on the garment edge the measurement starts from (one armpit, the shoulder at the collar, the waistband, the crotch seam, one side of the hem). That end may be out of frame; the skill trusts it was placed there.
- **The reading edge in frame and sharp.** The garment edge the measurement ends at must cross the yardstick's tick edge inside the photo, not under a hand, the other stick or the pointer.
- **One stick per measurement.** Two crossed sticks per photo is fine (pit to pit and length; waist and rise; inseam and leg opening).
- **Armpit clear.** For pit to pit, the sleeve must not hang over the stick at the armpit. A flutter or folded sleeve over the tick edge hides the corner, and the read is refused.
- **Crotch seam clear.** For rise, the seam where the legs meet must show at the stick's tick edge, not under the pointer, a fold or the other stick. This is the measurement most often refused.
- **Contrast.** A cream or tan garment is the colour of the yardstick and can stop the script finding it; see step 1.

If a measurement's reading edge cannot be seen, the answer for it is `cannot read`.

## Workflow

### 1. Cut crops
```bash
python3 skills/measure-this/scripts/ruler.py crop <photo> --out <dir>
```
For each yardstick it finds (A, B), it writes a straightened strip with tile numbers above it (`A-strip.jpg`), enlarged overlapping tiles along the stick (`A-01.jpg`, ...), and `overview.jpg`, the photo with each stick's letter on it. Tiles are cut so the numerals read upright and the ticks run along the top edge.

If a strip shows no yardstick (most often on a cream or tan garment), or the numerals are upside down, rerun with the stick's centre line in photo pixels (`--line x1,y1,x2,y2`) or `--flip A`. If that still fails, the photo's measurements are `cannot read`.

### 2. Name each stick's measurement
Open `overview.jpg` and the strips. Decide which measurement each stick carries:
- **top:** pit to pit is the stick across the chest under the sleeves; length runs from the shoulder down past the hem; sleeve runs along the sleeve.
- **pants:** waist runs along the top of the waistband and is the flat width, not the circumference (a 16 in flat waist is 32 in around); rise runs from the waistband down to the crotch seam; inseam runs down the inside leg to the hem; leg opening lies across the hem.
- **skirt:** waist along the waistband; hip across the widest point; length from waistband to hem.

### 3. Read the end point
In the strip, find where the measurement's ending garment edge crosses the stick (the side seam below the sleeve, the hem, the waistband corner, the crotch seam, the far side of the leg). Open the tile or two that contain it.

In the tile:
1. Find the inch numeral at or just before the edge. Read the inch's long line, not the numeral: on the seller's yardsticks it runs through the numeral, between the two digits of a two-digit number.
2. Count the 1/8 ticks from that inch line to the edge. The half inch has the second-longest tick, quarters the next.
3. Read where the edge itself meets the tick edge of the stick. If the edge is slanted or curved, read where it crosses the ticks, not where it meets the far side of the stick.
4. Report the value to the nearest 1/8 inch, or `cannot read` when the edge is hidden, blurred, outside every tile, or the numerals around it cannot be made out. A guess is worse than `cannot read`.

### 4. Read again, independently
Do steps 2-3 a second time without looking at the first read: a fresh agent or subagent given only the crops and the garment type. Then settle them:
```bash
python3 skills/measure-this/scripts/ruler.py settle reads.json
```
with `reads.json` as `{"garment": "top", "reads": {"pit_to_pit": [20.5, 20.625], "length": [27, "cannot read"]}}`. Two reads within 1/4 inch are accepted, averaged and rounded to the nearest 1/4 inch. Disagreement or a refusal leaves the measurement out and names it under `needs_seller`.

### 5. Report
Give the seller the `line` from `settle`, e.g. `- Measurements (top): Pit to pit: 20.5"; Length: 27"`, and list every `needs_seller` measurement with the reason (the two reads, or why it could not be read). Do not put measurements that were not accepted into the line.

## Accuracy
`scripts/eval.py` scores the skill against measurements the seller typed by hand. The eval photos stay outside the repo. Rerun it after changing the protocol, the crops or these instructions, and report the confidently-wrong share (accepted but more than 1/2 inch off) first.

Run of 2026-10-13: 21 tops from Studio with a ruler photo and typed pit to pit and length (42 measurements), two blind reads each, crops cut automatically.

| | Share |
|---|---|
| Accepted, within 1/4 in | 38/42 (90%) |
| Accepted, within 1/2 in | 38/42 (90%) |
| Refused (cannot read or reads disagree) | 3/42 (7%) |
| Confidently wrong (accepted, over 1/2 in off) | 1/42 (2%) |

The seller types to the nearest half inch, so 1/4 inch is as close as the truth itself goes. The one confidently-wrong case is a length the photo shows at about 27 3/4 in that was typed as 25; the photo was not re-measured, so it may be a typing slip rather than a misread. A shirt typed both as 22.5/27 and 21/24 in different conversations from the same photo was scored as 21/24: 22.5/27/9 is the example line in `skills/list-this/SKILL.md`. Refusals: a sleeve over the armpit (twice) and a cream tee whose stick was not found (fixed by `--line`). Pants were not scored (no typed truth yet); across 5 new pairs of pants the rise was refused 3 times.

Run of 2026-10-14, against the measurements in the seller's Vendoo descriptions: all 35 pants and 45 random tops among the 331 measured listings that have a two-stick photo (221 measurements, two blind reads each).

| | Tops (102) | Pants (119) |
|---|---|---|
| Accepted, within 1/4 in | 65 | 40 |
| Accepted, within 1/2 in | 69 | 41 |
| Refused | 32 | 77 |
| Confidently wrong | 1 | 1 |

Of the 112 accepted values, 110 are within 1/2 inch. The two misses: a waist read flat at 16 in that the listing gives as 32 in around (the same measurement), and a sweater length read at 24 1/4 in against a listed 25 in. Most refusals are measurements that were never photographed: 23 of the 80 listings had no yardstick across the garment in any photo the scan picked (tag and detail shots), and sleeves and rises are rarely shot. On listings with a real measurement photo, 70 of 80 top measurements and 42 of 71 pants measurements were accepted. Rise was accepted once in 17 tries: the crotch seam is almost always under a fold, the pointer or the stick. Inseam and leg opening were accepted 28 times, all within 1/2 inch.

Studio's **Read from photos** (2026-10-15), Claude Opus as the photo model, on 16 of those listings (42 measurements): 22 accepted, 21 within 1/4 inch; the other is the 16 in flat waist listed as 32 in around. The blind readers accepted 24 of the same 42. Studio reads each item in 6-96 seconds.

