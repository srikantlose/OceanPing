"use client";

import dynamic from "next/dynamic";

const AnalystDashboard = dynamic(() => import("@/components/AnalystDashboard"), {
  ssr: false,
});

export default function AnalystPage() {
  return <AnalystDashboard />;
}
