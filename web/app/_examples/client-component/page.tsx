'use client'

import { createClientSupabaseClient } from '@/lib/supabase/client'
import { useEffect, useState } from 'react'

export default function ClientComponent() {
  const [todos, setTodos] = useState<any[]>([])

  const supabase = createClientSupabaseClient()

  useEffect(() => {
    const getTodos = async () => {
      // https://github.com/vercel/next.js/blob/canary/examples/with-supabase/README.md
      const { data } = await (supabase as any).from('denormalized_images').select().limit(1)
      if (data) {
        setTodos(data)
      }
    }

    getTodos()
  }, [supabase, setTodos])

  return <pre>{JSON.stringify(todos, null, 2)}</pre>
}
