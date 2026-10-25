import Image from "next/image";
import { PHOTOS, type PhotoName } from "@/lib/photos";

/** A photograph from the list, with its description. `fill` makes it cover its (positioned) parent. */
export function Photo({ name, sizes, className, priority = false, fill = false }: {
  name: PhotoName;
  sizes: string;
  className?: string;
  priority?: boolean;
  fill?: boolean;
}) {
  const p = PHOTOS[name];
  return fill ? (
    <Image src={p.src} alt={p.alt} fill sizes={sizes} className={className} priority={priority} />
  ) : (
    <Image src={p.src} alt={p.alt} width={p.width} height={p.height} sizes={sizes} className={className} priority={priority} />
  );
}
