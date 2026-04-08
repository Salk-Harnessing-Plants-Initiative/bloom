import '@/styles/globals.css'
import { DM_Sans } from 'next/font/google'

const dmSans = DM_Sans({ subsets: ['latin'], weight: ['400', '500', '600', '700'] })

export const metadata = {
  title: 'Bloom',
  description: 'Web app for Salk Harnessing Plants Initiative',
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={dmSans.className} suppressHydrationWarning>
        {children}
      </body>
    </html>
  )
}
