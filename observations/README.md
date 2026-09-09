# What was observed, and how

Everything counted in this cafe is in `params/observed.yaml`, marked `observed`,
with the arithmetic next to it. This is the record of where those numbers came
from: what instrument produced each one, what it settled, and what it overturned.

The files here are the counter's own notation, pasted in unedited rather than
retyped or tidied.

What got counted was chosen by sensitivity rather than by convenience. The
ranking was written down before anyone went, so the model had committed to a
prediction the counting could contradict. It did, twice, and that is what makes
the two headline results below findings rather than a story assembled later.

### The instruments

* A tap counter and a stopwatch on a phone. One tap per completed order, per
  iced cup, per order with food, per person who left. The counter pauses, so a
  session is recorded minutes rather than elapsed minutes.
* `python -m analysis.calibrate observations/queue-log.txt` turns a pasted
  summary into `params/observed.yaml`. It exits non-zero if the calibrated model
  cannot reproduce what was seen, because the answer to that is to fix the model
  rather than to tune the fit.
* Photographs of the two menu boards and of the espresso machine.

## 1. The timed queue log

`queue-log.txt`. Line depth read on a timer, roughly every two minutes: 49
readings between 10:20 and 15:44. Every reading carries its clock time, so it
can be compared against the same time of day in the model rather than against
whenever the model happens to be busiest. Balks were recorded as the depth at
the moment somebody left, which is a different quantity from the depth on a
timer and identifies a different parameter.

It settled the hours, the crew by band (from the peak count plus a shift change
noted at 13:00), that the till person never makes drinks, what heats the food,
and the shape of the queue. The mean of 3.7 people is what `capture_rate` is
fitted against; `queue_by_bin.json` bins the same readings into half hours as
the target for the arrival-curve fits.

**What it overturned.** The model was losing about a third of peak demand to
balking, and the revenue case rested on recovering it. One person left, at a
line of eleven. People wait.

The log also records the counter being left running well past the last real
reading, and says so in its own notes. That belongs in the file rather than
being cleaned out of it.

## 2. Seventy till laps

`till-laps.txt`. One stopwatch lap per customer leaving the register, with the
item count and whether the order carried food. Nothing else, seventy times.

Fitted as `base + per_item x items + food x is_food`:

    base_s        17.3  +/- 3.6
    per_item_s     8.5  +/- 3.1
    food_s        13.2  +/- 3.2      R^2 0.84, residual sd 4.5s

The food term is there because leaving it out makes the item count absorb both
effects and returns 17.8s an item. Food nearly always comes with a drink, so the
two are collinear, and separating them recovers a thirteen-second premium on
ringing up a sandwich that nothing in the model had a place for.

**What it overturned.** The published figure was `40 + 3n`: a large fixed cost
with almost no sensitivity to basket size. The till is the other way round, and
it is the constraint. The same seventy laps also gave the basket shape, 60%
drink only against 33% food with a drink and 7% food alone, replacing a model
that generated a quarter of every day's orders as a sandwich with no coffee.

## 3. Twenty minutes in the morning

`morning-order-count.txt`. One mark per completed order, 09:15 to 09:35, twenty
orders. The calendar date was deliberately not recorded, only the weekday, which
is what the model takes.

This is the strongest result in the project, and it is the method that makes it
so rather than the count. Nothing had ever been fitted to the morning: the queue
log starts at 10:20, and the arrival curve built from the registrar's room
schedule was fitted only against the midday queue. So the hybrid model's 20.1
orders was a prediction out of sample. The free-rate fit it replaced predicted
7.3 and never once reached twenty in twenty simulated days, which excludes it
rather than merely disfavouring it.

A shelf tally of eight orders was taken alongside. Eight cannot pin a share, and
the file says so, but it settled structure rather than level: food came with a
drink both times it appeared.

## 4. One spot count

`spot-count.txt`. A single eyeball reading, about twenty people in line at 12:23.

Two readings is not a distribution. What makes this one worth keeping is that
the timed log had 6 in line at 12:21 and 2 at 12:27. Six against twenty at the
same minute of the clock is enough to say the logged readings were not typical,
and the model tops out near fifteen.

## 5. The menu boards

Photographed, then read off: item names, prices, calories at the medium size,
and which drinks the board offers hot or iced where it says.

## 6. Cup material at the handoff shelf

The hot/iced split is the one number on the boards that the boards cannot give
you, and it decides how much of the menu milk batching can reach at all. It does
not need a tally to read, because the cafe sorts it for you: a hot drink goes
out in paper and a cold one in plastic. Standing at the handoff shelf, the split
is legible at a glance without hearing a single order.

Read that way it is roughly 20/80 in warm weather, which is what `mix.serve`
carries. That is a look rather than a count, so it stays `assumed`, and
`params/season/*.yaml` interpolates it across the year from Madison's climate
normals with both anchors assumed too. The method is the useful part: two
tallies of paper against plastic, one in warm weather and one in cold, would
replace the whole construction with measured points, and they are cheaper to
collect than anything else still open.

## 7. Photographs

The espresso machine carries a Schaerer logo, twin hoppers and a touchscreen,
with no portafilters visible in any frame, which is what
`params/experiments/superauto.yaml` costs out. Running it says the answer does
not depend on which machine it is: either reading leaves the register the
constraint, with the same orders and the same margin.
