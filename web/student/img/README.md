Product art, one file per menu item, named for the item's key in `params/base.yaml`
so `latte.png`, `bacon_egg_cheese_bagel.png`, and so on. PNG with a transparent
background, square, 256px or larger.

Nothing imports these by name: `products.js` globs the directory at build time,
so an item with a file here shows its picture and an item without one falls back
to the drawn cup or plate in `marks.jsx`. Adding art is a file drop and a
rebuild, never a code change.

Variants get a suffix, `latte_iced.png`, and are used when that variant is
chosen; the unsuffixed file is the default.
