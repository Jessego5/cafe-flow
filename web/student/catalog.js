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

// Blurbs describe the real build. Where a drink's plan is two stations, the
// blurb names both, in the order the bar works them.
const BLURB = {
  drip_coffee: 'Off the urn, poured to order. The fastest cup on the board.',
  espresso: 'One shot, pulled to a 27-second standard. Nothing else in the way.',
  americano: 'Two shots, lengthened with hot water.',
  cappuccino: 'Two shots under eight ounces of steamed milk. Hot only.',
  latte: 'Two shots under twelve ounces of steamed milk, or over ice.',
  vanilla_latte: 'The latte, with vanilla stirred through the build.',
  caramel_macchiato: 'Milk first, then the shots, then caramel across the top.',
  mocha: 'Two shots, chocolate, steamed milk. Hot or over ice.',
  white_chocolate_mocha: 'White chocolate in place of dark, same build.',
  cold_brew: 'Steeped cold and poured from the tap. No heat anywhere in it.',
  cold_brew_oat_latte: 'Cold brew from the tap, oat milk over ice. Iced by definition.',
  black_tie: 'Cold brew, chicory, condensed milk and half & half.',
  brewed_tea: 'Leaf tea, brewed hot to order.',
  iced_tea: 'Brewed strong, poured long over ice.',
  matcha_latte: 'Whisked matcha under steamed milk. The board does not ice it.',
  chai_latte: 'Spiced chai concentrate under steamed milk.',
  sparkling_grapefruit_cold_brew: 'Cold brew, grapefruit and soda, built at the cold bar.',
  sparkling_passion_fruit_black_tea: 'Black tea and passion fruit, finished with soda.',
  sparkling_lemonade: 'Lemon and soda over ice. No coffee, no tea.',
  cocoa: 'Steamed milk and chocolate. The one drink that skips the espresso bar.',
  bacon_egg_cheese_bagel: 'Pressed on the grill until the cheese gives.',
  sausage_egg_cheese: 'Sausage patty, egg and cheese, pressed hot.',
  vegan_sausage_egg_cheese: 'Plant-based patty, egg and cheese, pressed hot.',
  italian_herb_chicken: 'Herbed chicken pressed on the panini grill.',
  mona_lisa: 'The house press: four minutes on the grill, worth the wait.',
  monterey_turkey: 'Turkey and Monterey jack, pressed.',
  caesar_salad: 'Built at the food counter, no grill time at all.',
  build_your_own_salad: 'Pick it out at the counter and it is assembled in front of you.',
  broccoli_cheese_soup: 'Ladled from the pot. Cup size.',
  chicken_dumpling_soup: 'Ladled from the pot. Cup size.',
}

export const blurb = (name) => BLURB[name] || ''

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

// What is new on the board and what has come back for the season. Curated,
// like a real board's chalk header.
const SEASONAL = ['sparkling_grapefruit_cold_brew', 'sparkling_passion_fruit_black_tea']
const RETURNING = ['cold_brew_oat_latte', 'black_tie']

export const FEATURE = {
  seasonal: SEASONAL[0],
  returning: RETURNING[0],
  eyebrow: 'In season',
  headline: ['Cold brew,', 'and soda'],
}

const has = (item, station) => (item.stations || []).includes(station)

// The left-hand rail. Order matters: this is the order a customer scrolls.
export const CATEGORIES = [
  {
    key: 'seasonal',
    label: 'Seasonal',
    tag: 'New',
    heading: 'On the board this season',
    note: 'Sparkling builds, made at the cold bar',
    members: [...SEASONAL, ...RETURNING],
  },
  {
    key: 'popular',
    label: 'Most ordered',
    heading: 'Most ordered',
    note: 'What the bar makes most of, not a guess about you',
    members: MOST_ORDERED,
  },
  {
    key: 'espresso',
    label: 'Espresso bar',
    heading: 'Espresso bar',
    note: 'Two group heads, one steam wand',
    match: (item) => has(item, 'group_head'),
  },
  {
    key: 'brew',
    label: 'Brew & cold brew',
    heading: 'Brew & cold brew',
    note: 'Urns and taps. The quickest things to hand you',
    match: (item) =>
      has(item, 'brew_tap') && !has(item, 'group_head') && !/tea/.test(item.name),
  },
  {
    key: 'tea',
    label: 'Tea & matcha',
    heading: 'Tea & matcha',
    note: 'Leaf, matcha and chai',
    match: (item) => /tea|matcha|chai/.test(item.name) && !has(item, 'group_head'),
  },
  {
    key: 'cold',
    label: 'Sparkling & cocoa',
    heading: 'Sparkling & cocoa',
    note: 'No coffee in these',
    match: (item) =>
      !has(item, 'group_head') &&
      !has(item, 'brew_tap') &&
      !has(item, 'panini_press') &&
      !has(item, 'food_counter') &&
      !/tea|matcha|chai/.test(item.name),
  },
  {
    key: 'breakfast',
    label: 'Breakfast',
    tag: 'Back',
    heading: 'Breakfast, pressed',
    note: 'The grill takes four minutes and holds two at a time',
    members: ['bacon_egg_cheese_bagel', 'sausage_egg_cheese', 'vegan_sausage_egg_cheese'],
  },
  {
    key: 'kitchen',
    label: 'Sandwiches & salads',
    heading: 'Sandwiches & salads',
    note: 'Pressed on the grill or built at the counter',
    match: (item) =>
      (has(item, 'panini_press') || has(item, 'food_counter')) &&
      !['bacon_egg_cheese_bagel', 'sausage_egg_cheese', 'vegan_sausage_egg_cheese'].includes(
        item.name,
      ),
  },
]

// Group the API's flat item list into the rail's sections. A curated section
// keeps its listed order; a matched one keeps the board's order.
export function sections(items) {
  const byName = new Map(items.map((item) => [item.name, item]))
  return CATEGORIES.map((category) => ({
    ...category,
    items: category.members
      ? category.members.map((name) => byName.get(name)).filter(Boolean)
      : items.filter(category.match),
  })).filter((category) => category.items.length > 0)
}
