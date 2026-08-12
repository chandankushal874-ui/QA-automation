import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { useAuth } from "@/hooks/useAuth";
import { Logo } from "@/ui/logo";
import { buttonVariants } from "@/ui/button-util";
import { cn } from "@/utils/misc";
import { Route as DashboardRoute } from "@/routes/_app/_auth/dashboard/_layout.index";
import { Route as LoginRoute } from "@/routes/_app/login/_layout.index";
import { Bug, Globe, Zap, Shield, Code, ArrowRight, BookOpen } from "lucide-react";

export const Route = createFileRoute("/")({
  component: LandingPage,
});

const features = [
  {
    icon: <Globe className="h-6 w-6" />,
    title: "Cross-Browser Testing",
    description:
      "Automatically test your web app across Chromium, Firefox, and WebKit in parallel.",
  },
  {
    icon: <Bug className="h-6 w-6" />,
    title: "Automatic Bug Detection",
    description:
      "Detect JS errors, CORS issues, CSP violations, network failures, and visual regressions.",
  },
  {
    icon: <Zap className="h-6 w-6" />,
    title: "AI-Powered Fixes",
    description:
      "Get AI-generated code patches with confidence scores, verified against your source code.",
  },
  {
    icon: <Shield className="h-6 w-6" />,
    title: "Auth & SPA Support",
    description:
      "Handles login flows, SPA state transitions, and destructive action gating automatically.",
  },
  {
    icon: <Code className="h-6 w-6" />,
    title: "Endpoint Discovery",
    description:
      "Mines HTML, JS bundles, GraphQL schemas, and OpenAPI specs to build a complete API map.",
  },
];

export default function LandingPage() {
  const { isAuthenticated, isLoading } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (!isLoading && isAuthenticated) {
      navigate({ to: DashboardRoute.fullPath });
    }
  }, [isLoading, isAuthenticated]);

  return (
    <div className="flex min-h-screen flex-col bg-black text-white">
      {/* Nav */}
      <nav className="sticky top-0 z-50 flex w-full items-center justify-between border-b border-white/10 bg-black/80 backdrop-blur-xl px-6 py-4">
        <Link to="/" className="flex items-center gap-2">
          <Logo width={32} height={32} />
          <span className="text-lg font-semibold">CrossBrowserTest</span>
        </Link>
        <div className="flex items-center gap-3">
          <Link
            to="/docs"
            className={cn(
              buttonVariants({ variant: "ghost", size: "sm" }),
              "text-white/70 hover:text-white"
            )}
          >
            <BookOpen className="h-4 w-4" />
            Docs
          </Link>
          <Link
            to={LoginRoute.fullPath}
            className={cn(
              buttonVariants({ variant: "ghost", size: "sm" }),
              "text-white/70 hover:text-white"
            )}
          >
            Sign In
          </Link>
          <Link
            to={LoginRoute.fullPath}
            className={cn(
              buttonVariants({ variant: "default", size: "sm" }),
              "gap-1.5"
            )}
          >
            Get Started
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </nav>

      {/* Hero */}
      <section className="relative flex flex-col items-center justify-center px-6 py-32 text-center">
        <div className="base-grid absolute inset-0 opacity-20" />
        <div className="absolute inset-0 bg-gradient-to-b from-purple-500/10 via-transparent to-transparent" />
        <div className="relative z-10 flex max-w-3xl flex-col items-center gap-6">
          <div className="flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-4 py-1.5 text-sm text-white/60">
            <Zap className="h-4 w-4 text-yellow-500" />
            Autonomous Cross-Browser Testing Platform
          </div>
          <h1 className="text-5xl font-bold tracking-tight sm:text-6xl">
            Find bugs before your users do
          </h1>
          <p className="max-w-xl text-lg text-white/60">
            Paste a URL. We crawl your entire web app across Chromium, Firefox,
            and WebKit, triage every error, and generate AI-powered fixes —
            all in one click.
          </p>
          <div className="flex items-center gap-4">
            <Link
              to={LoginRoute.fullPath}
              className={cn(
                buttonVariants({ variant: "default", size: "lg" }),
                "gap-2 text-base"
              )}
            >
              Start Testing Free
              <ArrowRight className="h-5 w-5" />
            </Link>
            <a
              href="#features"
              className={cn(
                buttonVariants({ variant: "outline", size: "lg" }),
                "gap-2 text-base bg-transparent border-white/20 text-white/70 hover:text-white"
              )}
            >
              Learn More
            </a>
            <Link
              to="/docs"
              className={cn(
                buttonVariants({ variant: "outline", size: "lg" }),
                "gap-2 text-base bg-transparent border-white/20 text-white/70 hover:text-white"
              )}
            >
              <BookOpen className="h-5 w-5" />
              Read the Docs
            </Link>
          </div>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="px-6 py-24">
        <div className="mx-auto max-w-6xl">
          <div className="mb-12 text-center">
            <h2 className="text-3xl font-bold">Everything you need to ship with confidence</h2>
            <p className="mt-3 text-white/50">
              One platform to test, triage, and fix cross-browser issues.
            </p>
          </div>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {features.map((f) => (
              <div
                key={f.title}
                className="flex flex-col gap-3 rounded-xl border border-white/10 bg-white/5 p-6 hover:border-white/20 transition-colors"
              >
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-white/10 text-white/80">
                  {f.icon}
                </div>
                <h3 className="text-lg font-semibold">{f.title}</h3>
                <p className="text-sm text-white/50">{f.description}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="px-6 py-24">
        <div className="mx-auto max-w-2xl rounded-2xl border border-white/10 bg-white/5 p-12 text-center">
          <h2 className="text-3xl font-bold">Ready to find your bugs?</h2>
          <p className="mt-3 text-white/50">
            Start a free cross-browser test in under 30 seconds.
          </p>
          <Link
            to={LoginRoute.fullPath}
            className={cn(
              buttonVariants({ variant: "default", size: "lg" }),
              "mt-6 gap-2"
            )}
          >
            Get Started Free
            <ArrowRight className="h-5 w-5" />
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-white/10 px-6 py-8 text-center text-sm text-white/40">
        <div className="flex flex-col items-center gap-3">
          <Link
            to="/docs"
            className="inline-flex items-center gap-1.5 text-white/60 hover:text-white"
          >
            <BookOpen className="h-4 w-4" />
            User Guide &amp; Docs
          </Link>
          <span>CrossBrowserTest — Autonomous Cross-Browser Testing Platform</span>
        </div>
      </footer>
    </div>
  );
}
