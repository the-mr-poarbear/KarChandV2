// ClientLayout.tsx
"use client";

import Navbar from "@/components/Navbar/Navbar";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { useState } from "react";

interface ClientLayoutProps {
  children: React.ReactNode;
}

const ClientLayout = ({ children }: ClientLayoutProps) => {
  const [queryClient] = useState(
    () => new QueryClient()
  );

  return (
    <QueryClientProvider client={queryClient}>
      <body className="min-h-full flex flex-col px-10">
        <Navbar />
        {children}
      </body>
    </QueryClientProvider>
  );
};

export default ClientLayout;