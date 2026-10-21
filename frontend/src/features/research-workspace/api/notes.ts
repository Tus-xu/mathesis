/**
 * 笔记 API（BACKEND-CONTRACT §3.2）。
 * 笔记带 locator（paper/page/section/selected_text），点击必须能回到阅读位置（宪法 §11）。
 */
import { rwDel, rwGet, rwPatch, rwPost } from './client'
import type { Note, NoteInput } from './types'

export async function listNotes(projectId: string): Promise<Note[]> {
  const r = await rwGet<{ items: Note[] }>(`/projects/${projectId}/notes`)
  return r?.items ?? []
}

export async function recentNotes(projectId: string, limit = 6): Promise<Note[]> {
  const r = await rwGet<{ items: Note[] }>(`/projects/${projectId}/notes/recent`, { limit })
  return r?.items ?? []
}

export async function createNote(projectId: string, input: NoteInput): Promise<Note> {
  const r = await rwPost<{ note: Note }>(`/projects/${projectId}/notes`, input)
  return r.note
}

export async function patchNote(noteId: string, body: Partial<NoteInput>): Promise<Note> {
  const r = await rwPatch<{ note: Note }>(`/notes/${noteId}`, body)
  return r.note
}

export async function deleteNote(noteId: string): Promise<void> {
  await rwDel<void>(`/notes/${noteId}`)
}
