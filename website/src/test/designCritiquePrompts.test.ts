import { describe, it, expect } from 'vitest'
import {
  CRITIC_FOR_TEST,
  DISCOVER_PROMPT,
  SCOPED_PROMPT,
  IMAGES_PROMPT,
} from '../apps/design-critique/prompts'
import type { DiscoveryScreen } from '../apps/design-critique/types'

/**
 * Design Critique used to open every run by telling the agent to shell out to
 * `python3 -c "import kiro_crew, ..."` to find its own scripts and the uploads
 * dir. security.py denies that shape on purpose: inline Python that imports the
 * package reaches the CLI, and `python -c "from kiro_crew.cli import main;
 * main()" token` mints a dashboard token. So the first tool call of every run
 * was blocked, and the agent burned turns hunting for a way around it — once by
 * re-issuing the identical command through an MCP shell tool.
 *
 * The gate is right. These pin that the prompts stop asking for it.
 */

const PICKS: DiscoveryScreen[] = [
  { id: 's1', label: 'Cart', ref: '/cart', group: '', canSee: true, why: '' },
]

const ALL_PROMPTS = (uploadsDir = ''): string[] => [
  CRITIC_FOR_TEST,
  DISCOVER_PROMPT('url', 'https://example.com', uploadsDir),
  DISCOVER_PROMPT('repo', 'https://github.com/o/r', uploadsDir),
  DISCOVER_PROMPT('local', '/tmp/app', uploadsDir),
  DISCOVER_PROMPT('figma', 'file.fig', uploadsDir),
  SCOPED_PROMPT(PICKS, 'brief', uploadsDir),
  IMAGES_PROMPT(['/up/a.png']),
]

describe('design-critique prompts never ask for a denied command', () => {
  it.each([
    ['inline python importing the package', /python3?\s+-c[^\n]*kiro_crew/],
    ['any inline python at all', /python3?\s+-c\s/],
    ['the package import as a literal', /import\s+kiro_crew/],
    ['the config_dir resolution', /from\s+kiro_crew\.config\.paths/],
  ])('contains no %s', (_label, pattern) => {
    for (const prompt of ALL_PROMPTS('/home/u/.kiro/crew/uploads')) {
      expect(prompt).not.toMatch(pattern)
    }
    for (const prompt of ALL_PROMPTS()) {
      expect(prompt).not.toMatch(pattern)
    }
  })
})

describe('the two directories still reach the agent', () => {
  it('names the loaded skill for <SCRIPTS> rather than a path to compute', () => {
    const p = DISCOVER_PROMPT('url', 'https://example.com', '')
    expect(p).toMatch(/<SCRIPTS>/)
    expect(p).toMatch(/SKILL\.md was read from/)
  })

  it('substitutes a real uploads dir when the app knows one', () => {
    const p = SCOPED_PROMPT(PICKS, undefined, '/home/u/.kiro/crew/uploads')
    expect(p).toContain('<UPLOADS> = /home/u/.kiro/crew/uploads')
  })

  it('falls back to the working directory when nothing was uploaded', () => {
    // URL and repo runs have no upload, and the session work dir is already
    // per-run isolated — so this must not be left dangling or hardcoded.
    const p = SCOPED_PROMPT(PICKS, undefined, '')
    expect(p).toContain('<UPLOADS> = your current working directory')
    expect(p).not.toContain('.kiro/crew')
  })

  it('still tells the critic to load the checklist first', () => {
    // The blocked command was load-bearing: it was how the method got read.
    // Removing it must not remove the instruction to read the method.
    expect(CRITIC_FOR_TEST).toMatch(/SKILL\.md/)
    expect(CRITIC_FOR_TEST).toMatch(/main-checklist\.md/)
  })
})
