import { useNavigate, useRouter } from "@tanstack/react-router";
import { useAuth } from "@/hooks/useAuth";
import type { ClassValue } from "clsx";
import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Tailwind CSS classnames with support for conditional classes.
 * Widely used for Radix components.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Returns a function that calls all of its arguments.
 */
export function callAll<Args extends unknown[]>(
  ...fns: (((...args: Args) => unknown) | undefined)[]
) {
  return (...args: Args) => fns.forEach((fn) => fn?.(...args));
}

export const useSignOut = () => {
  const router = useRouter();
  const navigate = useNavigate();
  const { logout } = useAuth();

  return async () => {
    logout();
    router.invalidate();
    navigate({ to: "/login" });
  };
};
