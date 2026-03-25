'use client'

import { usePathname } from 'next/navigation'
import Link from 'next/link'
import '@/styles/globals.css'

export function Navigation({ navLinks }: { navLinks: { name: string; href: string }[] }) {
  const pathname = usePathname()

  return (
    <div className="w-40 -mt-3">
      {navLinks.map((link) => {
        const isActive = link.href === '/app'
          ? pathname === '/app'
          : pathname.startsWith(link.href)
        return (
          <div key={link.name}>
            <Link href={link.href}>
              <div className={`py-2 my-0.5 px-4 rounded-lg transition-all duration-200 ease-in-out
                ${isActive
                  ? 'font-semibold text-lime-800 bg-lime-100/80 shadow-sm shadow-lime-200/50'
                  : 'text-lime-700 hover:bg-lime-50 hover:text-lime-800 hover:translate-x-0.5'
                }
              `}>
                {link.name}
              </div>
            </Link>
          </div>
        )
      })}
    </div>
  )
}
