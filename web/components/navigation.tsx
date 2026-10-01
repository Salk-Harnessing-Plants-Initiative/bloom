'use client'

import { usePathname, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import type { NavItem, NavSection } from './nav-sections'

/** Whether a link is the current page; a link with `?panel=` also needs that panel open. */
export function isCurrent(href: string, pathname: string, panel: string | null, defaultPanel?: string) {
  const [path, query] = href.split('?')
  if (path === '/app') return pathname === '/app' || pathname === '/app/'
  if (!pathname.startsWith(path)) return false
  if (!query) return true
  const wanted = new URLSearchParams(query).get('panel')
  // A panel's own pages (e.g. /app/timeline/rnaseq/12) belong to that panel.
  const fromPath = pathname.slice(path.length).split('/')[1] || null
  return (panel ?? fromPath ?? defaultPanel ?? null) === wanted
}

function ItemLink({ item, active, small }: { item: NavItem; active: boolean; small?: boolean }) {
  return (
    <Link
      href={item.href}
      aria-current={active ? 'page' : undefined}
      className={[
        'flex items-center gap-3 rounded-md transition-colors',
        small ? 'px-4 py-1 text-sm' : 'px-4 py-2',
        active
          ? 'bg-stone-50 text-lime-700 font-medium'
          : 'text-stone-700 hover:bg-stone-50/70 hover:text-stone-900',
      ].join(' ')}
    >
      <span
        className={[
          'inline-block rounded-full',
          small ? 'h-1 w-1' : 'h-1.5 w-1.5',
          active ? 'bg-lime-700' : 'bg-stone-400',
        ].join(' ')}
        aria-hidden
      />
      <span className="whitespace-pre-line leading-tight">{item.name}</span>
    </Link>
  )
}

export function Navigation({ sections }: { sections: NavSection[] }) {
  const pathname = usePathname()
  const panel = useSearchParams().get('panel')

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
              // With sub-links, the item itself is highlighted only when no sub-link is.
              const children = item.children ?? []
              const firstPanel = children[0] ? new URLSearchParams(children[0].href.split('?')[1] ?? '').get('panel') ?? undefined : undefined
              const childActive = children.map((c) => isCurrent(c.href, pathname, panel, firstPanel))
              const active = isCurrent(item.href, pathname, panel) && !childActive.some(Boolean)
              return (
                <li key={item.name}>
                  <ItemLink item={item} active={active} />
                  {children.length ? (
                    <ul className="mb-1 ml-5 border-l border-stone-200 pl-1">
                      {children.map((child, i) => (
                        <li key={child.name}>
                          <ItemLink item={child} active={childActive[i]} small />
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </nav>
  )
}
