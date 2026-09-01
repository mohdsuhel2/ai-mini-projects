'use client'

import { useRef, useState } from 'react'
import { Download, Trash2, Upload } from 'lucide-react'
import { Button } from '@/components/common/button'
import {
  BackupError,
  backupFilename,
  clearAllData,
  exportBackup,
  importBackup,
  parseBackup,
} from '@/lib/db/backup'
import { track } from '@/lib/analytics'
import { useUi } from '@/store/ui-context'

export function DataSection() {
  const fileInput = useRef<HTMLInputElement>(null)
  const { notify } = useUi()
  const [error, setError] = useState<string | null>(null)
  const [confirmingClear, setConfirmingClear] = useState(false)

  async function handleExport() {
    const backup = await exportBackup()
    const blob = new Blob([JSON.stringify(backup, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = backupFilename()
    anchor.click()
    URL.revokeObjectURL(url)
    track('data_exported', {
      todos: backup.counts.todos ?? 0,
      activities: backup.counts.activities ?? 0,
    })
    notify('Backup downloaded')
  }

  async function handleImport(file: File) {
    setError(null)
    try {
      const backup = parseBackup(await file.text())
      const result = await importBackup(backup, 'merge')
      track('data_imported', { todos: result.todos, activities: result.activities })
      notify(`Imported ${result.todos} tasks and ${result.activities} activities`)
    } catch (cause) {
      setError(cause instanceof BackupError ? cause.message : 'That import could not be completed.')
    }
  }

  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-[13px] font-semibold text-fg">Your data</h3>
        <p className="mt-0.5 text-[12.5px] leading-relaxed text-fg-muted">
          Everything lives in this browser on this device. Export a copy before clearing your
          browser data, or to move to another machine.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={handleExport}>
          <Download className="size-3.5" strokeWidth={2} aria-hidden="true" />
          Export JSON
        </Button>

        <Button size="sm" onClick={() => fileInput.current?.click()}>
          <Upload className="size-3.5" strokeWidth={2} aria-hidden="true" />
          Import
        </Button>
        <input
          ref={fileInput}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) void handleImport(file)
            event.target.value = ''
          }}
        />

        {confirmingClear ? (
          <span className="inline-flex items-center gap-2 rounded-md bg-danger-soft px-2 py-1">
            <span className="text-[12.5px] text-danger">Delete everything?</span>
            <Button
              size="sm"
              variant="danger"
              className="h-6 px-2 text-[12px]"
              onClick={async () => {
                await clearAllData()
                track('data_cleared')
                setConfirmingClear(false)
                notify('All data cleared')
              }}
            >
              Yes, delete
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-[12px]"
              onClick={() => setConfirmingClear(false)}
            >
              Cancel
            </Button>
          </span>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="text-danger hover:bg-danger-soft hover:text-danger"
            onClick={() => setConfirmingClear(true)}
          >
            <Trash2 className="size-3.5" strokeWidth={2} aria-hidden="true" />
            Clear all data
          </Button>
        )}
      </div>

      {error && (
        <p role="alert" className="text-[12.5px] text-danger">
          {error}
        </p>
      )}
      <p className="text-[12px] text-fg-faint">
        Import merges with what is already here, keeping whichever copy of a record was edited most
        recently.
      </p>
    </section>
  )
}
