// How the menu reads on a phone.
//
// Presentation only. Every fact here is either derived from what `/menu`
// already returns (stations, variants, whether it takes milk) or is copy that
// describes the station plan in words — a latte's blurb says "two shots under
// steamed milk" because that is literally its plan in `params/base.yaml`. No
// nutrition, no calories, no claims the cafe has not made.

const TITLE_EXCEPTIONS = {
  bacon_egg_cheese_bagel: 'Bacon, Egg & Cheese Bagel',
  sausage_egg_cheese: 'Sausage, Egg & Cheese',
  vegan_sausage_egg_cheese: 'Vegan Sausage, Egg & Cheese',
  cold_brew_oat_latte: 'Cold Brew Oat Latte',
  sparkling_passion_fruit_black_tea: 'Sparkling Passion Fruit Black Tea',
  build_your_own_salad: 'Build Your Own Salad',
  black_tie: 'Black Tie',
  mona_lisa: 'Mona Lisa',
  cocoa: 'Hot Cocoa',
}

export function title(name) {
  if (TITLE_EXCEPTIONS[name]) return TITLE_EXCEPTIONS[name]
  return name
    .split('_')
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ')
}

export const MILK_LABEL = {
  two_percent: '2% Milk',
  whole: 'Whole',
  nonfat: 'Nonfat',
  oat: 'Oat',
  almond: 'Almond',
  soy: 'Soy',
}

export const milkLabel = (milk) => MILK_LABEL[milk] || title(milk)

// The board prices no alternative milk and quotes its calories against 2%, so
// 2% leads the list and everything else follows it alphabetically, the way the
// API sent them.
export function milkOrder(milks) {
  const first = milks.includes('two_percent') ? ['two_percent'] : []
  return [...first, ...milks.filter((milk) => !first.includes(milk))]
}

// What the boards themselves say, transcribed from the photographs. Where the
// cafe has written a description, it is the cafe's description that shows.
const BOARD = {
  espresso: 'A single shot.',
  black_tie: 'Cold brew sweetened with condensed milk, chicory syrup, half & half.',
  brewed_tea: 'Black, green, or herbal.',
  iced_tea: 'Black, green, or wild berry hibiscus.',
  italian_herb_chicken:
    'Chicken, prosciutto, arugula, provolone, sun-dried tomato and herb aioli on multi-grain bread.',
  mona_lisa: 'Mozzarella cheese, fresh basil, tomato, balsamic glaze & pesto on sourdough.',
  // one word of this line was not legible in the photograph and is left out
  // rather than guessed at
  monterey_turkey: 'Smoked turkey, marble monterey cheese, avocado and zesty sauce on sourdough bread.',
  caesar_salad:
    'Chopped romaine lettuce, parmesan cheese, croutons, creamy Caesar dressing and choice of protein.',
  build_your_own_salad:
    'Start with fresh greens, add up to 4 toppings, 1 premium topping, garnish & top it off with dressing.',
}

// For the rest the board gives a name and a price and nothing else, so these
// describe the build instead — each one is that item's station plan in words,
// and nothing is claimed here that `params/base.yaml` does not already say.
const DERIVED = {
  drip_coffee: 'Off the urn, poured to order. The fastest cup on the board.',
  americano: 'Two shots, lengthened with hot water.',
  cappuccino: 'Two shots under eight ounces of steamed milk. Hot only.',
  latte: 'Two shots under twelve ounces of steamed milk, or over ice.',
  vanilla_latte: 'The latte, with vanilla stirred through the build.',
  caramel_macchiato: 'Milk first, then the shots, then caramel across the top.',
  mocha: 'Two shots, chocolate, steamed milk. Hot or over ice.',
  white_chocolate_mocha: 'White chocolate in place of dark, same build.',
  cold_brew: 'Steeped cold and poured from the tap. No heat anywhere in it.',
  cold_brew_oat_latte: 'Cold brew from the tap, oat milk over ice.',
  matcha_latte: 'Whisked matcha under steamed milk. The board does not ice it.',
  chai_latte: 'Spiced chai concentrate under steamed milk.',
  sparkling_grapefruit_cold_brew: 'Cold brew, grapefruit and soda, built at the cold bar.',
  sparkling_passion_fruit_black_tea: 'Black tea and passion fruit, finished with soda.',
  sparkling_lemonade: 'Lemon and soda over ice. No coffee, no tea.',
  cocoa: 'Steamed milk and chocolate. The one drink that skips the espresso bar.',
  bacon_egg_cheese_bagel: 'Pressed on the grill until the cheese gives.',
  sausage_egg_cheese: 'Sausage patty, egg and cheese, pressed hot.',
  vegan_sausage_egg_cheese: 'Plant-based patty, egg and cheese, pressed hot.',
  broccoli_cheese_soup: 'Ladled from the pot.',
  chicken_dumpling_soup: 'Ladled from the pot.',
}

export const blurb = (name) => BOARD[name] || DERIVED[name] || ''

// Badges are read off the item the API sent, never guessed.
const STATION_BADGE = {
  group_head: 'Espresso',
  steam_wand: 'Steamed milk',
  brew_tap: 'From the tap',
  cold_bar: 'Built cold',
  panini_press: 'Pressed hot',
  food_counter: 'From the counter',
}

export function badges(item) {
  const out = []
  const variants = item.variants || []
  if (variants.includes('hot') && variants.includes('iced')) out.push('Hot or iced')
  else if (variants.length === 1) out.push(title(variants[0]))
  if (item.requires_milk) out.push('Choose your milk')
  for (const station of item.stations || []) {
    const badge = STATION_BADGE[station]
    if (badge && !out.includes(badge)) out.push(badge)
  }
  return out
}

// The four the bar makes most, by the drink shares in `params/base.yaml`
// (latte .130, drip .095, cold brew .065, the bagel .055). Not personalised —
// this app has no idea who you are, and says so rather than pretending.
const MOST_ORDERED = ['latte', 'drip_coffee', 'cold_brew', 'bacon_egg_cheese_bagel']

// The hero names the cafe, and takes that name from `/config` rather than
// holding a copy of it. Beneath it, who the beans come from, in the size the
// board gives it. Half of that line is the board's own wording — it reads
// "PROUDLY POURING Peet's Coffee" above the Peet's logo — and half is from the
// cafe rather than the wall: Ground Truth supplies it too, which no board we
// photographed says.
export const FEATURE = {
  pouring: "Proudly pouring Peet's and Ground Truth Coffee",
  items: MOST_ORDERED,
}

// "Morgridge Coffee" -> ["Morgridge", "Coffee"], for the two-line display face.
export function nameLines(name) {
  const words = (name || '').split(' ').filter(Boolean)
  if (words.length < 2) return [name || '', '']
  return [words.slice(0, -1).join(' '), words[words.length - 1]]
}

// The rail, as the boards divide it.
//
// `heading` is the board's own wording and `rail` is the short form the
// left-hand jump list uses, where a two-word label wraps and reads as two
// entries. Sections without a `rail` are short enough already.
//
// These are the cafe's sections, photographed 2026-09-03, in the cafe's own
// order and wording: two drink boards reading Coffee & Espresso, Cold Brew,
// Tea & Sparkling, Non-Coffee, then Breakfast Paninis, Lunch Paninis, Salads,
// Soups. An earlier version of this file grouped by station instead — Espresso
// bar, Brew & cold brew — which is how the drink is made rather than how it is
// sold, and it disagreed with the board in three places: drip coffee sits
// under Coffee & Espresso, the sparkling cold brew under Tea & Sparkling, and
// cocoa in a section of its own.
//
// Only `Most ordered` is the app's own, and it says so.
export const CATEGORIES = [
  {
    key: 'popular',
    label: 'Most ordered',
    rail: 'Popular',
    heading: 'Most ordered',
    note: 'What the bar makes most of, not a guess about you',
    members: MOST_ORDERED,
  },
  {
    key: 'coffee',
    label: 'Coffee & espresso',
    rail: 'Coffee',
    heading: 'Coffee & Espresso',
    note: 'Regular or decaf',
    members: [
      'drip_coffee', 'espresso', 'americano', 'cappuccino', 'latte',
      'vanilla_latte', 'caramel_macchiato', 'mocha', 'white_chocolate_mocha',
    ],
  },
  {
    key: 'cold_brew',
    label: 'Cold brew',
    heading: 'Cold Brew',
    members: ['cold_brew', 'cold_brew_oat_latte', 'black_tie'],
  },
  {
    key: 'tea',
    label: 'Tea & sparkling',
    rail: 'Tea',
    heading: 'Tea & Sparkling',
    members: [
      'brewed_tea', 'matcha_latte', 'chai_latte', 'iced_tea',
      'sparkling_grapefruit_cold_brew', 'sparkling_passion_fruit_black_tea',
      'sparkling_lemonade',
    ],
  },
  {
    key: 'non_coffee',
    label: 'Non-coffee',
    heading: 'Non-Coffee',
    members: ['cocoa'],
  },
  {
    key: 'breakfast',
    label: 'Breakfast paninis',
    rail: 'Breakfast',
    heading: 'Breakfast Paninis',
    members: ['bacon_egg_cheese_bagel', 'sausage_egg_cheese', 'vegan_sausage_egg_cheese'],
  },
  {
    key: 'lunch',
    label: 'Lunch paninis',
    rail: 'Lunch',
    heading: 'Lunch Paninis',
    members: ['italian_herb_chicken', 'mona_lisa', 'monterey_turkey'],
  },
  {
    key: 'salads',
    label: 'Salads',
    heading: 'Salads',
    note: 'Extra topping +$1.00 · extra premium +$1.75',
    members: ['caesar_salad', 'build_your_own_salad'],
  },
  {
    key: 'soups',
    label: 'Soups',
    heading: 'Soups',
    note: 'Cup priced here · bowl $4.39 · Vienna dinner roll +$1.00',
    members: ['broccoli_cheese_soup', 'chicken_dumpling_soup'],
  },
]

// Group the API's flat item list into the rail's sections, keeping the board's
// order. Anything the board does not list would go missing, so it is reported
// rather than dropped quietly.
export function sections(items) {
  const byName = new Map(items.map((item) => [item.name, item]))
  const placed = new Set()
  const groups = CATEGORIES.map((category) => {
    const found = category.members.map((name) => byName.get(name)).filter(Boolean)
    if (category.key !== 'popular') found.forEach((item) => placed.add(item.name))
    return { ...category, items: found }
  }).filter((category) => category.items.length > 0)

  const missing = items.filter((item) => !placed.has(item.name))
  if (missing.length > 0) {
    groups.push({
      key: 'other',
      label: 'Also on sale',
      heading: 'Also on sale',
      note: 'On the till but not on the boards we photographed',
      items: missing,
    })
  }
  return groups
}
