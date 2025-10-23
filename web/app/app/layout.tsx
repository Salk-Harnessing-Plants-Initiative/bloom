import { redirect } from "next/navigation";
import { Navigation } from "@/components/navigation";
import {
  createServerActionSupabaseClient,
  getUser,
} from "@salk-hpi/bloom-nextjs-auth";
import Link from "next/link";

export const metadata = {
  title: "Bloom",
  description: "Web app for Salk Harnessing Plants Initiative",
};

const navLinks = [
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
  {
    name: "Genomes",
    href: "/app/jbrowse",
  },
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
    name: "Software",
    href: "/app/software",
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
    const supabase = createServerActionSupabaseClient();
    await supabase.auth.signOut();
    redirect("/login");
  };

  if (!user) {
    redirect("/login");
  } else {
    return (
      <main className="min-h-screen flex flex-col bg-stone-100">
        <div className="absolute left-12 top-8 align-middle">
          <Link href="/">
            <img src="/logo.png" className="h-12 inline" />
          </Link>
        </div>
        <div className="absolute right-12 top-8">
          <div className="flex text-sm">
            <span className="ml-auto">
              <span className="flex gap-4">
                {user.email} <span className="border-r"></span>{" "}
                <form action={signOut}>
                  <button className="hover:underline">Logout</button>
                </form>
              </span>
            </span>
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
