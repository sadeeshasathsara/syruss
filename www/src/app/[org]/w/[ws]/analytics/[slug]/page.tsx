"use client";

import { useParams } from "next/navigation";
import { ScreenView } from "@/components/screens/ScreenView";

export default function ScreenPage() {
  const { slug } = useParams<{ slug: string }>();
  return <ScreenView slug={slug} />;
}
