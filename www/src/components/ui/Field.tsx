import { forwardRef, useId } from "react";
import { cn } from "@/lib/format";

const control =
  "w-full rounded-control border border-control bg-sheet px-3 text-body text-ink placeholder:text-faint " +
  "focus:border-accent focus:outline-none focus-visible:outline-none focus:ring-2 focus:ring-[var(--accent-soft)] " +
  "disabled:opacity-60";

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...props },
  ref,
) {
  return <input ref={ref} className={cn(control, "h-9 pointer-coarse:h-11", className)} {...props} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...props }, ref) {
    return <textarea ref={ref} className={cn(control, "min-h-20 py-2", className)} {...props} />;
  },
);

/** Label above the control, optional help below. */
export function Field({
  label,
  help,
  error,
  children,
}: {
  label: string;
  help?: string;
  error?: string;
  children: (id: string) => React.ReactNode;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="text-body font-medium text-ink">
        {label}
      </label>
      {children(id)}
      {error ? (
        <p className="text-meta text-negative">{error}</p>
      ) : help ? (
        <p className="text-meta text-muted">{help}</p>
      ) : null}
    </div>
  );
}
