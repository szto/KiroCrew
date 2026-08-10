import { describe, it, expect } from 'vitest'
import { parseTags } from '../pages/knowledge/DetailView'

/**
 * The knowledge API serves `tags` as a JSON-encoded array string, because the
 * store round-trips the column through json.dumps. The reader used to only
 * comma-split, so `'["content_type:markdown"]'` produced one element still
 * wrapped in its brackets and quotes — the markdown marker never matched and
 * every folder-ingested document rendered as raw source instead of prose.
 */
describe('parseTags', () => {
  it('parses the JSON array string the API actually sends', () => {
    expect(parseTags('["content_type:markdown"]')).toEqual(['content_type:markdown'])
  })

  it('parses a multi-tag JSON array', () => {
    expect(parseTags('["content_type:markdown", "lang:ko"]')).toEqual([
      'content_type:markdown',
      'lang:ko',
    ])
  })

  it('accepts an already-decoded array', () => {
    expect(parseTags(['content_type:markdown'])).toEqual(['content_type:markdown'])
  })

  it('falls back to the comma form for a hand-written string', () => {
    expect(parseTags('content_type:markdown, lang:ko')).toEqual([
      'content_type:markdown',
      'lang:ko',
    ])
  })

  it('strips stray brackets and quotes from a partially-encoded value', () => {
    // Not valid JSON (unquoted member), so it takes the comma path and must
    // still yield a usable tag rather than one carrying its own punctuation.
    expect(parseTags('[content_type:markdown]')).toEqual(['content_type:markdown'])
  })

  it('returns an empty list for empty, null and non-string input', () => {
    expect(parseTags('')).toEqual([])
    expect(parseTags(null as unknown as string)).toEqual([])
    expect(parseTags(undefined)).toEqual([])
  })
})
