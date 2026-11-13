// How the landing page raises its voice. Three devices, used for things the product
// measured and for the few words a section turns on:
//
//   Count   a number that counts up to its value the first time it is seen
//   Pen     two strokes under a number or a phrase, drawn as if by hand
//   Key     a phrase inside a sentence, set in the display face
//
// All three stand still for a visitor who prefers reduced motion, and a screen
// reader hears the finished number, never the counting.

import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

const still = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

/** True from the first time the element is on screen, and from then on. */
export function useSeen<T extends Element>(threshold = 0.5): [RefObject<T | null>, boolean] {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    if (!("IntersectionObserver" in window)) {
      setSeen(true);
      return;
    }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setSeen(true);
        observer.disconnect();
      }
    }, { threshold });
    observer.observe(el);
    return () => observer.disconnect();
  }, [seen, threshold]);
  return [ref, seen];
}

const DURATION = 1100;

export function Count({ to, digits = 0, suffix = "" }: { to: number; digits?: number; suffix?: string }) {
  const [ref, seen] = useSeen<HTMLSpanElement>(0.6);
  const [shown, setShown] = useState(() => (still() ? to : 0));
  useEffect(() => {
    if (!seen || still()) return;
    let frame = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / DURATION);
      setShown(to * (1 - Math.pow(1 - p, 4))); // fast at first, settling on the value
      if (p < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    const settle = setTimeout(() => setShown(to), DURATION + 600); // frames can be withheld (a background tab); the value still arrives
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(settle);
    };
  }, [seen, to]);
  const format = new Intl.NumberFormat("en", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return (
    <span ref={ref} className="count">
      <span className="sr">{format.format(to)}{suffix}</span>
      <span aria-hidden="true">{format.format(shown)}{suffix}</span>
    </span>
  );
}

export function Pen({ children }: { children: ReactNode }) {
  const [ref, seen] = useSeen<HTMLSpanElement>(0.9);
  return (
    <span ref={ref} className={`pen ${seen ? "seen" : ""}`}>
      {children}
      <svg viewBox="0 0 100 12" preserveAspectRatio="none" aria-hidden="true">
        <path pathLength={1} d="M1 3.6C22 1.3 49 5.7 99 2.9" />
        <path pathLength={1} d="M5 9.3C31 7.1 63 10.9 95 8.3" />
      </svg>
    </span>
  );
}

export function Key({ children }: { children: ReactNode }) {
  return <em className="key">{children}</em>;
}

/** A measured number, loud, beside what it counts. */
export function Stat({ children, label }: { children: ReactNode; label: ReactNode }) {
  return (
    <p className="stat">
      <span className="stat-n">{children}</span>
      <span className="stat-l">{label}</span>
    </p>
  );
}
