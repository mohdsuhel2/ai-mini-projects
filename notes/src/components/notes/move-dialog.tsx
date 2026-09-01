'use client'

import { Check, Folder as FolderIcon, Home } from 'lucide-react'
import { Dialog } from '@/components/common/dialog'
import { buildFolderTree, canMoveFolder, flattenTree } from '@/features/notes/tree'
import { cn } from '@/lib/utils/cn'
import type { Folder, Id } from '@/types'

interface MoveDialogProps {
  open: boolean
  onClose: () => void
  folders: Folder[]
  /** The folder being moved, when moving a folder rather than a note. */
  movingFolderId?: Id | null
  currentParentId: Id | null
  title: string
  onMove: (targetParentId: Id | null) => void
}

/**
 * A picker rather than drag-and-drop: it works on a phone, it works from the
 * keyboard, and destinations that would create a cycle are visibly disabled
 * instead of silently rejected on drop.
 */
export function MoveDialog({
  open,
  onClose,
  folders,
  movingFolderId,
  currentParentId,
  title,
  onMove,
}: MoveDialogProps) {
  const flat = flattenTree(buildFolderTree(folders, []))

  return (
    <Dialog open={open} onClose={onClose} title={title} description="Choose a destination.">
      <div className="max-h-[24rem] flex-1 overflow-y-auto p-2">
        <DestinationRow
          label="Unfiled"
          icon={<Home className="size-3.5 text-fg-subtle" strokeWidth={2} aria-hidden="true" />}
          depth={0}
          selected={currentParentId === null}
          disabled={false}
          onClick={() => {
            onMove(null)
            onClose()
          }}
        />

        {flat.map((node) => {
          const blocked =
            movingFolderId != null && !canMoveFolder(folders, movingFolderId, node.folder.id)
          return (
            <DestinationRow
              key={node.folder.id}
              label={node.folder.name}
              icon={
                <FolderIcon className="size-3.5 text-fg-subtle" strokeWidth={2} aria-hidden="true" />
              }
              depth={node.depth + 1}
              selected={currentParentId === node.folder.id}
              disabled={blocked}
              hint={blocked ? 'Inside itself' : undefined}
              onClick={() => {
                onMove(node.folder.id)
                onClose()
              }}
            />
          )
        })}
      </div>
    </Dialog>
  )
}

function DestinationRow({
  label,
  icon,
  depth,
  selected,
  disabled,
  hint,
  onClick,
}: {
  label: string
  icon: React.ReactNode
  depth: number
  selected: boolean
  disabled: boolean
  hint?: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      style={{ paddingLeft: 8 + Math.min(depth, 6) * 14 }}
      className={cn(
        'flex w-full items-center gap-2 rounded-md py-2 pr-2 text-left text-[13px] transition-colors',
        disabled
          ? 'cursor-not-allowed text-fg-faint'
          : selected
            ? 'bg-accent-soft text-accent'
            : 'text-fg hover:bg-surface-hover',
      )}
    >
      {icon}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {hint && <span className="shrink-0 text-[11px] text-fg-faint">{hint}</span>}
      {selected && !disabled && <Check className="size-3.5 shrink-0" strokeWidth={2.4} />}
    </button>
  )
}
