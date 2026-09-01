import {
  BookOpen,
  Briefcase,
  CircleDashed,
  Coffee,
  Dumbbell,
  Flag,
  HeartPulse,
  Home,
  MessageCircle,
  Moon,
  Palette,
  Plane,
  Play,
  ShoppingBag,
  Smartphone,
  Sparkles,
  User,
  UtensilsCrossed,
  Wallet,
  type LucideIcon,
} from 'lucide-react'

/**
 * Categories store an icon key rather than a component, so the value survives
 * export, import and any future sync without carrying code with it.
 */
export const CATEGORY_ICONS: Record<string, LucideIcon> = {
  briefcase: Briefcase,
  user: User,
  'heart-pulse': HeartPulse,
  dumbbell: Dumbbell,
  'book-open': BookOpen,
  wallet: Wallet,
  'shopping-bag': ShoppingBag,
  home: Home,
  'message-circle': MessageCircle,
  play: Play,
  plane: Plane,
  flag: Flag,
  utensils: UtensilsCrossed,
  smartphone: Smartphone,
  coffee: Coffee,
  moon: Moon,
  sparkles: Sparkles,
  palette: Palette,
  'circle-dashed': CircleDashed,
}

export const ICON_KEYS = Object.keys(CATEGORY_ICONS)

/**
 * Renders a category's icon from its stored key. This is a component rather
 * than a `getIcon()` helper so no component value is produced by a call during
 * render, which the React Compiler rightly refuses to memoise.
 */
export function CategoryIcon({
  icon,
  className,
  strokeWidth = 2,
}: {
  icon: string | undefined | null
  className?: string
  strokeWidth?: number
}) {
  const Icon = (icon ? CATEGORY_ICONS[icon] : undefined) ?? CircleDashed
  return <Icon className={className} strokeWidth={strokeWidth} aria-hidden="true" />
}
