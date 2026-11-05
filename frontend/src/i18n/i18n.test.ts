import { describe, expect, it } from 'vitest'
import el from './el.json'
import en from './en.json'

type Tree = { [key: string]: string | Tree }

function flatten(obj: Tree, prefix = ''): Record<string, string> {
  return Object.entries(obj).reduce<Record<string, string>>((acc, [key, value]) => {
    if (typeof value === 'string') acc[`${prefix}${key}`] = value
    else Object.assign(acc, flatten(value, `${prefix}${key}.`))
    return acc
  }, {})
}

const placeholders = (s: string) => (s.match(/{{\w+}}/g) ?? []).sort()

describe('i18n resources', () => {
  const enFlat = flatten(en as Tree)
  const elFlat = flatten(el as Tree)

  it('have the same keys in Greek and English', () => {
    expect(Object.keys(elFlat).sort()).toEqual(Object.keys(enFlat).sort())
  })

  it('keep interpolation placeholders aligned and leave no value empty', () => {
    for (const [key, value] of Object.entries(enFlat)) {
      expect(value.trim(), key).not.toBe('')
      expect(elFlat[key]?.trim(), key).not.toBe('')
      expect(placeholders(elFlat[key] ?? ''), key).toEqual(placeholders(value))
    }
  })
})
