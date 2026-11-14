import { Slot } from "@radix-ui/react-slot";
import { forwardRef } from "react";
import { cn } from "@/lib/format";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const base =
  "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-control font-medium " +
  "transition-colors disabled:cursor-not-allowed disabled:opacity-60 [&_svg]:size-4 [&_svg]:shrink-0";

const variants: Record<Variant, string> = {
  primary: "bg-primary text-primary-ink hover:opacity-90 active:opacity-80",
  secondary: "border border-control bg-sheet text-ink hover:bg-raised active:bg-active",
  ghost: "text-ink hover:bg-raised active:bg-active",
  danger: "border border-control bg-sheet text-negative hover:bg-raised active:bg-active",
};

// Touch screens get 40px controls; pointer screens keep the 32px app density.
const sizes: Record<Size, string> = {
  sm: "h-7 px-2.5 text-meta pointer-coarse:h-9",
  md: "h-8 px-3 text-body pointer-coarse:h-10",
};

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  asChild?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", asChild, className, type, ...props },
  ref,
) {
  const Comp = asChild ? Slot : "button";
  return (
    <Comp
      ref={ref}
      type={asChild ? undefined : (type ?? "button")}
      className={cn(base, variants[variant], sizes[size], className)}
      {...props}
    />
  );
});

/** A square icon-only button. Always give it an aria-label. */
export const IconButton = forwardRef<HTMLButtonElement, Omit<ButtonProps, "size"> & { "aria-label": string }>(
  function IconButton({ variant = "ghost", className, ...props }, ref) {
    return (
      <Button
        ref={ref}
        variant={variant}
        className={cn("size-8 px-0 text-muted hover:text-ink pointer-coarse:size-10", className)}
        {...props}
      />
    );
  },
);
