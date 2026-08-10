import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AccountDropdown from '../components/AccountDropdown'

const accounts = vi.hoisted(() => vi.fn())

vi.mock('../api/client', () => ({ api: { accounts } }))

function twoProfiles() {
  return {
    provider: 'claude_code',
    active: 'work',
    accounts: [
      { name: 'work', logged_in: true },
      { name: 'personal', logged_in: false },
    ],
  }
}

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe('AccountDropdown', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    accounts.mockResolvedValue(twoProfiles())
  })

  it('lists every profile returned by the API', async () => {
    wrap(<AccountDropdown slot="s1" selected="" disabled={false} onSelect={() => {}} />)
    await userEvent.click(await screen.findByRole('button'))

    // Queried by role, not by text: the trigger also renders the active name, so a
    // bare findByText('work') matches two nodes.
    const rows = await screen.findAllByRole('menuitem')
    expect(rows.map(r => r.textContent)).toEqual(['work', 'personal'])
  })

  it('reports the selected account to its parent', async () => {
    const onSelect = vi.fn()
    wrap(<AccountDropdown slot="s1" selected="" disabled={false} onSelect={onSelect} />)
    await userEvent.click(await screen.findByRole('button'))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'work' }))

    expect(onSelect).toHaveBeenCalledWith('work')
  })

  it('does not open while disabled', async () => {
    wrap(<AccountDropdown slot="s1" selected="" disabled onSelect={() => {}} />)
    await userEvent.click(await screen.findByRole('button'))

    await waitFor(() => expect(screen.queryByRole('menuitem')).not.toBeInTheDocument())
  })

  it('marks a profile with no login as unavailable', async () => {
    wrap(<AccountDropdown slot="s1" selected="" disabled={false} onSelect={() => {}} />)
    await userEvent.click(await screen.findByRole('button'))

    const row = (await screen.findByText('personal')).closest('[role="menuitem"]')
    expect(row).toHaveAttribute('aria-disabled', 'true')
  })

  it('renders nothing on a provider without account profiles', async () => {
    accounts.mockResolvedValue({ provider: 'acp', active: 'default', accounts: [] })
    wrap(<AccountDropdown slot="s1" selected="" disabled={false} onSelect={() => {}} />)

    await waitFor(() => expect(accounts).toHaveBeenCalled())
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('shows the slot\'s own pick over the config-level active account', async () => {
    // /api/accounts only knows the config default; after a per-slot switch the
    // slots stream carries the pick, and it must win in the trigger.
    wrap(<AccountDropdown slot="s1" selected="personal" disabled={false} onSelect={() => {}} />)

    expect(await screen.findByRole('button')).toHaveTextContent('personal')
  })

  it('renders nothing when a single profile is all there is', async () => {
    // A picker with one choice is chrome, not a control.
    accounts.mockResolvedValue({
      provider: 'claude_code',
      active: 'default',
      accounts: [{ name: 'default', logged_in: true }],
    })
    wrap(<AccountDropdown slot="s1" selected="" disabled={false} onSelect={() => {}} />)

    await waitFor(() => expect(accounts).toHaveBeenCalled())
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
