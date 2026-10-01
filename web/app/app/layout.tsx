import { redirect } from "next/navigation";
import { Navigation } from "@/components/navigation";
import { navSections } from "@/components/nav-sections";
import {
  createServerSupabaseClient,
  getUser,
} from "@/lib/supabase/server";

export const metadata = {
  title: "Bloom",
  description: "Web app for Salk Harnessing Plants Initiative",
};

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const user = await getUser();

  const signOut = async () => {
    "use server";
    const supabase = await createServerSupabaseClient();
    await supabase.auth.signOut();
    redirect("/login");
  };

  if (!user) {
    redirect("/login");
  }

  return (
    <main className="min-h-screen flex bg-stone-100">
      <aside className="w-60 shrink-0 border-r border-stone-200 bg-stone-100 px-6 py-8">
        <Navigation sections={navSections} />
      </aside>

      <div className="flex-1 flex flex-col min-w-0">
        <header className="flex items-center justify-end gap-4 px-12 py-6 text-sm text-stone-700">
          <span>{user.email}</span>
          <span className="text-stone-300">·</span>
          <form action={signOut}>
            <button className="hover:underline">Logout</button>
          </form>
        </header>
        <div className="flex-1 px-12 pb-12">{children}</div>
      </div>
    </main>
  );
}
