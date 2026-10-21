/**
 * 笔记 store（FRONTEND-ARCH §5）。
 * list / recent；load / loadRecent / create / update / remove。
 * 笔记带 locator，供阅读器回跳（宪法 §11/§44）。
 */
import { ref } from 'vue'
import { defineStore } from 'pinia'
import { toRvError, type RvError } from '../api/client'
import { createNote, deleteNote, listNotes, patchNote, recentNotes } from '../api/notes'
import type { Note, NoteInput } from '../api/types'

export const useRwNotesStore = defineStore('rw.notes', () => {
  const list = ref<Note[]>([])
  const recent = ref<Note[]>([])
  const loading = ref(false)
  const error = ref<RvError | null>(null)

  async function load(projectId: string): Promise<Note[]> {
    loading.value = true
    error.value = null
    try {
      list.value = await listNotes(projectId)
      return list.value
    } catch (e) {
      list.value = []
      error.value = toRvError(e)
      return []
    } finally {
      loading.value = false
    }
  }

  async function loadRecent(projectId: string, limit = 6): Promise<Note[]> {
    try {
      recent.value = await recentNotes(projectId, limit)
      return recent.value
    } catch (e) {
      recent.value = []
      error.value = toRvError(e)
      return []
    }
  }

  async function create(projectId: string, input: NoteInput): Promise<Note | null> {
    error.value = null
    try {
      const note = await createNote(projectId, input)
      list.value = [note, ...list.value]
      recent.value = [note, ...recent.value].slice(0, 6)
      return note
    } catch (e) {
      error.value = toRvError(e)
      return null
    }
  }

  async function update(noteId: string, body: Partial<NoteInput>): Promise<Note | null> {
    error.value = null
    try {
      const note = await patchNote(noteId, body)
      list.value = list.value.map((n) => (n.id === noteId ? note : n))
      recent.value = recent.value.map((n) => (n.id === noteId ? note : n))
      return note
    } catch (e) {
      error.value = toRvError(e)
      return null
    }
  }

  async function remove(noteId: string): Promise<boolean> {
    error.value = null
    try {
      await deleteNote(noteId)
      list.value = list.value.filter((n) => n.id !== noteId)
      recent.value = recent.value.filter((n) => n.id !== noteId)
      return true
    } catch (e) {
      error.value = toRvError(e)
      return false
    }
  }

  function reset() {
    list.value = []
    recent.value = []
    error.value = null
  }

  return { list, recent, loading, error, load, loadRecent, create, update, remove, reset }
})
