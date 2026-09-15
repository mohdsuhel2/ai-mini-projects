import type { LucideIcon, LucideProps } from 'lucide-react'
import {
  Archive,
  ArrowDownLeft,
  Bell,
  Bold,
  CalendarCheck,
  CalendarDays,
  ChartNoAxesColumn,
  Check,
  ChevronLeft,
  ChevronRight,
  Clock,
  Clock3,
  Copy,
  CornerUpRight,
  Download,
  Ellipsis,
  Eraser,
  FilePlus,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  GripVertical,
  Heading1,
  Heading2,
  Home,
  Hourglass,
  Italic,
  LayoutList,
  List,
  ListOrdered,
  ListTodo,
  Lock,
  Monitor,
  Moon,
  NotebookPen,
  Pause,
  PenLine,
  Pin,
  Play,
  Plus,
  Quote,
  Repeat,
  Search,
  Settings2,
  Sparkles,
  Strikethrough,
  Sun,
  Tag,
  Timer,
  Trash2,
  Upload,
  WifiOff,
  X,
} from 'lucide-react'
import { cn } from '@/lib/utils/cn'

/** Default stroke for UI icons — thin, round, line-based. */
export const ICON_STROKE = 1.75

/** Slightly heavier stroke for active states and small marks. */
export const ICON_STROKE_STRONG = 2

export const iconSizes = {
  xs: 12,
  sm: 14,
  md: 16,
  lg: 18,
  nav: 19,
  xl: 20,
} as const

export type IconSize = keyof typeof iconSizes | number

export interface AppIconProps extends Omit<LucideProps, 'size' | 'ref'> {
  icon: LucideIcon
  size?: IconSize
}

export function AppIcon({
  icon: Icon,
  size = 'md',
  className,
  strokeWidth = ICON_STROKE,
  ...props
}: AppIconProps) {
  const dimension = typeof size === 'number' ? size : iconSizes[size]
  return (
    <Icon
      width={dimension}
      height={dimension}
      className={cn('shrink-0', className)}
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={props['aria-hidden'] ?? true}
      {...props}
    />
  )
}

function lineIcon(Icon: LucideIcon) {
  function LineIcon({ size = 'md', strokeWidth = ICON_STROKE, ...props }: Omit<AppIconProps, 'icon'>) {
    return <AppIcon icon={Icon} size={size} strokeWidth={strokeWidth} {...props} />
  }
  LineIcon.displayName = Icon.displayName ?? 'LineIcon'
  return LineIcon
}

/** Pre-bound line icons — import these instead of lucide-react directly. */
export const ArchiveIcon = lineIcon(Archive)
export const ArrowDownLeftIcon = lineIcon(ArrowDownLeft)
export const BellIcon = lineIcon(Bell)
export const BoldIcon = lineIcon(Bold)
export const CalendarCheckIcon = lineIcon(CalendarCheck)
export const CalendarDaysIcon = lineIcon(CalendarDays)
export const ChartNoAxesColumnIcon = lineIcon(ChartNoAxesColumn)
export const CheckIcon = lineIcon(Check)
export const ChevronLeftIcon = lineIcon(ChevronLeft)
export const ChevronRightIcon = lineIcon(ChevronRight)
export const ClockIcon = lineIcon(Clock)
export const Clock3Icon = lineIcon(Clock3)
export const CopyIcon = lineIcon(Copy)
export const CornerUpRightIcon = lineIcon(CornerUpRight)
export const DownloadIcon = lineIcon(Download)
export const EllipsisIcon = lineIcon(Ellipsis)
export const EraserIcon = lineIcon(Eraser)
export const FilePlusIcon = lineIcon(FilePlus)
export const FileTextIcon = lineIcon(FileText)
export const FolderIcon = lineIcon(Folder)
export const FolderOpenIcon = lineIcon(FolderOpen)
export const FolderPlusIcon = lineIcon(FolderPlus)
export const GripVerticalIcon = lineIcon(GripVertical)
export const Heading1Icon = lineIcon(Heading1)
export const Heading2Icon = lineIcon(Heading2)
export const HomeIcon = lineIcon(Home)
export const HourglassIcon = lineIcon(Hourglass)
export const ItalicIcon = lineIcon(Italic)
export const LayoutListIcon = lineIcon(LayoutList)
export const ListIcon = lineIcon(List)
export const ListOrderedIcon = lineIcon(ListOrdered)
export const ListTodoIcon = lineIcon(ListTodo)
export const LockIcon = lineIcon(Lock)
export const MonitorIcon = lineIcon(Monitor)
export const MoonIcon = lineIcon(Moon)
export const NotebookPenIcon = lineIcon(NotebookPen)
export const PauseIcon = lineIcon(Pause)
export const PenLineIcon = lineIcon(PenLine)
export const PinIcon = lineIcon(Pin)
export const PlayIcon = lineIcon(Play)
export const PlusIcon = lineIcon(Plus)
export const QuoteIcon = lineIcon(Quote)
export const RepeatIcon = lineIcon(Repeat)
export const SearchIcon = lineIcon(Search)
export const Settings2Icon = lineIcon(Settings2)
export const SparklesIcon = lineIcon(Sparkles)
export const StrikethroughIcon = lineIcon(Strikethrough)
export const SunIcon = lineIcon(Sun)
export const TagIcon = lineIcon(Tag)
export const TimerIcon = lineIcon(Timer)
export const Trash2Icon = lineIcon(Trash2)
export const UploadIcon = lineIcon(Upload)
export const WifiOffIcon = lineIcon(WifiOff)
export const XIcon = lineIcon(X)

/** Surface navigation — Tasks, Insights, Notes. */
export const surfaceNavIcons = {
  day: ListTodoIcon,
  insights: ChartNoAxesColumnIcon,
  notes: NotebookPenIcon,
} as const
