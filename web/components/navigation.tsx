'use client'

import { usePathname } from 'next/navigation'
import Link from 'next/link'

type NavItem = { name: string; href: string }
type NavSection = { heading: string | null; items: NavItem[] }

export function Navigation({ sections }: { sections: NavSection[] }) {
  const pathname = usePathname()

  const isActive = (href: string) => {
    if (href === '/app') return pathname === '/app' || pathname === '/app/'
    return pathname.startsWith(href)
  }

  return (
    <nav className="select-none text-stone-700">
      <Link
        href="/app"
        className="flex items-center gap-2 mb-8 px-2 hover:opacity-80 transition-opacity"
      >
        <img src="/logo-mark.png" alt="" className="h-14 w-14 object-contain" />
        <span className="text-3xl font-serif italic font-semibold text-stone-900">
          Bloom
        </span>
      </Link>

      {sections.map((section) => (
        <div key={section.heading ?? '_root'} className="mb-6">
          {section.heading ? (
            <div className="px-4 mb-2 text-xs uppercase tracking-widest text-stone-500">
              {section.heading}
            </div>
          ) : null}
          <ul>
            {section.items.map((item) => {
              const active = isActive(item.href)
              return (
                <li key={item.name}>
                  <Link
                    href={item.href}
                    className={[
                      'flex items-center gap-3 px-4 py-2 rounded-md transition-colors',
                      active
                        ? 'bg-stone-50 text-lime-700 font-medium'
                        : 'text-stone-700 hover:bg-stone-50/70 hover:text-stone-900',
                    ].join(' ')}
                  >
                    <span
                      className={[
                        'inline-block h-1.5 w-1.5 rounded-full',
                        active ? 'bg-lime-700' : 'bg-stone-400',
                      ].join(' ')}
                      aria-hidden
                    />
                    <span>{item.name}</span>
                  </Link>
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </nav>
  )
}
