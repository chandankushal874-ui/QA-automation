import { createFileRoute, Outlet } from "@tanstack/react-router";
import { Navigation } from "./-ui.navigation";
import { Header } from "@/ui/header";
import { useQuery } from "@tanstack/react-query";
import { getMe } from "@/lib/api";

export const Route = createFileRoute("/_app/_auth/dashboard/_layout")({
  component: DashboardLayout,
});

function DashboardLayout() {
  const { data: user } = useQuery({
    queryKey: ["me"],
    queryFn: getMe,
  });
  if (!user) {
    return null;
  }
  return (
    <div className="flex min-h-[100vh] w-full flex-col bg-secondary dark:bg-black">
      <Navigation user={user} />
      <Header />
      <Outlet />
    </div>
  );
}
