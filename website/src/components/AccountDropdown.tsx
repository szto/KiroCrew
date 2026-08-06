import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { UserRound, Check } from 'lucide-react'
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from './ui/dropdown-menu'
import { api } from '../api/client'

import { i18nT } from '../i18n/t'

// The only provider that keeps per-account state. Compared against the value
// /api/accounts reports rather than a frontend capability flag: the adapter
// registry is deliberately single-entry (ProviderId is 'acp'), so a capability
// gate could never turn true and the dropdown would be dead code.
const ACCOUNT_PROVIDER = 'claude_code'

interface Props {
  slot: string
  /** The slot's own pick ("" = none yet). Overrides the config-level active
   *  account in the display: /api/accounts only knows the config default, so
   *  without this the trigger would keep naming the old account after a switch. */
  selected: string
  /** True once the slot has a live session: the account is fixed at session
   *  start, so switching means starting a new session, not mutating this one. */
  disabled: boolean
  onSelect: (name: string) => void
}

/** Account picker for the claude_code provider. Reads the declared profiles from
 *  /api/accounts (names and login state only — the API never returns config dirs)
 *  and locks itself once the slot has a session.
 *
 *  Renders nothing on any other provider, and nothing when a single profile is all
 *  there is: a picker with one choice is chrome, not a control. */
export default function AccountDropdown({ slot, selected, disabled, onSelect }: Props) {
  const [open, setOpen] = useState(false)
  const { data } = useQuery({
    queryKey: ['accounts', slot],
    queryFn: () => api.accounts(),
    staleTime: 0,
  })

  const accounts = data?.accounts ?? []
  const active = selected || (data?.active ?? '')
  if (data?.provider !== ACCOUNT_PROVIDER || accounts.length < 2) return null

  return (
    <DropdownMenu open={open} onOpenChange={o => !disabled && setOpen(o)}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          className="h-7 px-2 rounded-lg text-[12px] text-muted hover:text-text hover:bg-bg-hover flex items-center gap-1 cursor-pointer transition-all bg-transparent border-none shrink-0 whitespace-nowrap disabled:cursor-not-allowed disabled:opacity-50"
          title={disabled
            ? i18nT('components.accountDropdown.locked_tooltip')
            : i18nT('components.accountDropdown.switch_tooltip')}
          aria-label={i18nT('components.accountDropdown.account_aria', { name: active })}
        >
          <UserRound className="lucide-inline shrink-0" />
          {active}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" collisionPadding={8} className="w-[220px]">
        {accounts.map(a => (
          <DropdownMenuItem
            key={a.name}
            disabled={!a.logged_in}
            title={a.logged_in ? '' : i18nT('components.accountDropdown.needs_login_tooltip')}
            onSelect={() => {
              onSelect(a.name)
              setOpen(false)
            }}
            className={a.name === active ? 'text-accent' : ''}
          >
            <span className="flex-1 min-w-0 truncate">{a.name}</span>
            {a.name === active && <Check className="lucide-inline shrink-0 text-accent" />}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
