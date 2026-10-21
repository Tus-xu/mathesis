/**
 * 草稿文件树 API（BACKEND-CONTRACT §3.3）。
 *
 * 实测后端信封（2026-09-16，经 8000 网关）：
 * - `GET  /projects/{id}/files`            → `{ tree: FileNode[], items: FileMeta[] }`
 * - `GET  /projects/{id}/files/{path}`     → `{ file: { …, content, revision, saved_at } }`
 * - `PUT  /projects/{id}/files/{path}`     → `{ file: { …, revision, saved_at } }`（`expected_revision` 不匹配 → 409）
 * - `POST /projects/{id}/files`            → `{ file: {...} }`（201）
 * - `GET  /projects/{id}/outline`          → `{ source_file, items: OutlineItem[], tree, revision }`
 *
 * 本层负责**解包信封**，对上层（store）保持稳定形状；`PUT` 带 `expected_revision` 做乐观并发。
 */
import { rwDel, rwGet, rwPost, rwPut } from './client'
import type { FileContent, FileKind, FileNode, OutlineItem } from './types'

/** 后端扁平文件元信息（`file_public_dict`）。 */
interface FileMeta {
  id?: string
  path: string
  kind: FileKind
  revision: number
  size?: number
  saved_at?: string | null
  updated_at?: string | null
  content?: string | null
}

/** 由扁平元信息重建最小树（后端未给 `tree` 时的降级，禁止造假数据）。 */
function treeFromItems(items: FileMeta[]): FileNode[] {
  const root: FileNode = { name: '', path: '', type: 'dir', kind: 'dir', children: [] }
  for (const it of items) {
    const parts = String(it.path || '').split('/').filter(Boolean)
    let cur = root
    parts.forEach((seg, i) => {
      const isLeaf = i === parts.length - 1
      const acc = parts.slice(0, i + 1).join('/')
      cur.children = cur.children ?? []
      let next = cur.children.find((c) => c.name === seg)
      if (!next) {
        next = isLeaf
          ? { name: seg, path: acc, type: 'file', kind: it.kind, revision: it.revision, size: it.size, children: [] }
          : { name: seg, path: acc, type: 'dir', kind: 'dir', children: [] }
        cur.children.push(next)
      }
      cur = next
    })
  }
  const sortNodes = (nodes: FileNode[]): FileNode[] => {
    nodes.sort((a, b) => (a.type !== 'dir' ? 1 : 0) - (b.type !== 'dir' ? 1 : 0) || a.name.localeCompare(b.name))
    for (const n of nodes) if (n.children) n.children = sortNodes(n.children)
    return nodes
  }
  return sortNodes(root.children ?? [])
}

export async function getTree(projectId: string): Promise<FileNode[]> {
  const r = await rwGet<{ tree?: FileNode[]; items?: FileMeta[] }>(`/projects/${projectId}/files`)
  if (Array.isArray(r?.tree) && r.tree.length) return r.tree
  return treeFromItems(r?.items ?? [])
}

export async function getFile(projectId: string, path: string): Promise<FileContent> {
  const r = await rwGet<{ file: FileMeta }>(`/projects/${projectId}/files/${encodePath(path)}`)
  const f = r?.file
  return {
    path: f?.path ?? path,
    content: f?.content ?? '',
    revision: Number(f?.revision ?? 0),
    saved_at: f?.saved_at ?? f?.updated_at ?? '',
  }
}

/** 保存（自动保存语义）：带 expected_revision，冲突返回 409。 */
export async function putFile(
  projectId: string,
  path: string,
  content: string,
  expectedRevision: number,
): Promise<FileContent> {
  const r = await rwPut<{ file: FileMeta }>(`/projects/${projectId}/files/${encodePath(path)}`, {
    content,
    expected_revision: expectedRevision,
  })
  const f = r?.file
  return {
    path: f?.path ?? path,
    content,
    revision: Number(f?.revision ?? expectedRevision + 1),
    saved_at: f?.saved_at ?? f?.updated_at ?? '',
  }
}

export async function createFile(
  projectId: string,
  path: string,
  kind: FileKind = 'tex',
  content = '',
): Promise<FileContent> {
  const r = await rwPost<{ file: FileMeta }>(`/projects/${projectId}/files`, { path, kind, content })
  const f = r?.file
  return {
    path: f?.path ?? path,
    content,
    revision: Number(f?.revision ?? 1),
    saved_at: f?.saved_at ?? f?.updated_at ?? '',
  }
}

export async function deleteFile(projectId: string, path: string): Promise<void> {
  await rwDel<void>(`/projects/${projectId}/files/${encodePath(path)}`)
}

export async function renameFile(projectId: string, from: string, to: string): Promise<void> {
  await rwPost<void>(`/projects/${projectId}/files:rename`, { from, to })
}

export async function getOutline(projectId: string, entry = 'main.tex'): Promise<OutlineItem[]> {
  const r = await rwGet<{ source_file?: string; items?: OutlineItem[] }>(
    `/projects/${projectId}/outline`,
    { entry },
  )
  const src = r?.source_file ?? entry
  return (r?.items ?? []).map((it) => ({ ...it, file: it.file || src }))
}

/** 路径可能含 `/`，逐段编码但保留分隔符。 */
function encodePath(path: string): string {
  return path
    .split('/')
    .map((seg) => encodeURIComponent(seg))
    .join('/')
}
