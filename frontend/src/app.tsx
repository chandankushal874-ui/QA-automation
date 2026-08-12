import { RouterProvider } from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { router } from "@/router";
import { AuthProvider } from "@/hooks/useAuth";
import "@/i18n";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

function InnerApp() {
  return <RouterProvider router={router} context={{ queryClient }} />;
}

const helmetContext = {};

export default function App() {
  return (
    <HelmetProvider context={helmetContext}>
      <AuthProvider>
        <QueryClientProvider client={queryClient}>
          <InnerApp />
        </QueryClientProvider>
      </AuthProvider>
    </HelmetProvider>
  );
}
