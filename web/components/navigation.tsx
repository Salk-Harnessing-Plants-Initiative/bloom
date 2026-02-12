'use client'

import { usePathname } from 'next/navigation'
import Link from 'next/link'
import '@/styles/globals.css'

export function Navigation({ navLinks }: { navLinks: { name: string; href: string }[] }) {
  const pathname = usePathname()

  return (
    <div className="w-36 -mt-3">
      {navLinks.map((link, index) => {
        const isActive = pathname.startsWith(link.href)
        const baseClasses = 'border-l-4 py-1 my-2 px-4 text-lime-700 hover:underline'
        const classes =
          baseClasses + ' ' + (isActive ? 'border-stone-300 font-bold' : 'border-stone-100')
        return (
          <div key={link.name}>
            <Link href={link.href}>
              <div className={classes}>{link.name}</div>
            </Link>
          </div>
        )
      })}
    </div>
  )
}
