import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { Route as DashboardRoute } from "@/routes/_app/_auth/dashboard/_layout.index";

export const Route = createFileRoute("/_app/_auth/onboarding/_layout/username")(
  {
    component: OnboardingUsername,
    beforeLoad: () => ({
      title: `Username`,
    }),
  },
);

export default function OnboardingUsername() {
  const navigate = useNavigate();

  useEffect(() => {
    navigate({ to: DashboardRoute.fullPath });
  }, [navigate]);

  return null;
}
