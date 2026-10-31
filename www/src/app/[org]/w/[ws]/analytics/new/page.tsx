"use client";

import { Suspense } from "react";
import { ScreenBuilder } from "@/components/screens/ScreenBuilder";

export default function NewScreen() {
  return (
    <Suspense>
      <ScreenBuilder />
    </Suspense>
  );
}
