import '@/styles/globals.css'


export const metadata = {
  title: 'Bloom',
  description: 'Web app for Salk Harnessing Plants Initiative',
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
      </body>
    </html>
  )
}
