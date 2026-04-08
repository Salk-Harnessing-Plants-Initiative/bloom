import { redirect } from "next/navigation";
import { Navigation } from "@/components/navigation";
import {
  createServerSupabaseClient,
  getUser,
} from "@/lib/supabase/server";
import Link from "next/link";

export const metadata = {
  title: "Bloom",
  description: "Web app for Salk Harnessing Plants Initiative",
};

const navLinks = [
  {
    name: "Home",
    href: "/app",
  },
  // {
  //   name: "Accessions",
  //   href: "/app/accessions",
  // },
  // {
  //   name: "Genotypes",
  //   href: "/app/genotypes",
  // },
  {
    name: "Phenotypes",
    href: "/app/phenotypes",
  },
  // {
  //   name: "Pipelines",
  //   href: "/app/pipelines",
  // },
  {
    name: "Traits",
    href: "/app/traits",
  },
  {
    name: "Genes",
    href: "/app/genes",
  },
  // {
  //   name: "Genomes",
  //   href: "/app/jbrowse",
  // },
  {
    name: "Expression",
    href: "/app/expression",
  },
  // {
  //   name: "PyGCMS",
  //   href: "/app/pygcms",
  // },
  // {
  //   name: "Greenhouse",
  //   href: "/app/greenhouse",
  // },
  {
    name: "Timeline",
    href: "/app/timeline",
  },
  {
    name: "Translation",
    href: "/app/translation",
  },
  {
    name: "OrthoBrowser",
    href: "/app/orthofinder",
  },
  {
    name: "Software",
    href: "/app/software",
  },
  {
    name: "Bloom Assistant",
    href: "/chat",
  },
  // {
  //   name: "Settings",
  //   href: "/app/settings",
  // },
];

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
  } else {
    return (
      <main className="min-h-screen flex flex-col bg-slate-50">
        <div className="absolute left-12 top-8 align-middle">
          <Link href="/app">
            <img src="/logo.png" className="h-14 inline" alt="Bloom" />
          </Link>
        </div>
        <div className="absolute right-12 top-8">
          <div className="flex items-center text-sm gap-4">
            <span className="text-neutral-500">{user.email}</span>
            <span className="text-neutral-300">|</span>
            <form action={signOut}>
              <button className="text-neutral-500 hover:text-neutral-800 transition-colors">
                Logout
              </button>
            </form>
          </div>
        </div>
        <div className="mt-20 flex flex-col">
          <div className="flex flex-row">
            <div className="ml-12 mt-6 w-40 select-none">
              <Navigation navLinks={navLinks} />
            </div>
            <div className="mt-6 ml-6 flex-grow pb-8">{children}</div>
          </div>
        </div>
      </main>
    );
  }
}
