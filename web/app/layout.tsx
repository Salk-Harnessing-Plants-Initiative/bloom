import '@/styles/globals.css'
import { Analytics } from '@vercel/analytics/react'

export const metadata = {
  title: 'Bloom',
  description: 'Web app for Salk Harnessing Plants Initiative',
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
        <Analytics />
      </body>
    </html>
  )
}
