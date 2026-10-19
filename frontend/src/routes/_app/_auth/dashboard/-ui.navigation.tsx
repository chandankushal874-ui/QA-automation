import {
  ChevronUp,
  ChevronDown,
  Slash,
  Check,
  Settings,
  LogOut,
  PlusCircle,
  History,
  LayoutDashboard,
  Users,
  Shield,
  ScrollText,
  BookOpen,
  Timer,
  Layers,
} from "lucide-react";
import { cn, useSignOut } from "@/utils/misc";
import { ThemeSwitcher } from "@/ui/theme-switcher";
import { LanguageSwitcher } from "@/ui/language-switcher";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
} from "@/ui/dropdown-menu";
import { Button } from "@/ui/button";
import { buttonVariants } from "@/ui/button-util";
import { Logo } from "@/ui/logo";
import { Link, useMatchRoute, useNavigate } from "@tanstack/react-router";
import { Route as DashboardRoute } from "@/routes/_app/_auth/dashboard/_layout.index";
import { Route as NewTestRoute } from "@/routes/_app/_auth/dashboard/_layout.new-test";
import { Route as HistoryRoute } from "@/routes/_app/_auth/dashboard/_layout.history";
import { Route as SettingsRoute } from "@/routes/_app/_auth/dashboard/_layout.settings.index";
import { User } from "~/types";

export function Navigation({ user }: { user: User }) {
  const signOut = useSignOut();
  const matchRoute = useMatchRoute();
  const navigate = useNavigate();
  const isDashboardPath = matchRoute({ to: DashboardRoute.fullPath });
  const isNewTestPath = matchRoute({ to: NewTestRoute.fullPath });
  const isHistoryPath = matchRoute({ to: HistoryRoute.fullPath });
  const isSettingsPath = matchRoute({ to: SettingsRoute.fullPath });
  const isUsersPath = matchRoute({ to: "/dashboard/users" });
  const isAuditPath = matchRoute({ to: "/dashboard/audit" });
  const isMonitorsPath = matchRoute({ to: "/dashboard/monitors" });
  const isProjectsPath = matchRoute({ to: "/dashboard/projects" });
  const isDocsPath = matchRoute({ to: "/docs" });
  const isAdmin = user?.role === "admin";

  if (!user) {
    return null;
  }

  return (
    <nav className="sticky top-0 z-50 flex w-full flex-col border-b border-border bg-card px-6">
      <div className="mx-auto flex w-full max-w-screen-xl items-center justify-between py-3">
        <div className="flex h-10 items-center gap-2">
          <Link
            to={DashboardRoute.fullPath}
            className="flex h-10 items-center gap-1"
          >
            <Logo />
          </Link>
          <Slash className="h-6 w-6 -rotate-12 stroke-[1.5px] text-primary/10" />
          <DropdownMenu modal={false}>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                className="gap-2 px-2 data-[state=open]:bg-primary/5"
              >
                <div className="flex items-center gap-2">
                  <span className="h-8 w-8 rounded-full bg-gradient-to-br from-lime-400 from-10% via-cyan-300 to-blue-500" />
                  <p className="text-sm font-medium text-primary/80">
                    {user?.username || ""}
                  </p>
                  {isAdmin ? (
                    <span className="inline-flex items-center gap-0.5 rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-600 dark:text-amber-400">
                      <Shield className="h-3 w-3" />
                      Admin
                    </span>
                  ) : (
                    <span className="rounded-full bg-sky-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sky-600 dark:text-sky-400">
                      User
                    </span>
                  )}
                </div>
                <span className="flex flex-col items-center justify-center">
                  <ChevronUp className="relative top-[3px] h-[14px] w-[14px] stroke-[1.5px] text-primary/60" />
                  <ChevronDown className="relative bottom-[3px] h-[14px] w-[14px] stroke-[1.5px] text-primary/60" />
                </span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              sideOffset={8}
              className="min-w-56 bg-card p-2"
            >
              <DropdownMenuLabel className="flex items-center text-xs font-normal text-primary/60">
                Personal Account
              </DropdownMenuLabel>
              <DropdownMenuItem className="h-10 w-full cursor-pointer justify-between rounded-md bg-secondary px-2">
                <div className="flex items-center gap-2">
                  <span className="h-6 w-6 rounded-full bg-gradient-to-br from-lime-400 from-10% via-cyan-300 to-blue-500" />
                  <p className="text-sm font-medium text-primary/80">
                    {user.username || ""}
                  </p>
                </div>
                <Check className="h-[18px] w-[18px] stroke-[1.5px] text-primary/60" />
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        <div className="flex h-10 items-center gap-3">
          <DropdownMenu modal={false}>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" className="h-8 w-8 rounded-full">
                <span className="min-h-8 min-w-8 rounded-full bg-gradient-to-br from-lime-400 from-10% via-cyan-300 to-blue-500" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              sideOffset={8}
              className="fixed -right-4 min-w-56 bg-card p-2"
            >
              <DropdownMenuItem className="group flex-col items-start focus:bg-transparent">
                <p className="text-sm font-medium text-primary/80 group-hover:text-primary group-focus:text-primary">
                  {user?.username || ""}
                </p>
                <p className="text-xs text-primary/50">
                  {user?.email || ""} · {user?.role || "user"}
                </p>
              </DropdownMenuItem>

              <DropdownMenuItem
                className="group h-9 w-full cursor-pointer justify-between rounded-md px-2"
                onClick={() => navigate({ to: SettingsRoute.fullPath })}
              >
                <span className="text-sm text-primary/60 group-hover:text-primary group-focus:text-primary">
                  Settings
                </span>
                <Settings className="h-[18px] w-[18px] stroke-[1.5px] text-primary/60 group-hover:text-primary group-focus:text-primary" />
              </DropdownMenuItem>

              <DropdownMenuItem
                className={cn(
                  "group flex h-9 justify-between rounded-md px-2 hover:bg-transparent",
                )}
              >
                <span className="w-full text-sm text-primary/60 group-hover:text-primary group-focus:text-primary">
                  Theme
                </span>
                <ThemeSwitcher />
              </DropdownMenuItem>

              <DropdownMenuItem
                className={cn(
                  "group flex h-9 justify-between rounded-md px-2 hover:bg-transparent",
                )}
              >
                <span className="w-full text-sm text-primary/60 group-hover:text-primary group-focus:text-primary">
                  Language
                </span>
                <LanguageSwitcher />
              </DropdownMenuItem>

              <DropdownMenuSeparator className="mx-0 my-2" />

              <DropdownMenuItem
                className="group h-9 w-full cursor-pointer justify-between rounded-md px-2"
                onClick={() => signOut()}
              >
                <span className="text-sm text-primary/60 group-hover:text-primary group-focus:text-primary">
                  Log Out
                </span>
                <LogOut className="h-[18px] w-[18px] stroke-[1.5px] text-primary/60 group-hover:text-primary group-focus:text-primary" />
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <div className="mx-auto flex w-full max-w-screen-xl items-center gap-3">
        <div
          className={cn(
            "flex h-12 items-center border-b-2",
            isDashboardPath ? "border-primary" : "border-transparent",
          )}
        >
          <Link
            to={DashboardRoute.fullPath}
            className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "text-primary/80 gap-1.5")}
          >
            <LayoutDashboard className="h-4 w-4" />
            Dashboard
          </Link>
        </div>
        <div
          className={cn(
            "flex h-12 items-center border-b-2",
            isNewTestPath ? "border-primary" : "border-transparent",
          )}
        >
          <Link
            to={NewTestRoute.fullPath}
            className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "text-primary/80 gap-1.5")}
          >
            <PlusCircle className="h-4 w-4" />
            New Test
          </Link>
        </div>
        <div
          className={cn(
            "flex h-12 items-center border-b-2",
            isHistoryPath ? "border-primary" : "border-transparent",
          )}
        >
          <Link
            to={HistoryRoute.fullPath}
            className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "text-primary/80 gap-1.5")}
          >
            <History className="h-4 w-4" />
            History
          </Link>
        </div>
        <div
          className={cn(
            "flex h-12 items-center border-b-2",
            isDocsPath ? "border-primary" : "border-transparent",
          )}
        >
          <Link
            to="/docs"
            className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "text-primary/80 gap-1.5")}
          >
            <BookOpen className="h-4 w-4" />
            Docs
          </Link>
        </div>
        <div
          className={cn(
            "flex h-12 items-center border-b-2",
            isSettingsPath ? "border-primary" : "border-transparent",
          )}
        >
          <Link
            to={SettingsRoute.fullPath}
            className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "text-primary/80")}
          >
            Settings
          </Link>
        </div>
        {isAdmin ? (
          <div
            className={cn(
              "flex h-12 items-center border-b-2",
              isUsersPath ? "border-primary" : "border-transparent",
            )}
          >
            <Link
              to="/dashboard/users"
              className={cn(
                buttonVariants({ variant: "ghost", size: "sm" }),
                "text-primary/80 gap-1.5",
              )}
            >
              <Users className="h-4 w-4" />
              Users
            </Link>
          </div>
        ) : null}
        {isAdmin ? (
          <div
            className={cn(
              "flex h-12 items-center border-b-2",
              isAuditPath ? "border-primary" : "border-transparent",
            )}
          >
            <Link
              to="/dashboard/audit"
              className={cn(
                buttonVariants({ variant: "ghost", size: "sm" }),
                "text-primary/80 gap-1.5",
              )}
            >
              <ScrollText className="h-4 w-4" />
              Audit
            </Link>
          </div>
        ) : null}
        <div
          className={cn(
            "flex h-12 items-center border-b-2",
            isMonitorsPath ? "border-primary" : "border-transparent",
          )}
        >
          <Link
            to="/dashboard/monitors"
            className={cn(
              buttonVariants({ variant: "ghost", size: "sm" }),
              "text-primary/80 gap-1.5",
            )}
          >
            <Timer className="h-4 w-4" />
            Monitors
          </Link>
        </div>
        <div
          className={cn(
            "flex h-12 items-center border-b-2",
            isProjectsPath ? "border-primary" : "border-transparent",
          )}
        >
          <Link
            to="/dashboard/projects"
            className={cn(
              buttonVariants({ variant: "ghost", size: "sm" }),
              "text-primary/80 gap-1.5",
            )}
          >
            <Layers className="h-4 w-4" />
            Projects
          </Link>
        </div>
      </div>
    </nav>
  );
}