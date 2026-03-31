
import { createServerSupabaseClient } from '@/lib/supabase/server'
import { revalidatePath } from 'next/cache'

export default async function ServerAction() {
  const addTodo = async (formData: FormData) => {
    'use server'
    const title = formData.get('title')?.toString().trim()

    if (title) {
      const supabase = await createServerSupabaseClient()

      // https://github.com/vercel/next.js/blob/canary/examples/with-supabase/README.md
      await (supabase as any).from('todos').insert({ title })
      revalidatePath('/server-action-example')
    }
  }

  return (
    <form action={addTodo}>
      <input name="title" />
    </form>
  )
}
