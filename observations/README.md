# What to count, in priority order

Read off the menu boards on 2026-08-27, so already `observed`: item names,
prices, and which drinks the board offers hot or iced.

Service times are now `published` — conventional figures from industry and
vendor sources, cited at each parameter in `params/base.yaml`. They are a far
better starting point than a guess, but they describe a typical cafe, not this
one. What no published source can supply is this cafe's own demand: the mix,
the hot/iced split, the capture rate and the staffing plan.

The list below is ordered by how much the answer moves the result, not by how
easy it is to collect.

### 1. The hot/iced split

`mix.serve`. Currently assumed at 45/55. An iced latte never touches the steam
wand, so this fraction decides how much of the menu milk batching — the plan's
core throughput mechanism — can reach at all. If iced is dominant, batching is
worth little here no matter how well it is scheduled.

Count cups at the handoff shelf for one peak and one trough. Nothing else on
this list is worth collecting before this one.

### 2. Whether the register competes for barista time

`stations.register.capacity`. Currently `from_staffing`, which assumes whoever
takes the order also makes drinks. If there is a dedicated cashier, this becomes
a fixed capacity and register queueing largely disappears from the model.

Watch one rush and write down whether the person at the POS ever makes a drink.

### 3. How many steam wands and group heads

`stations.steam_wand.capacity`, `stations.group_head.capacity`. Assumed 1 and 2.
Two wands roughly halves the contention the model currently produces.

One photograph of the espresso machine settles both.

### 4. Panini press time and capacity

`stations.panini_press.run_s`, `.batch_size`. Now published: 4 minutes a
sandwich, one or two at a time, which is the 10-15 sandwiches an hour a single
14-inch grill is rated for. This station is the busiest thing in the cafe
during the peak hour on every configuration tried.

Worth confirming anyway, because it is the constraint and because the published
figure is for a generic grill: this cafe's press may be a different size, or a
rapid-cook oven, which would be several times faster.

### 5. Food share of orders

`mix.drink`, the ten food items. Assumed 26.5% of orders. Drives item 4.

### 6. Transact adoption

`customers.preorder_adoption`, currently 0. Order-ahead is already deployed
here, so this is a count, not a sweep: how many pickups come off the mobile
shelf versus the register during one peak.

### 7. Service times, mix, queue lengths and balks

The `observations/*.csv` contract in `campus-cafe-ordering-plan.md`, read by
`analysis/calibrate.py` at M8. Sample every 30s across at least one peak and one
trough. The balk count is the entire revenue case; collect it carefully.

### Still unread from the photographs

* Caesar salad price sat at the edge of the frame; recorded as `assumed`.
* Soup bowl prices were cropped; only the cup price is recorded.
* Whether matcha and chai are available iced. The board does not say, so they
  are modelled hot only.
* Opening hours. `07:00`–`15:00` is a guess.
