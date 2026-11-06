"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 15_000, refetchOnWindowFocus: true, retry: (n, err) => n < 2 && !isClientError(err) },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function isClientError(err: unknown): boolean {
  const status = (err as { status?: number })?.status;
  return typeof status === "number" && status >= 400 && status < 500;
}
