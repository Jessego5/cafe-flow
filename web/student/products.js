// Product pictures, resolved at build time.
//
// The directory is globbed rather than listed, so the art and the code never
// drift: a file whose name matches a menu key is that item's picture, an item
// with no file keeps the drawn mark, and neither case needs anything edited
// here. Vite hashes and copies whatever it finds.

const files = import.meta.glob('./img/*.png', { eager: true, query: '?url', import: 'default' })

const byKey = Object.fromEntries(
  Object.entries(files).map(([path, url]) => [path.split('/').pop().replace(/\.png$/, ''), url]),
)

// `latte_iced.png` wins for the iced build when it exists; otherwise the item's
// own picture stands in for every variant.
export function productImage(name, variant = null) {
  return (variant && byKey[`${name}_${variant}`]) || byKey[name] || null
}

export const artCount = Object.keys(byKey).length
