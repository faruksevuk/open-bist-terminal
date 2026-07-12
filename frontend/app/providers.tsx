"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        // Varsayılan POLLING YOK — yalnız canlı veri sorguları (portföy/fırsat/skor/detay)
        // kendi refetchInterval'ini açar. Böylece AI/grafik/sparkline/config gibi pahalı ya da
        // statik sorgular 30sn'de bir gereksiz yere (token/ağ) tekrarlanmaz.
        defaultOptions: {
          queries: { refetchInterval: false, staleTime: 15_000, refetchOnWindowFocus: false },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
