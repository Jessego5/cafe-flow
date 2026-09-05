# data/

Raw source material — course schedules, exports, photographs. Ignored by git
(see `.gitignore`); only this README is tracked.

Nothing here is read at runtime. These files are *inputs to* the parameters,
and the parameters are what gets committed:

    data/morgridge_hall_fall2026_classes.xlsx
        -> arrivals.class_blocks in params/observed.yaml, source: observed

That keeps the repo reproducible without redistributing institutional data,
and keeps the provenance honest: a block marked `observed` can name the file
it came from even when the file is not in the tree.

Contrast `observations/`, which *is* tracked — those are notes taken by hand
and short enough to read in a diff.
